// CPU AXI peripheral, independent of Coral source. Single-beat AXI accesses.
// Storage port uses aligned DATA_BITS beats, byte addresses, read-only, one outstanding transaction.
// Modified from native_pq2/baseline: spatial-L parallel reduction tile with
// shared activation transforms; no neighbor-to-neighbor systolic propagation.
module coral_weight_axi #(
  parameter int DATA_BITS=128, ID_BITS=6,
  parameter bit ENGINE_ENABLE=1,
  parameter logic [31:0] ROM_BASE=32'h40000000,
  parameter logic [31:0] MMIO_BASE=32'h60000000,
  parameter int unsigned ROM_BYTES=463290464,
  parameter int unsigned DOT_LANES=8
)(
  input logic clk, reset,
  input logic [31:0] s_araddr, input logic [ID_BITS-1:0] s_arid,
  input logic [7:0] s_arlen, input logic [2:0] s_arsize,
  input logic s_arvalid, output logic s_arready,
  output logic [DATA_BITS-1:0] s_rdata, output logic [ID_BITS-1:0] s_rid,
  output logic [1:0] s_rresp, output logic s_rlast, s_rvalid, input logic s_rready,
  input logic [31:0] s_awaddr, input logic [ID_BITS-1:0] s_awid,
  input logic [7:0] s_awlen, input logic [2:0] s_awsize,
  input logic s_awvalid, output logic s_awready,
  input logic [DATA_BITS-1:0] s_wdata, input logic [DATA_BITS/8-1:0] s_wstrb,
  input logic s_wlast, s_wvalid, output logic s_wready,
  output logic [ID_BITS-1:0] s_bid, output logic [1:0] s_bresp,
  output logic s_bvalid, input logic s_bready,
  output logic storage_valid, input logic storage_ready,
  output logic [31:0] storage_addr,
  input logic storage_rsp_valid, storage_rsp_error, input logic [DATA_BITS-1:0] storage_rsp_data,
  output logic storage_rsp_ready
);
  localparam int BYTES=DATA_BITS/8;
  localparam int PRODUCT_BITS=10;
  localparam int REDUCTION_LEVELS=$clog2(DOT_LANES);
  localparam int TILE_SUM_BITS=PRODUCT_BITS+REDUCTION_LEVELS;
  // Every supported tile divides the native 32-element subgroup exactly.
  // Refuse other configurations instead of dropping lanes at a boundary.
  initial begin
    if (!(DOT_LANES==1 || DOT_LANES==2 || DOT_LANES==4 ||
          DOT_LANES==8 || DOT_LANES==16 || DOT_LANES==32))
      $fatal(1, "DOT_LANES must be one of 1, 2, 4, 8, 16, 32");
  end
  typedef enum logic [2:0] {IDLE, CPU_REQ, CPU_WAIT, ENG_REQ, ENG_WAIT, ENG_MAC} state_t;
  state_t state;
  logic aw_hold,w_hold;
  logic [31:0] aw_addr;
  logic [ID_BITS-1:0] aw_id;
  logic [7:0] aw_len;
  logic [2:0] aw_size;
  logic [DATA_BITS-1:0] w_data;
  logic [BYTES-1:0] w_strb;
  logic w_last;
  logic [31:0] cpu_base;
  integer fetch_unit, fetch_byte;
  logic [6:0] mac_lane;
  logic busy,done,error;
  logic [31:0] cycles;
  integer unit_count;
  logic [31:0] weight_addr[32];
  logic [7:0] packed_weight[32][34];
  logic signed [7:0] activation[128];
  logic [31:0] activation_scale;
  logic signed [31:0] result[32][4];
  logic signed [PRODUCT_BITS-1:0] shared_x[DOT_LANES];
  logic signed [PRODUCT_BITS-1:0] shared_neg_x[DOT_LANES];
  logic signed [PRODUCT_BITS-1:0] shared_twice_x[DOT_LANES];
  logic signed [TILE_SUM_BITS-1:0] tile_sum[32];
  logic [31:0] write_offset;
  integer write_lane;
  logic [31:0] write_word;
  logic [3:0] write_mask;
  assign s_awready=!aw_hold&&!s_bvalid;
  assign s_wready=!w_hold&&!s_bvalid;
  assign s_arready=!s_rvalid && (state==IDLE ||
    (ENGINE_ENABLE && s_araddr>=MMIO_BASE && s_araddr<MMIO_BASE+32'h1000));
  assign s_rlast=1'b1;
  assign storage_valid=state==CPU_REQ || state==ENG_REQ;
  assign storage_addr=state==CPU_REQ ? cpu_base :
    ((weight_addr[fetch_unit]+32'(fetch_byte))/BYTES)*BYTES;
  assign storage_rsp_ready=state==CPU_WAIT || state==ENG_WAIT;
  assign write_offset=aw_addr-MMIO_BASE;
  assign write_lane=int'(aw_addr%BYTES);
  assign write_word=w_data[write_lane*8 +: 32];
  assign write_mask=w_strb[write_lane +: 4];

  // Widen before negating or shifting: x=-128 requires -x=128 and 2x=-256.
  // These transforms fan out to all output units; they are not per-weight
  // multipliers. The original PQ2 codes are 00=-1, 01=0, 10=+1, 11=+2.
  for(genvar lane=0;lane<DOT_LANES;lane++) begin : activation_transform
    wire [6:0] element_index=mac_lane+7'(lane);
    wire signed [7:0] x=activation[element_index];
    assign shared_x[lane]={{(PRODUCT_BITS-8){x[7]}},x};
    assign shared_neg_x[lane]=-shared_x[lane];
    assign shared_twice_x[lane]=shared_x[lane]<<<1;
  end

  // Balanced reduction with one guard bit per level. There is one tile sum
  // and one accumulator update per active output, including DOT_LANES=1.
  for(genvar unit=0;unit<32;unit++) begin : output_tile
    for(genvar level=0;level<=REDUCTION_LEVELS;level++) begin : reduction
      localparam int LEVEL_BITS=PRODUCT_BITS+level;
      logic signed [LEVEL_BITS-1:0] value[DOT_LANES>>level];
      if(level==0) begin : products
        for(genvar lane=0;lane<DOT_LANES;lane++) begin : select_product
          wire [6:0] element_index=mac_lane+7'(lane);
          wire [1:0] code=packed_weight[unit][2+int'(element_index[6:2])]
            [2*int'(element_index[1:0])+:2];
          assign value[lane]=code==2'b00 ? shared_neg_x[lane] :
            code==2'b01 ? PRODUCT_BITS'(0) :
            code==2'b10 ? shared_x[lane] : shared_twice_x[lane];
        end
      end else begin : sums
        for(genvar node=0;node<(DOT_LANES>>level);node++) begin : add_pair
          wire signed [LEVEL_BITS-2:0] left=reduction[level-1].value[2*node];
          wire signed [LEVEL_BITS-2:0] right=reduction[level-1].value[2*node+1];
          assign value[node]=$signed({left[LEVEL_BITS-2],left})+
            $signed({right[LEVEL_BITS-2],right});
        end
      end
    end
    assign tile_sum[unit]=reduction[REDUCTION_LEVELS].value[0];
  end

  function automatic logic [31:0] mmio_read(input logic [31:0] off);
    if(off==0) return {29'b0,error,done,busy};
    if(off==8) return 32'(unit_count);
    if(off==16) return cycles;
    if(off>=32'h100 && off<32'h180) return weight_addr[(off-32'h100)/4];
    if(off>=32'h200 && off<32'h280) return {
      activation[off-32'h200+3],activation[off-32'h200+2],
      activation[off-32'h200+1],activation[off-32'h200]};
    if(off==32'h280) return activation_scale;
    if(off>=32'h400 && off<32'h600) return result[(off-32'h400)/16][((off-32'h400)%16)/4];
    if(off>=32'h600 && off<32'h640) return {
      packed_weight[(off-32'h600)/2+1][1],packed_weight[(off-32'h600)/2+1][0],
      packed_weight[(off-32'h600)/2][1],packed_weight[(off-32'h600)/2][0]};
    return 0;
  endfunction

  always_ff @(posedge clk) begin : sequential
    integer i,j,beat_offset,copy_bytes;
    logic valid_addresses;
    if(reset) begin
      state<=IDLE; aw_hold<=0;w_hold<=0;s_bvalid<=0;s_rvalid<=0;
      s_bresp<=0;s_rresp<=0;s_bid<=0;s_rid<=0;s_rdata<=0;
      busy<=0;done<=0;error<=0;cycles<=0;unit_count<=32;
      cpu_base<=0;fetch_unit<=0;fetch_byte<=0;mac_lane<=0;
      aw_addr<=0;aw_id<=0;aw_len<=0;aw_size<=0;w_data<=0;w_strb<=0;w_last<=0;
      for(i=0;i<32;i++) begin
        weight_addr[i]<=0;
        for(j=0;j<4;j++) result[i][j]<=0;
        for(j=0;j<34;j++) packed_weight[i][j]<=0;
      end
      for(i=0;i<128;i++) activation[i]<=0;
      activation_scale<=0;
    end else begin
      if(busy) cycles<=cycles+1;
      if(s_bvalid&&s_bready) s_bvalid<=0;
      if(s_rvalid&&s_rready) s_rvalid<=0;
      if(s_awvalid&&s_awready) begin
        aw_hold<=1;aw_addr<=s_awaddr;aw_id<=s_awid;aw_len<=s_awlen;aw_size<=s_awsize;
      end
      if(s_wvalid&&s_wready) begin
        w_hold<=1;w_data<=s_wdata;w_strb<=s_wstrb;w_last<=s_wlast;
      end
      if(aw_hold&&w_hold&&!s_bvalid) begin
        aw_hold<=0;w_hold<=0;s_bvalid<=1;s_bid<=aw_id;s_bresp<=0;
        if(!ENGINE_ENABLE || aw_addr<MMIO_BASE || aw_addr>=MMIO_BASE+32'h1000 ||
           aw_len!=0 || aw_size!=2 || aw_addr[1:0]!=0 || !w_last) s_bresp<=3;
        else if(busy || state!=IDLE || (s_arvalid&&s_arready) || write_mask!=4'hf) s_bresp<=2;
        else if(write_offset==4 && write_word==1) begin
          valid_addresses=1;
          for(i=0;i<32;i++) if(i<unit_count && weight_addr[i]>ROM_BYTES-34) valid_addresses=0;
          done<=0;error<=!valid_addresses;
          if(valid_addresses) begin
            busy<=1;cycles<=0;fetch_unit<=0;fetch_byte<=0;state<=ENG_REQ;
            for(i=0;i<32;i++) for(j=0;j<4;j++) result[i][j]<=0;
          end else s_bresp<=2;
        end else if(write_offset==8 && write_word>=1 && write_word<=32) unit_count<=int'(write_word);
        else if(write_offset>=32'h100 && write_offset<32'h180)
          weight_addr[(write_offset-32'h100)/4]<=write_word;
        else if(write_offset>=32'h200 && write_offset<32'h280)
          for(i=0;i<4;i++) activation[write_offset-32'h200+32'(i)]<=write_word[i*8+:8];
        else if(write_offset==32'h280) activation_scale<=write_word;
        else s_bresp<=2;
      end
      if(s_arvalid&&s_arready) begin
        s_rid<=s_arid;s_rresp<=0;s_rdata<=0;
        if(s_arlen!=0 || int'(s_arsize)>$clog2(BYTES) ||
           (s_araddr%BYTES)+(1<<s_arsize)>BYTES) begin s_rvalid<=1;s_rresp<=3;end
        else if(s_araddr>=ROM_BASE && s_araddr-ROM_BASE<ROM_BYTES &&
                (s_araddr-ROM_BASE)+(1<<s_arsize)<=ROM_BYTES) begin
          cpu_base<=((s_araddr-ROM_BASE)/BYTES)*BYTES;state<=CPU_REQ;
        end else if(ENGINE_ENABLE && s_araddr>=MMIO_BASE && s_araddr<MMIO_BASE+32'h1000 &&
                    s_arsize==2 && s_araddr[1:0]==0) begin
          s_rdata[(s_araddr%BYTES)*8+:32]<=mmio_read(s_araddr-MMIO_BASE);s_rvalid<=1;
        end else begin s_rvalid<=1;s_rresp<=3;end
      end
      case(state)
        CPU_REQ: if(storage_valid&&storage_ready) state<=CPU_WAIT;
        CPU_WAIT: if(storage_rsp_valid) begin
          for(i=0;i<BYTES;i++) s_rdata[i*8+:8]<=cpu_base+32'(i)<ROM_BYTES ? storage_rsp_data[i*8+:8] : 8'b0;
          state<=IDLE;s_rvalid<=1;if(storage_rsp_error)s_rresp<=2;
        end
        ENG_REQ: if(storage_valid&&storage_ready) state<=ENG_WAIT;
        ENG_WAIT: if(storage_rsp_valid) begin
          if(storage_rsp_error) begin busy<=0;done<=0;error<=1;state<=IDLE;end
          else begin
          beat_offset=int'((weight_addr[fetch_unit]+32'(fetch_byte))%BYTES);
          copy_bytes=BYTES-beat_offset;
          if(copy_bytes>34-fetch_byte) copy_bytes=34-fetch_byte;
          for(i=0;i<BYTES;i++) if(i<copy_bytes)
            packed_weight[fetch_unit][fetch_byte+i]<=storage_rsp_data[(beat_offset+i)*8+:8];
          if(fetch_byte+copy_bytes==34) begin
            fetch_byte<=0;
            if(fetch_unit==unit_count-1) begin mac_lane<=0;state<=ENG_MAC;end
            else begin fetch_unit<=fetch_unit+1;state<=ENG_REQ;end
          end else begin fetch_byte<=fetch_byte+copy_bytes;state<=ENG_REQ;end
          end
        end
        ENG_MAC: begin
          for(i=0;i<32;i++) if(i<unit_count) begin
            result[i][mac_lane[6:5]]<=result[i][mac_lane[6:5]]+
              $signed({{(32-TILE_SUM_BITS){tile_sum[i][TILE_SUM_BITS-1]}},tile_sum[i]});
          end
          if(mac_lane==7'(128-DOT_LANES)) begin busy<=0;done<=1;state<=IDLE;end
          else mac_lane<=mac_lane+7'(DOT_LANES);
        end
        default: ;
      endcase
    end
  end
endmodule
