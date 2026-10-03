// CPU AXI peripheral, independent of Coral source. Single-beat AXI accesses.
// E1-fetch: bounded, in-order storage reads; the native PQ2 arithmetic is unchanged.
// Storage addresses are byte offsets and each request returns one aligned 128-bit beat.
// Responses must remain valid while !storage_rsp_ready. Reset also resets storage.
module coral_weight_axi #(
    parameter int DATA_BITS = 128,
    ID_BITS = 6,
    parameter bit ENGINE_ENABLE = 1,
    parameter logic [31:0] ROM_BASE = 32'h40000000,
    parameter logic [31:0] MMIO_BASE = 32'h60000000,
    parameter int unsigned ROM_BYTES = 463290464,
    parameter int unsigned FETCH_DEPTH = 4
) (
    input logic clk,
    reset,
    input logic [31:0] s_araddr,
    input logic [ID_BITS-1:0] s_arid,
    input logic [7:0] s_arlen,
    input logic [2:0] s_arsize,
    input logic s_arvalid,
    output logic s_arready,
    output logic [DATA_BITS-1:0] s_rdata,
    output logic [ID_BITS-1:0] s_rid,
    output logic [1:0] s_rresp,
    output logic s_rlast,
    s_rvalid,
    input logic s_rready,
    input logic [31:0] s_awaddr,
    input logic [ID_BITS-1:0] s_awid,
    input logic [7:0] s_awlen,
    input logic [2:0] s_awsize,
    input logic s_awvalid,
    output logic s_awready,
    input logic [DATA_BITS-1:0] s_wdata,
    input logic [DATA_BITS/8-1:0] s_wstrb,
    input logic s_wlast,
    s_wvalid,
    output logic s_wready,
    output logic [ID_BITS-1:0] s_bid,
    output logic [1:0] s_bresp,
    output logic s_bvalid,
    input logic s_bready,
    output logic storage_valid,
    input logic storage_ready,
    output logic [31:0] storage_addr,
    input logic storage_rsp_valid,
    storage_rsp_error,
    input logic [DATA_BITS-1:0] storage_rsp_data,
    output logic storage_rsp_ready
);
  localparam int BYTES = DATA_BITS / 8;
  localparam int PTR_BITS = FETCH_DEPTH > 1 ? $clog2(FETCH_DEPTH) : 1;
  localparam int COUNT_BITS = $clog2(FETCH_DEPTH + 1);
  typedef enum logic [2:0] {
    IDLE,
    CPU_REQ,
    CPU_WAIT,
    ENG_FETCH,
    ENG_DRAIN,
    ENG_MAC
  } state_t;
  // The packed data staging is identical to E1. Each queue entry adds only
  // destination unit/byte and source offset/length, never a second data beat.
  typedef struct packed {
    logic [4:0] unit_index;
    logic [5:0] byte_index;
    logic [3:0] beat_offset;
    logic [4:0] copy_bytes;
  } fetch_desc_t;
  fetch_desc_t fetch_desc[FETCH_DEPTH];
  logic [PTR_BITS-1:0] fetch_head, fetch_tail;
  logic [COUNT_BITS-1:0] fetch_outstanding;
  logic fetch_more, eng_req_valid;
  logic eng_req_fire, eng_rsp_fire;
  logic [31:0] fetch_addr;
  integer fetch_offset, fetch_copy;
  state_t state;
  logic aw_hold, w_hold;
  logic [31:0] aw_addr;
  logic [ID_BITS-1:0] aw_id;
  logic [7:0] aw_len;
  logic [2:0] aw_size;
  logic [DATA_BITS-1:0] w_data;
  logic [BYTES-1:0] w_strb;
  logic w_last;
  logic [31:0] cpu_base;
  integer fetch_unit, fetch_byte, mac_lane;
  logic busy, done, error;
  logic [31:0] cycles;
  integer unit_count;
  logic [31:0] weight_addr[32];
  logic [7:0] packed_weight[32][34];
  logic signed [7:0] activation[128];
  logic [31:0] activation_scale;
  logic signed [31:0] result[32][4];
  logic [31:0] write_offset;
  integer write_lane;
  logic [31:0] write_word;
  logic [3:0] write_mask;
  assign s_awready = !aw_hold && !s_bvalid;
  assign s_wready = !w_hold && !s_bvalid;
  assign s_arready=!s_rvalid && (state==IDLE ||
    (ENGINE_ENABLE && (state==ENG_FETCH || state==ENG_DRAIN || state==ENG_MAC) &&
     s_araddr>=MMIO_BASE && s_araddr<MMIO_BASE+32'h1000));
  assign s_rlast = 1'b1;
  // The cursor advances only on request acceptance. busy prevents MMIO writes
  // to the addresses, so storage_valid/address stay stable through a stall.
  assign fetch_addr = weight_addr[fetch_unit] + 32'(fetch_byte);
  assign fetch_offset = int'(fetch_addr % BYTES);
  assign fetch_copy=(BYTES-fetch_offset < 34-fetch_byte) ?
    BYTES-fetch_offset : 34-fetch_byte;
  assign storage_valid = state == CPU_REQ || eng_req_valid;
  assign storage_addr = state == CPU_REQ ? cpu_base : (fetch_addr / BYTES) * BYTES;
  assign storage_rsp_ready=state==CPU_WAIT ||
    ((state==ENG_FETCH || state==ENG_DRAIN) && fetch_outstanding!=0);
  assign eng_req_fire = eng_req_valid && storage_ready;
  assign eng_rsp_fire=(state==ENG_FETCH || state==ENG_DRAIN) &&
    storage_rsp_valid && storage_rsp_ready;
  assign write_offset = aw_addr - MMIO_BASE;
  assign write_lane = int'(aw_addr % BYTES);
  assign write_word = w_data[write_lane*8+:32];
  assign write_mask = w_strb[write_lane+:4];

  initial begin
    if (DATA_BITS != 128) $fatal(1, "E1-fetch requires 128-bit storage beats");
    if (FETCH_DEPTH < 1 || FETCH_DEPTH > 32) $fatal(1, "FETCH_DEPTH must be in [1,32]");
  end

  function automatic logic [PTR_BITS-1:0] next_ptr(input logic [PTR_BITS-1:0] ptr);
    if (ptr == PTR_BITS'(FETCH_DEPTH - 1)) return '0;
    return ptr + 1'b1;
  endfunction

  function automatic logic [31:0] mmio_read(input logic [31:0] off);
    if (off == 0) return {29'b0, error, done, busy};
    if (off == 8) return 32'(unit_count);
    if (off == 16) return cycles;
    if (off >= 32'h100 && off < 32'h180) return weight_addr[(off-32'h100)/4];
    if (off >= 32'h200 && off < 32'h280)
      return {
        activation[off-32'h200+3],
        activation[off-32'h200+2],
        activation[off-32'h200+1],
        activation[off-32'h200]
      };
    if (off == 32'h280) return activation_scale;
    if (off >= 32'h400 && off < 32'h600) return result[(off-32'h400)/16][((off-32'h400)%16)/4];
    if (off >= 32'h600 && off < 32'h640)
      return {
        packed_weight[(off-32'h600)/2+1][1],
        packed_weight[(off-32'h600)/2+1][0],
        packed_weight[(off-32'h600)/2][1],
        packed_weight[(off-32'h600)/2][0]
      };
    return 0;
  endfunction

  always_ff @(posedge clk) begin : sequential
    integer i, j, code, prod, beat_offset, copy_bytes, dest_byte;
    logic [4:0] dest_unit;
    logic valid_addresses;
    if (reset) begin
      state <= IDLE;
      aw_hold <= 0;
      w_hold <= 0;
      s_bvalid <= 0;
      s_rvalid <= 0;
      s_bresp <= 0;
      s_rresp <= 0;
      s_bid <= 0;
      s_rid <= 0;
      s_rdata <= 0;
      busy <= 0;
      done <= 0;
      error <= 0;
      cycles <= 0;
      unit_count <= 32;
      cpu_base <= 0;
      fetch_unit <= 0;
      fetch_byte <= 0;
      mac_lane <= 0;
      fetch_head <= '0;
      fetch_tail <= '0;
      fetch_outstanding <= '0;
      fetch_more <= 0;
      eng_req_valid <= 0;
      aw_addr <= 0;
      aw_id <= 0;
      aw_len <= 0;
      aw_size <= 0;
      w_data <= 0;
      w_strb <= 0;
      w_last <= 0;
      for (i = 0; i < 32; i++) begin
        weight_addr[i] <= 0;
        for (j = 0; j < 4; j++) result[i][j] <= 0;
        for (j = 0; j < 34; j++) packed_weight[i][j] <= 0;
      end
      for (i = 0; i < 128; i++) activation[i] <= 0;
      activation_scale <= 0;
    end else begin
      if (busy) cycles <= cycles + 1;
      if (s_bvalid && s_bready) s_bvalid <= 0;
      if (s_rvalid && s_rready) s_rvalid <= 0;
      if (s_awvalid && s_awready) begin
        aw_hold <= 1;
        aw_addr <= s_awaddr;
        aw_id   <= s_awid;
        aw_len  <= s_awlen;
        aw_size <= s_awsize;
      end
      if (s_wvalid && s_wready) begin
        w_hold <= 1;
        w_data <= s_wdata;
        w_strb <= s_wstrb;
        w_last <= s_wlast;
      end
      if (aw_hold && w_hold && !s_bvalid) begin
        aw_hold <= 0;
        w_hold <= 0;
        s_bvalid <= 1;
        s_bid <= aw_id;
        s_bresp <= 0;
        if(!ENGINE_ENABLE || aw_addr<MMIO_BASE || aw_addr>=MMIO_BASE+32'h1000 ||
           aw_len!=0 || aw_size!=2 || aw_addr[1:0]!=0 || !w_last)
          s_bresp <= 3;
        else if (busy || state != IDLE || (s_arvalid && s_arready) || write_mask != 4'hf)
          s_bresp <= 2;
        else if (write_offset == 4 && write_word == 1) begin
          valid_addresses = 1;
          for (i = 0; i < 32; i++)
          if (i < unit_count && weight_addr[i] > ROM_BYTES - 34) valid_addresses = 0;
          done  <= 0;
          error <= !valid_addresses;
          if (valid_addresses) begin
            busy <= 1;
            cycles <= 0;
            fetch_unit <= 0;
            fetch_byte <= 0;
            state <= ENG_FETCH;
            fetch_head <= '0;
            fetch_tail <= '0;
            fetch_outstanding <= '0;
            fetch_more <= 1;
            eng_req_valid <= 1;
            for (i = 0; i < 32; i++) for (j = 0; j < 4; j++) result[i][j] <= 0;
          end else s_bresp <= 2;
        end else if (write_offset == 8 && write_word >= 1 && write_word <= 32)
          unit_count <= int'(write_word);
        else if (write_offset >= 32'h100 && write_offset < 32'h180)
          weight_addr[(write_offset-32'h100)/4] <= write_word;
        else if (write_offset >= 32'h200 && write_offset < 32'h280)
          for (i = 0; i < 4; i++) activation[write_offset-32'h200+32'(i)] <= write_word[i*8+:8];
        else if (write_offset == 32'h280) activation_scale <= write_word;
        else s_bresp <= 2;
      end
      if (s_arvalid && s_arready) begin
        s_rid   <= s_arid;
        s_rresp <= 0;
        s_rdata <= 0;
        if (s_arlen != 0 || int'(s_arsize) > $clog2(
                BYTES
            ) || (s_araddr % BYTES) + (1 << s_arsize) > BYTES) begin
          s_rvalid <= 1;
          s_rresp  <= 3;
        end
        else if(s_araddr>=ROM_BASE && s_araddr-ROM_BASE<ROM_BYTES &&
                (s_araddr-ROM_BASE)+(1<<s_arsize)<=ROM_BYTES) begin
          cpu_base <= ((s_araddr - ROM_BASE) / BYTES) * BYTES;
          state <= CPU_REQ;
        end else if(ENGINE_ENABLE && s_araddr>=MMIO_BASE && s_araddr<MMIO_BASE+32'h1000 &&
                    s_arsize==2 && s_araddr[1:0]==0) begin
          s_rdata[(s_araddr%BYTES)*8+:32] <= mmio_read(s_araddr - MMIO_BASE);
          s_rvalid <= 1;
        end else begin
          s_rvalid <= 1;
          s_rresp  <= 3;
        end
      end
      case (state)
        CPU_REQ: if (storage_valid && storage_ready) state <= CPU_WAIT;
        CPU_WAIT:
        if (storage_rsp_valid) begin
          for (i = 0; i < BYTES; i++)
          s_rdata[i*8+:8] <= cpu_base + 32'(i) < ROM_BYTES ? storage_rsp_data[i*8+:8] : 8'b0;
          state <= IDLE;
          s_rvalid <= 1;
          if (storage_rsp_error) s_rresp <= 2;
        end
        ENG_FETCH, ENG_DRAIN: begin
          // Responses carry no IDs: match them to accepted descriptors in order.
          // A response may be presented immediately, but cannot be consumed until
          // its descriptor was registered on an earlier clock edge.
          case ({
            eng_req_fire, eng_rsp_fire
          })
            2'b10:   fetch_outstanding <= fetch_outstanding + 1'b1;
            2'b01:   fetch_outstanding <= fetch_outstanding - 1'b1;
            default: ;
          endcase
          if (eng_req_fire) begin
            fetch_desc[fetch_tail].unit_index <= 5'(fetch_unit);
            fetch_desc[fetch_tail].byte_index <= 6'(fetch_byte);
            fetch_desc[fetch_tail].beat_offset <= 4'(fetch_offset);
            fetch_desc[fetch_tail].copy_bytes <= 5'(fetch_copy);
            fetch_tail <= next_ptr(fetch_tail);
            if (fetch_byte + fetch_copy == 34) begin
              fetch_byte <= 0;
              if (fetch_unit == unit_count - 1) fetch_more <= 0;
              else fetch_unit <= fetch_unit + 1;
            end else fetch_byte <= fetch_byte + fetch_copy;
          end
          if (eng_rsp_fire) fetch_head <= next_ptr(fetch_head);

          if (state == ENG_DRAIN) begin
            // A request already presented at the error must remain valid. Drain
            // its response too, before giving storage ownership back to the CPU.
            if (eng_req_fire) eng_req_valid <= 0;
            if(int'(fetch_outstanding)+int'(eng_req_fire)-int'(eng_rsp_fire)==0 &&
               (!eng_req_valid || eng_req_fire)) begin
              busy  <= 0;
              state <= IDLE;
            end
          end else if (eng_rsp_fire && storage_rsp_error) begin
            done <= 0;
            error <= 1;
            state <= ENG_DRAIN;
            eng_req_valid <= eng_req_valid && !eng_req_fire;
          end else begin
            if (eng_req_fire) begin
              // Reserve capacity for the next presented request. When full,
              // leave the cursor at the next unread byte and resume after space
              // is available. FETCH_DEPTH=1 is valid and explicitly serialized.
              eng_req_valid<=!(fetch_unit==unit_count-1 && fetch_byte+fetch_copy==34) &&
                int'(fetch_outstanding)+1-int'(eng_rsp_fire)<FETCH_DEPTH;
            end else if (!eng_req_valid && fetch_more && int'(fetch_outstanding) < FETCH_DEPTH)
              eng_req_valid <= 1;
            if (eng_rsp_fire) begin
              dest_unit   = fetch_desc[fetch_head].unit_index;
              dest_byte   = int'(fetch_desc[fetch_head].byte_index);
              beat_offset = int'(fetch_desc[fetch_head].beat_offset);
              copy_bytes  = int'(fetch_desc[fetch_head].copy_bytes);
              for (i = 0; i < BYTES; i++)
              if (i < copy_bytes)
                packed_weight[dest_unit][dest_byte+i] <= storage_rsp_data[(beat_offset+i)*8+:8];
              if (fetch_outstanding == 1 && !eng_req_valid && !fetch_more) begin
                mac_lane <= 0;
                state <= ENG_MAC;
              end
            end
          end
        end
        ENG_MAC: begin
          for (i = 0; i < 32; i++)
          if (i < unit_count) begin
            code = (int'(packed_weight[i][2+mac_lane/4]) >> (2 * (mac_lane % 4))) & 3;
            case (code)
              0: prod = -int'($signed(activation[mac_lane]));
              1: prod = 0;
              2: prod = int'($signed(activation[mac_lane]));
              default: prod = int'($signed(activation[mac_lane])) <<< 1;
            endcase
            result[i][mac_lane/32] <= result[i][mac_lane/32] + prod;
          end
          if (mac_lane == 127) begin
            busy  <= 0;
            done  <= 1;
            state <= IDLE;
          end else mac_lane <= mac_lane + 1;
        end
        default: ;
      endcase
    end
  end
endmodule
