// SPDX-License-Identifier: Apache-2.0
// One native Q1_0 or directed ternary2 group; all weights arrive via line reads.
module dot128 (
    input  logic                 clk_i,
    input  logic                 rst_ni,
    input  logic                 cmd_valid_i,
    output logic                 cmd_ready_o,
    input  logic        [  47:0] cmd_weight_offset_i,
    input  logic                 cmd_format_i,               // 0: 18-byte Q1_0; 1: 32-byte ternary2
    input  logic        [  31:0] cmd_epoch_i,
    input  logic        [1023:0] cmd_activations_i,
    output logic                 mem_req_valid_o,
    input  logic                 mem_req_ready_i,
    output logic        [  47:0] mem_req_offset_o,
    output logic        [   7:0] mem_req_tag_o,
    output logic        [  31:0] mem_req_epoch_o,
    input  logic                 mem_rsp_valid_i,
    output logic                 mem_rsp_ready_o,
    input  logic        [ 511:0] mem_rsp_data_i,
    input  logic        [   7:0] mem_rsp_tag_i,
    input  logic        [  31:0] mem_rsp_epoch_i,
    input  logic        [   2:0] mem_rsp_status_i,
    output logic                 result_valid_o,
    input  logic                 result_ready_i,
    output logic signed [  31:0] result_acc_o,
    output logic        [  15:0] result_scale_o,
    output logic        [   2:0] result_status_o,
    output logic        [  63:0] result_cycles_o,
    output logic        [  63:0] result_mem_stall_cycles_o,
    output logic        [  63:0] result_read_requests_o,
    output logic                 fatal_o
);
  localparam logic [2:0] STATUS_OK = 3'd0;
  localparam logic [2:0] STATUS_RANGE = 3'd2;
  localparam logic [2:0] STATUS_PROTOCOL = 3'd7;
  typedef enum logic [2:0] {
    IDLE,
    ISSUE,
    WAIT_LINE,
    PREPARE,
    MAC,
    RESPONSE,
    FAULT
  } state_t;
  state_t state_q;
  logic [47:0] base_q;
  logic [5:0] offset_q;
  logic format_q, need_second_q, second_q;
  logic [  31:0] epoch_q;
  logic [1023:0] activations_q;
  // At offset 63, a 32-byte payload uses 64 bytes + 31 bytes of the next line.
  logic [ 759:0] line_window_q;
  logic [255:0] payload_q, payload_window;
  logic [6:0] index_q, command_span;
  logic signed [31:0] accumulator_q, activation, product, next_accumulator;
  logic [7:0] activation_byte;
  logic [1:0] ternary_code;
  logic illegal_code;
  logic [63:0] cycles_q, stalls_q, reads_q;

  assign cmd_ready_o = rst_ni && state_q == IDLE;
  assign mem_req_valid_o = rst_ni && state_q == ISSUE;
  assign mem_req_offset_o = second_q ? base_q + 48'd64 : base_q;
  assign mem_req_tag_o = {7'b0, second_q};
  assign mem_req_epoch_o = epoch_q;
  assign mem_rsp_ready_o = rst_ni && state_q == WAIT_LINE;
  assign result_valid_o = rst_ni && state_q == RESPONSE;
  assign command_span = {1'b0, cmd_weight_offset_i[5:0]} + (cmd_format_i ? 7'd32 : 7'd18);
  assign payload_window = line_window_q[{1'b0, offset_q, 3'b000}+:256];
  assign activation_byte = activations_q[{index_q, 3'b000}+:8];
  // Widen before negation so -(-128) is +128 rather than wrapped int8.
  assign activation = {{24{activation_byte[7]}}, activation_byte};
  assign ternary_code = payload_q[{index_q, 1'b0}+:2];
  always_comb begin
    product = 32'sd0;
    illegal_code = 1'b0;
    if (!format_q) begin
      product = payload_q[8'd16+{1'b0, index_q}] ? activation : -activation;
    end else begin
      case (ternary_code)
        2'b00:   product = 32'sd0;
        2'b01:   product = activation;
        2'b10:   product = -activation;
        default: illegal_code = 1'b1;
      endcase
    end
  end
  assign next_accumulator = accumulator_q + product;

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      state_q <= IDLE;
      base_q <= '0;
      offset_q <= '0;
      format_q <= 1'b0;
      need_second_q <= 1'b0;
      second_q <= 1'b0;
      epoch_q <= '0;
      activations_q <= '0;
      line_window_q <= '0;
      payload_q <= '0;
      index_q <= '0;
      accumulator_q <= '0;
      cycles_q <= '0;
      stalls_q <= '0;
      reads_q <= '0;
      result_acc_o <= '0;
      result_scale_o <= '0;
      result_status_o <= STATUS_OK;
      result_cycles_o <= '0;
      result_mem_stall_cycles_o <= '0;
      result_read_requests_o <= '0;
      fatal_o <= 1'b0;
    end else begin
      // Counts elapsed core cycles from command acceptance to result assertion;
      // result backpressure is excluded. Stalls count request/response waiting.
      if (state_q != IDLE && state_q != RESPONSE && state_q != FAULT) cycles_q <= cycles_q + 64'd1;
      if ((mem_req_valid_o && !mem_req_ready_i) || (mem_rsp_ready_o && !mem_rsp_valid_i))
        stalls_q <= stalls_q + 64'd1;
      case (state_q)
        IDLE:
        if (cmd_valid_i && cmd_ready_o) begin
          base_q <= {cmd_weight_offset_i[47:6], 6'b0};
          offset_q <= cmd_weight_offset_i[5:0];
          format_q <= cmd_format_i;
          need_second_q <= command_span > 7'd64;
          second_q <= 1'b0;
          epoch_q <= cmd_epoch_i;
          activations_q <= cmd_activations_i;
          line_window_q <= '0;
          payload_q <= '0;
          accumulator_q <= '0;
          index_q <= '0;
          cycles_q <= '0;
          stalls_q <= '0;
          reads_q <= '0;
          result_acc_o <= '0;
          result_scale_o <= '0;
          result_status_o <= STATUS_OK;
          result_cycles_o <= '0;
          result_mem_stall_cycles_o <= '0;
          result_read_requests_o <= '0;
          // Do not wrap the second line through the 48-bit address boundary.
          if (command_span > 7'd64 && (&cmd_weight_offset_i[47:6])) begin
            result_status_o <= STATUS_RANGE;
            state_q <= RESPONSE;
          end else begin
            state_q <= ISSUE;
          end
        end
        ISSUE:
        if (mem_req_valid_o && mem_req_ready_i) begin
          reads_q <= reads_q + 64'd1;
          state_q <= WAIT_LINE;
        end
        WAIT_LINE:
        if (mem_rsp_valid_i && mem_rsp_ready_o) begin
          if (mem_rsp_tag_i != {7'b0, second_q} || mem_rsp_epoch_i != epoch_q) begin
            // The real request may still be outstanding: never reuse its ID.
            result_status_o <= STATUS_PROTOCOL;
            result_cycles_o <= cycles_q + 64'd1;
            result_mem_stall_cycles_o <= stalls_q;
            result_read_requests_o <= reads_q;
            fatal_o <= 1'b1;
            state_q <= RESPONSE;
          end else if (mem_rsp_status_i != STATUS_OK) begin
            result_status_o <= mem_rsp_status_i;
            result_cycles_o <= cycles_q + 64'd1;
            result_mem_stall_cycles_o <= stalls_q;
            result_read_requests_o <= reads_q;
            state_q <= RESPONSE;
          end else begin
            if (second_q) line_window_q[759:512] <= mem_rsp_data_i[247:0];
            else line_window_q[511:0] <= mem_rsp_data_i;
            if (need_second_q && !second_q) begin
              second_q <= 1'b1;
              state_q  <= ISSUE;
            end else begin
              state_q <= PREPARE;
            end
          end
        end
        PREPARE: begin
          payload_q <= payload_window;
          if (!format_q && payload_window[14:10] == 5'b11111) begin
            // The integer datapath does not silently accept NaN/Inf metadata.
            result_status_o <= STATUS_PROTOCOL;
            result_cycles_o <= cycles_q + 64'd1;
            result_mem_stall_cycles_o <= stalls_q;
            result_read_requests_o <= reads_q;
            state_q <= RESPONSE;
          end else begin
            result_scale_o <= format_q ? 16'h3c00 : payload_window[15:0];
            state_q <= MAC;
          end
        end
        MAC: begin
          if (illegal_code) begin
            result_acc_o <= '0;
            result_scale_o <= '0;
            result_status_o <= STATUS_PROTOCOL;
            result_cycles_o <= cycles_q + 64'd1;
            result_mem_stall_cycles_o <= stalls_q;
            result_read_requests_o <= reads_q;
            state_q <= RESPONSE;
          end else if (index_q == 7'd127) begin
            result_acc_o <= next_accumulator;
            result_cycles_o <= cycles_q + 64'd1;
            result_mem_stall_cycles_o <= stalls_q;
            result_read_requests_o <= reads_q;
            state_q <= RESPONSE;
          end else begin
            accumulator_q <= next_accumulator;
            index_q <= index_q + 7'd1;
          end
        end
        RESPONSE: if (result_ready_i) state_q <= fatal_o ? FAULT : IDLE;
        FAULT: state_q <= FAULT;
        default: begin
          fatal_o <= 1'b1;
          state_q <= FAULT;
        end
      endcase
    end
  end
endmodule
