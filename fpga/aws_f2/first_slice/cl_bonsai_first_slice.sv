// ============================================================================
// Amazon FPGA Hardware Development Kit
//
// Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Amazon Software License (the "License"). You may not use
// this file except in compliance with the License. A copy of the License is
// located at
//
//    http://aws.amazon.com/asl/
//
// or in the "license" file accompanying this file. This file is distributed on
// an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, express or
// implied. See the License for the specific language governing permissions and
// limitations under the License.
// ============================================================================


// DDR/stats hookup adapted from pinned AWS cl_mem_perf; all compute and guard
// logic is in the original Ergodex modules. Fixed shell main clock, no HBM,
// scrubber, test generator, PCIM writer or other bypass around the loader guard.
module cl_bonsai_first_slice (
    `include "cl_ports.vh"
);
  `include "cl_id_defines.vh"
  logic [15:0] ddr_awid;
  logic [63:0] ddr_awaddr;
  logic [7:0] ddr_awlen;
  logic [2:0] ddr_awsize;
  logic [1:0] ddr_awburst;
  logic ddr_awvalid;
  logic ddr_awready;
  logic [511:0] ddr_wdata;
  logic [63:0] ddr_wstrb;
  logic ddr_wlast;
  logic ddr_wvalid;
  logic ddr_wready;
  logic [15:0] ddr_bid;
  logic [1:0] ddr_bresp;
  logic ddr_bvalid;
  logic ddr_bready;
  logic [15:0] ddr_arid;
  logic [63:0] ddr_araddr;
  logic [7:0] ddr_arlen;
  logic [2:0] ddr_arsize;
  logic [1:0] ddr_arburst;
  logic ddr_arvalid;
  logic ddr_arready;
  logic [15:0] ddr_rid;
  logic [511:0] ddr_rdata;
  logic [1:0] ddr_rresp;
  logic ddr_rlast;
  logic ddr_rvalid;
  logic ddr_rready;
  logic loader_enable, loader_idle, backend_ready, loader_write_rejected;
  logic [63:0] image_base, image_bytes;
  logic mem_req_valid, mem_req_ready, mem_rsp_valid, mem_rsp_ready, mem_rsp_error;
  logic [63:0] mem_req_addr;
  logic [511:0] mem_rsp_data;
  logic ddr_ready;
  assign cl_sh_id0 = `CL_SH_ID0;
  assign cl_sh_id1 = `CL_SH_ID1;
  assign cl_sh_status_vled = {14'd0, backend_ready, ddr_ready};
  // No in-band reset can unlock sealed memory. Reconfiguration/reset of the
  // entire shell is the coordinated reset boundary for this first slice.
  assign cl_sh_flr_done = 1'b0;
  assign cl_sh_status0 = '0;
  assign cl_sh_status1 = '0;
  assign cl_sh_status2 = '0;
  assign cl_sh_dma_wr_full = '0;
  assign cl_sh_dma_rd_full = '0;
  assign cl_sh_pcim_awid = '0;
  assign cl_sh_pcim_awaddr = '0;
  assign cl_sh_pcim_awlen = '0;
  assign cl_sh_pcim_awsize = '0;
  assign cl_sh_pcim_awburst = '0;
  assign cl_sh_pcim_awcache = '0;
  assign cl_sh_pcim_awlock = '0;
  assign cl_sh_pcim_awprot = '0;
  assign cl_sh_pcim_awqos = '0;
  assign cl_sh_pcim_awuser = '0;
  assign cl_sh_pcim_awvalid = '0;
  assign cl_sh_pcim_wid = '0;
  assign cl_sh_pcim_wdata = '0;
  assign cl_sh_pcim_wstrb = '0;
  assign cl_sh_pcim_wlast = '0;
  assign cl_sh_pcim_wuser = '0;
  assign cl_sh_pcim_wvalid = '0;
  assign cl_sh_pcim_bready = '0;
  assign cl_sh_pcim_arid = '0;
  assign cl_sh_pcim_araddr = '0;
  assign cl_sh_pcim_arlen = '0;
  assign cl_sh_pcim_arsize = '0;
  assign cl_sh_pcim_arburst = '0;
  assign cl_sh_pcim_arcache = '0;
  assign cl_sh_pcim_arlock = '0;
  assign cl_sh_pcim_arprot = '0;
  assign cl_sh_pcim_arqos = '0;
  assign cl_sh_pcim_aruser = '0;
  assign cl_sh_pcim_arvalid = '0;
  assign cl_sh_pcim_rready = '0;
  assign cl_sh_apppf_irq_req = '0;
  assign cl_sda_awready = '0;
  assign cl_sda_wready = '0;
  assign cl_sda_bresp = '0;
  assign cl_sda_bvalid = '0;
  assign cl_sda_arready = '0;
  assign cl_sda_rdata = '0;
  assign cl_sda_rresp = '0;
  assign cl_sda_rvalid = '0;
  assign tdo = '0;
  assign hbm_apb_paddr_1 = '0;
  assign hbm_apb_pprot_1 = '0;
  assign hbm_apb_psel_1 = '0;
  assign hbm_apb_penable_1 = '0;
  assign hbm_apb_pwrite_1 = '0;
  assign hbm_apb_pwdata_1 = '0;
  assign hbm_apb_pstrb_1 = '0;
  assign hbm_apb_pready_1 = '0;
  assign hbm_apb_prdata_1 = '0;
  assign hbm_apb_pslverr_1 = '0;
  assign hbm_apb_paddr_0 = '0;
  assign hbm_apb_pprot_0 = '0;
  assign hbm_apb_psel_0 = '0;
  assign hbm_apb_penable_0 = '0;
  assign hbm_apb_pwrite_0 = '0;
  assign hbm_apb_pwdata_0 = '0;
  assign hbm_apb_pstrb_0 = '0;
  assign hbm_apb_pready_0 = '0;
  assign hbm_apb_prdata_0 = '0;
  assign hbm_apb_pslverr_0 = '0;
  assign PCIE_EP_TXP = '0;
  assign PCIE_EP_TXN = '0;
  assign PCIE_RP_PERSTN = '0;
  assign PCIE_RP_TXP = '0;
  assign PCIE_RP_TXN = '0;

  first_slice_top APP (
      .clk_i(clk_main_a0),
      .rst_ni(rst_main_n),
      .s_axi_awaddr(ocl_cl_awaddr),
      .s_axi_awvalid(ocl_cl_awvalid),
      .s_axi_awready(cl_ocl_awready),
      .s_axi_wdata(ocl_cl_wdata),
      .s_axi_wstrb(ocl_cl_wstrb),
      .s_axi_wvalid(ocl_cl_wvalid),
      .s_axi_wready(cl_ocl_wready),
      .s_axi_bresp(cl_ocl_bresp),
      .s_axi_bvalid(cl_ocl_bvalid),
      .s_axi_bready(ocl_cl_bready),
      .s_axi_araddr(ocl_cl_araddr),
      .s_axi_arvalid(ocl_cl_arvalid),
      .s_axi_arready(cl_ocl_arready),
      .s_axi_rdata(cl_ocl_rdata),
      .s_axi_rresp(cl_ocl_rresp),
      .s_axi_rvalid(cl_ocl_rvalid),
      .s_axi_rready(ocl_cl_rready),
      .mem_req_valid_o(mem_req_valid),
      .mem_req_ready_i(mem_req_ready),
      .mem_req_addr_o(mem_req_addr),
      .mem_rsp_valid_i(mem_rsp_valid),
      .mem_rsp_ready_o(mem_rsp_ready),
      .mem_rsp_data_i(mem_rsp_data),
      .mem_rsp_error_i(mem_rsp_error),
      .loader_enable_o(loader_enable),
      .loader_idle_i(loader_idle),
      .backend_ready_i(backend_ready),
      .loader_write_rejected_i(loader_write_rejected),
      .image_base_o(image_base),
      .image_bytes_o(image_bytes)
  );

  f2_memory_bridge MEMORY (
      .clk_i(clk_main_a0),
      .rst_ni(rst_main_n),
      .ddr_ready_i(ddr_ready),
      .mem_req_valid_i(mem_req_valid),
      .mem_req_ready_o(mem_req_ready),
      .mem_req_addr_i(mem_req_addr),
      .mem_rsp_valid_o(mem_rsp_valid),
      .mem_rsp_ready_i(mem_rsp_ready),
      .mem_rsp_data_o(mem_rsp_data),
      .mem_rsp_error_o(mem_rsp_error),
      .loader_enable_i(loader_enable),
      .loader_idle_o(loader_idle),
      .backend_ready_o(backend_ready),
      .loader_write_rejected_o(loader_write_rejected),
      .image_base_i(image_base),
      .image_bytes_i(image_bytes),
      .s_axi_awid(sh_cl_dma_pcis_awid),
      .s_axi_awaddr(sh_cl_dma_pcis_awaddr),
      .s_axi_awlen(sh_cl_dma_pcis_awlen),
      .s_axi_awsize(sh_cl_dma_pcis_awsize),
      .s_axi_awburst(sh_cl_dma_pcis_awburst),
      .s_axi_awvalid(sh_cl_dma_pcis_awvalid),
      .s_axi_awready(cl_sh_dma_pcis_awready),
      .s_axi_wdata(sh_cl_dma_pcis_wdata),
      .s_axi_wstrb(sh_cl_dma_pcis_wstrb),
      .s_axi_wlast(sh_cl_dma_pcis_wlast),
      .s_axi_wvalid(sh_cl_dma_pcis_wvalid),
      .s_axi_wready(cl_sh_dma_pcis_wready),
      .s_axi_bid(cl_sh_dma_pcis_bid),
      .s_axi_bresp(cl_sh_dma_pcis_bresp),
      .s_axi_bvalid(cl_sh_dma_pcis_bvalid),
      .s_axi_bready(sh_cl_dma_pcis_bready),
      .s_axi_arid(sh_cl_dma_pcis_arid),
      .s_axi_araddr(sh_cl_dma_pcis_araddr),
      .s_axi_arlen(sh_cl_dma_pcis_arlen),
      .s_axi_arsize(sh_cl_dma_pcis_arsize),
      .s_axi_arburst(sh_cl_dma_pcis_arburst),
      .s_axi_arvalid(sh_cl_dma_pcis_arvalid),
      .s_axi_arready(cl_sh_dma_pcis_arready),
      .s_axi_rid(cl_sh_dma_pcis_rid),
      .s_axi_rdata(cl_sh_dma_pcis_rdata),
      .s_axi_rresp(cl_sh_dma_pcis_rresp),
      .s_axi_rlast(cl_sh_dma_pcis_rlast),
      .s_axi_rvalid(cl_sh_dma_pcis_rvalid),
      .s_axi_rready(sh_cl_dma_pcis_rready),
      .m_axi_awid(ddr_awid),
      .m_axi_awaddr(ddr_awaddr),
      .m_axi_awlen(ddr_awlen),
      .m_axi_awsize(ddr_awsize),
      .m_axi_awburst(ddr_awburst),
      .m_axi_awvalid(ddr_awvalid),
      .m_axi_awready(ddr_awready),
      .m_axi_wdata(ddr_wdata),
      .m_axi_wstrb(ddr_wstrb),
      .m_axi_wlast(ddr_wlast),
      .m_axi_wvalid(ddr_wvalid),
      .m_axi_wready(ddr_wready),
      .m_axi_bid(ddr_bid),
      .m_axi_bresp(ddr_bresp),
      .m_axi_bvalid(ddr_bvalid),
      .m_axi_bready(ddr_bready),
      .m_axi_arid(ddr_arid),
      .m_axi_araddr(ddr_araddr),
      .m_axi_arlen(ddr_arlen),
      .m_axi_arsize(ddr_arsize),
      .m_axi_arburst(ddr_arburst),
      .m_axi_arvalid(ddr_arvalid),
      .m_axi_arready(ddr_arready),
      .m_axi_rid(ddr_rid),
      .m_axi_rdata(ddr_rdata),
      .m_axi_rresp(ddr_rresp),
      .m_axi_rlast(ddr_rlast),
      .m_axi_rvalid(ddr_rvalid),
      .m_axi_rready(ddr_rready)
  );
  assign cl_sh_dma_pcis_ruser = '0;
  localparam NUM_CFG_STGS_CL_DDR_ATG = 8;

  logic [ 7:0] sh_ddr_stat_addr_q;
  logic        sh_ddr_stat_wr_q;
  logic        sh_ddr_stat_rd_q;
  logic [31:0] sh_ddr_stat_wdata_q;
  logic        ddr_sh_stat_ack_q;
  logic [31:0] ddr_sh_stat_rdata_q;
  logic [ 7:0] ddr_sh_stat_int_q;

  // !!NOTE!!: Tie SH_DDR resets to rst_main_n ONLY!!
  logic        ddr_sync_rst_n;

  xpm_cdc_async_rst CDC_ASYNC_RST_N_DDR (
      .src_arst (rst_main_n),
      .dest_clk (clk_main_a0),
      .dest_arst(ddr_sync_rst_n)
  );

  lib_pipe #(
      .WIDTH (1 + 1 + 8 + 32),
      .STAGES(NUM_CFG_STGS_CL_DDR_ATG)
  ) PIPE_DDR_STAT0 (
      .clk    (clk_main_a0),
      .rst_n  (ddr_sync_rst_n),
      .in_bus ({sh_cl_ddr_stat_wr, sh_cl_ddr_stat_rd, sh_cl_ddr_stat_addr, sh_cl_ddr_stat_wdata}),
      .out_bus({sh_ddr_stat_wr_q, sh_ddr_stat_rd_q, sh_ddr_stat_addr_q, sh_ddr_stat_wdata_q})
  );

  lib_pipe #(
      .WIDTH (1 + 8 + 32),
      .STAGES(NUM_CFG_STGS_CL_DDR_ATG)
  ) PIPE_DDR_STAT_ACK0 (
      .clk    (clk_main_a0),
      .rst_n  (ddr_sync_rst_n),
      .in_bus ({ddr_sh_stat_ack_q, ddr_sh_stat_int_q, ddr_sh_stat_rdata_q}),
      .out_bus({cl_sh_ddr_stat_ack, cl_sh_ddr_stat_int, cl_sh_ddr_stat_rdata})
  );

  // `define USE_AP_64GB_DDR_DIMM  // This is 64GB DDR controller with user-controlled Auto Precharge

  sh_ddr #(
      .DDR_PRESENT(1)
  ) SH_DDR (
      .clk       (clk_main_a0),
      .rst_n     (ddr_sync_rst_n),
      .stat_clk  (clk_main_a0),
      .stat_rst_n(ddr_sync_rst_n),

      .CLK_DIMM_DP  (CLK_DIMM_DP),
      .CLK_DIMM_DN  (CLK_DIMM_DN),
      .M_ACT_N      (M_ACT_N),
      .M_MA         (M_MA),
      .M_BA         (M_BA),
      .M_BG         (M_BG),
      .M_CKE        (M_CKE),
      .M_ODT        (M_ODT),
      .M_CS_N       (M_CS_N),
      .M_CLK_DN     (M_CLK_DN),
      .M_CLK_DP     (M_CLK_DP),
      .M_PAR        (M_PAR),
      .M_DQ         (M_DQ),
      .M_ECC        (M_ECC),
      .M_DQS_DP     (M_DQS_DP),
      .M_DQS_DN     (M_DQS_DN),
      .cl_RST_DIMM_N(RST_DIMM_N),

      .cl_sh_ddr_axi_awid   (ddr_awid),
      .cl_sh_ddr_axi_awaddr (ddr_awaddr),
      .cl_sh_ddr_axi_awlen  (ddr_awlen),
      .cl_sh_ddr_axi_awsize (ddr_awsize),
      .cl_sh_ddr_axi_awvalid(ddr_awvalid),
      .cl_sh_ddr_axi_awburst(ddr_awburst),
      .cl_sh_ddr_axi_awuser (1'd0),
      .cl_sh_ddr_axi_awready(ddr_awready),
      .cl_sh_ddr_axi_wdata  (ddr_wdata),
      .cl_sh_ddr_axi_wstrb  (ddr_wstrb),
      .cl_sh_ddr_axi_wlast  (ddr_wlast),
      .cl_sh_ddr_axi_wvalid (ddr_wvalid),
      .cl_sh_ddr_axi_wready (ddr_wready),
      .cl_sh_ddr_axi_bid    (ddr_bid),
      .cl_sh_ddr_axi_bresp  (ddr_bresp),
      .cl_sh_ddr_axi_bvalid (ddr_bvalid),
      .cl_sh_ddr_axi_bready (ddr_bready),
      .cl_sh_ddr_axi_arid   (ddr_arid),
      .cl_sh_ddr_axi_araddr (ddr_araddr),
      .cl_sh_ddr_axi_arlen  (ddr_arlen),
      .cl_sh_ddr_axi_arsize (ddr_arsize),
      .cl_sh_ddr_axi_arvalid(ddr_arvalid),
      .cl_sh_ddr_axi_arburst(ddr_arburst),
      .cl_sh_ddr_axi_aruser (1'd0),
      .cl_sh_ddr_axi_arready(ddr_arready),
      .cl_sh_ddr_axi_rid    (ddr_rid),
      .cl_sh_ddr_axi_rdata  (ddr_rdata),
      .cl_sh_ddr_axi_rresp  (ddr_rresp),
      .cl_sh_ddr_axi_rlast  (ddr_rlast),
      .cl_sh_ddr_axi_rvalid (ddr_rvalid),
      .cl_sh_ddr_axi_rready (ddr_rready),

      .sh_ddr_stat_bus_addr (sh_ddr_stat_addr_q),
      .sh_ddr_stat_bus_wdata(sh_ddr_stat_wdata_q),
      .sh_ddr_stat_bus_wr   (sh_ddr_stat_wr_q),
      .sh_ddr_stat_bus_rd   (sh_ddr_stat_rd_q),
      .sh_ddr_stat_bus_ack  (ddr_sh_stat_ack_q),
      .sh_ddr_stat_bus_rdata(ddr_sh_stat_rdata_q),

      .ddr_sh_stat_int   (ddr_sh_stat_int_q),
      .sh_cl_ddr_is_ready(ddr_ready)
  );
endmodule
