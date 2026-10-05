// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// Pinned clk_mmcm_hbm: 100MHz reference -> 450MHz H2. No software clock/reset access.
module hbm_fixed_clock (
    input  logic clk_ref_i,
    rst_ni,
    output logic clk_hbm_o,
    rst_hbm_no,
    rst_ref_no,
    locked_o
);
  xpm_cdc_async_rst #(
      .DEST_SYNC_FF(4),
      .INIT_SYNC_FF(0),
      .RST_ACTIVE_HIGH(0)
  ) REF_RESET (
      .src_arst (rst_ni),
      .dest_clk (clk_ref_i),
      .dest_arst(rst_ref_no)
  );
  // Keep this generate/instance name aligned with pinned AWS clock constraints.
  // verilog_lint: waive-start generate-label-prefix
  if (1) begin : CLK_HBM_EN_I
    clk_mmcm_hbm CLK_MMCM_HBM_I (
        .clk_in1(clk_ref_i),
        .clk_out1(clk_hbm_o),
        .locked(locked_o),
        .s_axi_aclk(clk_ref_i),
        .s_axi_aresetn(rst_ref_no),
        .s_axi_awaddr(11'd0),
        .s_axi_awvalid(1'b0),
        .s_axi_awready(),
        .s_axi_wdata(32'd0),
        .s_axi_wstrb(4'd0),
        .s_axi_wvalid(1'b0),
        .s_axi_wready(),
        .s_axi_bresp(),
        .s_axi_bvalid(),
        .s_axi_bready(1'b1),
        .s_axi_araddr(11'd0),
        .s_axi_arvalid(1'b0),
        .s_axi_arready(),
        .s_axi_rdata(),
        .s_axi_rresp(),
        .s_axi_rvalid(),
        .s_axi_rready(1'b1)
    );
  end
  // verilog_lint: waive-stop generate-label-prefix
  // Stretch any lock-loss assertion through eight destination stages. Even
  // immediate relock leaves ready low for >15ns at450MHz (>three main cycles),
  // so the independent250MHz source monitor can capture the loss.
  xpm_cdc_async_rst #(
      .DEST_SYNC_FF(8),
      .INIT_SYNC_FF(0),
      .RST_ACTIVE_HIGH(0)
  ) HBM_RESET (
      .src_arst (rst_ni && locked_o),
      .dest_clk (clk_hbm_o),
      .dest_arst(rst_hbm_no)
  );
endmodule
