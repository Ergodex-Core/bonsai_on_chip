// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// One-entry, bundled-data CDC mailbox. Both ends MUST undergo paired reset.
// The sender holds payload_q from request until the acknowledgement has
// returned through two synchronizer stages. The receiver observes the payload
// only after the request has crossed two stages, then holds VALID until READY.
// Physical implementation must constrain payload_q -> dst_data_o to less than
// one destination clock period; it must not blanket-false-path the data bus.
module hbm_cdc_mailbox #(
    parameter integer WIDTH = 1
) (
    input logic src_clk_i,
    input logic src_rst_ni,
    input logic src_valid_i,
    output logic src_ready_o,
    input logic [WIDTH-1:0] src_data_i,
    input logic dst_clk_i,
    input logic dst_rst_ni,
    output logic dst_valid_o,
    input logic dst_ready_i,
    output logic [WIDTH-1:0] dst_data_o
);
  logic [WIDTH-1:0] payload_q;
  logic request_q, acknowledge_q;
  (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *)logic [1:0] request_sync_q;
  (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *)logic [1:0] acknowledge_sync_q;

  assign src_ready_o = src_rst_ni && request_q == acknowledge_sync_q[1];
  assign dst_valid_o = dst_rst_ni && request_sync_q[1] != acknowledge_q;
  assign dst_data_o  = payload_q;

  always_ff @(posedge src_clk_i or negedge src_rst_ni) begin
    if (!src_rst_ni) begin
      payload_q <= '0;
      request_q <= 1'b0;
      acknowledge_sync_q <= '0;
    end else begin
      acknowledge_sync_q <= {acknowledge_sync_q[0], acknowledge_q};
      if (src_valid_i && src_ready_o) begin
        payload_q <= src_data_i;
        request_q <= ~request_q;
      end
    end
  end

  always_ff @(posedge dst_clk_i or negedge dst_rst_ni) begin
    if (!dst_rst_ni) begin
      request_sync_q <= '0;
      acknowledge_q  <= 1'b0;
    end else begin
      request_sync_q <= {request_sync_q[0], request_q};
      if (dst_valid_o && dst_ready_i) acknowledge_q <= request_sync_q[1];
    end
  end
endmodule
