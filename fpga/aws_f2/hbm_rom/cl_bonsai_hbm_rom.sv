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


// PCIS/OCL shell wiring adapted from the pinned AWS example. Native HBM
// attachment is exclusive to the guarded loader/read path. No scrubber,
// performance mux, debug master, reset CSR or mutable clock/map control exists.
module cl_bonsai_hbm_rom (
    `include "cl_ports.vh"
);
  `include "cl_id_defines.vh"
  logic [15:0] line_awid;
  logic [63:0] line_awaddr;
  logic [7:0] line_awlen;
  logic [2:0] line_awsize;
  logic [1:0] line_awburst;
  logic line_awvalid;
  logic line_awready;
  logic [511:0] line_wdata;
  logic [63:0] line_wstrb;
  logic line_wlast;
  logic line_wvalid;
  logic line_wready;
  logic [15:0] line_bid;
  logic [1:0] line_bresp;
  logic line_bvalid;
  logic line_bready;
  logic [15:0] line_arid;
  logic [63:0] line_araddr;
  logic [7:0] line_arlen;
  logic [2:0] line_arsize;
  logic [1:0] line_arburst;
  logic line_arvalid;
  logic line_arready;
  logic [15:0] line_rid;
  logic [511:0] line_rdata;
  logic [1:0] line_rresp;
  logic line_rlast;
  logic line_rvalid;
  logic line_rready;
  logic loader_enable, loader_idle, backend_ready, loader_write_rejected;
  logic [63:0] image_base, image_bytes;
  logic mem_req_valid, mem_req_ready, mem_rsp_valid, mem_rsp_ready, mem_rsp_error;
  logic [ 63:0] mem_req_addr;
  logic [511:0] mem_rsp_data;
  logic line_ready, hbm_clk, hbm_rst_n, hbm_ref_rst_n, clock_locked;
  logic controller_ready, adapter_fault;
  logic [31:0] hbm_telemetry;
  logic hbm_awvalid, hbm_awready, hbm_wvalid, hbm_wready, hbm_wlast;
  logic hbm_bvalid, hbm_bready, hbm_arvalid, hbm_arready, hbm_rvalid, hbm_rready, hbm_rlast;
  logic [5:0] hbm_awid, hbm_bid, hbm_arid, hbm_rid;
  logic [33:0] hbm_awaddr, hbm_araddr;
  logic [3:0] hbm_awlen, hbm_arlen;
  logic [2:0] hbm_awsize, hbm_arsize;
  logic [1:0] hbm_awburst, hbm_arburst, hbm_bresp, hbm_rresp;
  logic [255:0] hbm_wdata, hbm_rdata;
  logic [31:0] hbm_wstrb;
  assign cl_sh_id0 = `CL_SH_ID0;
  assign cl_sh_id1 = `CL_SH_ID1;
  assign cl_sh_status_vled = {14'd0, backend_ready, line_ready};
  // No in-band reset can unlock sealed memory. Reconfiguration/reset of the
  // entire shell is the coordinated reset boundary for this first slice.
  assign cl_sh_flr_done = 1'b0;
  assign cl_sh_status0 = hbm_telemetry;
  assign cl_sh_status1 = {30'd0, adapter_fault, backend_ready};
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
  assign PCIE_EP_TXP = '0;
  assign PCIE_EP_TXN = '0;
  assign PCIE_RP_PERSTN = '0;
  assign PCIE_RP_TXP = '0;
  assign PCIE_RP_TXN = '0;

  first_slice_top #(
      .BACKEND_ID(3),
      .PHYSICAL_BYTES(64'h20000000)
  ) APP (
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
      .ddr_ready_i(line_ready),
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
      .m_axi_awid(line_awid),
      .m_axi_awaddr(line_awaddr),
      .m_axi_awlen(line_awlen),
      .m_axi_awsize(line_awsize),
      .m_axi_awburst(line_awburst),
      .m_axi_awvalid(line_awvalid),
      .m_axi_awready(line_awready),
      .m_axi_wdata(line_wdata),
      .m_axi_wstrb(line_wstrb),
      .m_axi_wlast(line_wlast),
      .m_axi_wvalid(line_wvalid),
      .m_axi_wready(line_wready),
      .m_axi_bid(line_bid),
      .m_axi_bresp(line_bresp),
      .m_axi_bvalid(line_bvalid),
      .m_axi_bready(line_bready),
      .m_axi_arid(line_arid),
      .m_axi_araddr(line_araddr),
      .m_axi_arlen(line_arlen),
      .m_axi_arsize(line_arsize),
      .m_axi_arburst(line_arburst),
      .m_axi_arvalid(line_arvalid),
      .m_axi_arready(line_arready),
      .m_axi_rid(line_rid),
      .m_axi_rdata(line_rdata),
      .m_axi_rresp(line_rresp),
      .m_axi_rlast(line_rlast),
      .m_axi_rvalid(line_rvalid),
      .m_axi_rready(line_rready)
  );
  assign cl_sh_dma_pcis_ruser = '0;

  // Fixed H2 clock. The name/hierarchy matches the pinned AWS constraint flow.
  // All MMCM AXI-Lite inputs are tied off inside this module.
  hbm_fixed_clock AWS_CLK_GEN (
      .clk_ref_i(clk_hbm_ref),
      .rst_ni(rst_main_n),
      .clk_hbm_o(hbm_clk),
      .rst_hbm_no(hbm_rst_n),
      .rst_ref_no(hbm_ref_rst_n),
      .locked_o(clock_locked)
  );

  hbm_line_bridge HBM_LINES (
      .clk_i(clk_main_a0),
      .rst_ni(rst_main_n),
      .clk_hbm_i(hbm_clk),
      .rst_hbm_ni(rst_main_n),
      .controller_ready_i(controller_ready && clock_locked),
      .ready_o(line_ready),
      .fault_o(adapter_fault),
      .s_axi_awid(line_awid),
      .s_axi_awaddr(line_awaddr),
      .s_axi_awlen(line_awlen),
      .s_axi_awsize(line_awsize),
      .s_axi_awburst(line_awburst),
      .s_axi_awvalid(line_awvalid),
      .s_axi_awready(line_awready),
      .s_axi_wdata(line_wdata),
      .s_axi_wstrb(line_wstrb),
      .s_axi_wlast(line_wlast),
      .s_axi_wvalid(line_wvalid),
      .s_axi_wready(line_wready),
      .s_axi_bid(line_bid),
      .s_axi_bresp(line_bresp),
      .s_axi_bvalid(line_bvalid),
      .s_axi_bready(line_bready),
      .s_axi_arid(line_arid),
      .s_axi_araddr(line_araddr),
      .s_axi_arlen(line_arlen),
      .s_axi_arsize(line_arsize),
      .s_axi_arburst(line_arburst),
      .s_axi_arvalid(line_arvalid),
      .s_axi_arready(line_arready),
      .s_axi_rid(line_rid),
      .s_axi_rdata(line_rdata),
      .s_axi_rresp(line_rresp),
      .s_axi_rlast(line_rlast),
      .s_axi_rvalid(line_rvalid),
      .s_axi_rready(line_rready),
      .m_axi_awid(hbm_awid),
      .m_axi_awaddr(hbm_awaddr),
      .m_axi_awlen(hbm_awlen),
      .m_axi_awsize(hbm_awsize),
      .m_axi_awburst(hbm_awburst),
      .m_axi_awvalid(hbm_awvalid),
      .m_axi_awready(hbm_awready),
      .m_axi_wdata(hbm_wdata),
      .m_axi_wstrb(hbm_wstrb),
      .m_axi_wlast(hbm_wlast),
      .m_axi_wvalid(hbm_wvalid),
      .m_axi_wready(hbm_wready),
      .m_axi_bid(hbm_bid),
      .m_axi_bresp(hbm_bresp),
      .m_axi_bvalid(hbm_bvalid),
      .m_axi_bready(hbm_bready),
      .m_axi_arid(hbm_arid),
      .m_axi_araddr(hbm_araddr),
      .m_axi_arlen(hbm_arlen),
      .m_axi_arsize(hbm_arsize),
      .m_axi_arburst(hbm_arburst),
      .m_axi_arvalid(hbm_arvalid),
      .m_axi_arready(hbm_arready),
      .m_axi_rid(hbm_rid),
      .m_axi_rdata(hbm_rdata),
      .m_axi_rresp(hbm_rresp),
      .m_axi_rlast(hbm_rlast),
      .m_axi_rvalid(hbm_rvalid),
      .m_axi_rready(hbm_rready)
  );

  hbm_rom_controller HBM_CONTROLLER (
      .clk_ref_i(clk_hbm_ref),
      .rst_ref_ni(hbm_ref_rst_n),
      .clk_hbm_i(hbm_clk),
      .rst_hbm_ni(hbm_rst_n),
      .clk_main_i(clk_main_a0),
      .rst_main_ni(rst_main_n),
      .clock_locked_i(clock_locked),
      .ready_o(controller_ready),
      .telemetry_o(hbm_telemetry),
      .s_axi_awid(hbm_awid),
      .s_axi_awaddr(hbm_awaddr),
      .s_axi_awlen(hbm_awlen),
      .s_axi_awsize(hbm_awsize),
      .s_axi_awburst(hbm_awburst),
      .s_axi_awvalid(hbm_awvalid),
      .s_axi_awready(hbm_awready),
      .s_axi_wdata(hbm_wdata),
      .s_axi_wstrb(hbm_wstrb),
      .s_axi_wlast(hbm_wlast),
      .s_axi_wvalid(hbm_wvalid),
      .s_axi_wready(hbm_wready),
      .s_axi_bid(hbm_bid),
      .s_axi_bresp(hbm_bresp),
      .s_axi_bvalid(hbm_bvalid),
      .s_axi_bready(hbm_bready),
      .s_axi_arid(hbm_arid),
      .s_axi_araddr(hbm_araddr),
      .s_axi_arlen(hbm_arlen),
      .s_axi_arsize(hbm_arsize),
      .s_axi_arburst(hbm_arburst),
      .s_axi_arvalid(hbm_arvalid),
      .s_axi_arready(hbm_arready),
      .s_axi_rid(hbm_rid),
      .s_axi_rdata(hbm_rdata),
      .s_axi_rresp(hbm_rresp),
      .s_axi_rlast(hbm_rlast),
      .s_axi_rvalid(hbm_rvalid),
      .s_axi_rready(hbm_rready),
      .hbm_apb_preset_n_0(hbm_apb_preset_n_0),
      .hbm_apb_paddr_0(hbm_apb_paddr_0),
      .hbm_apb_pprot_0(hbm_apb_pprot_0),
      .hbm_apb_psel_0(hbm_apb_psel_0),
      .hbm_apb_penable_0(hbm_apb_penable_0),
      .hbm_apb_pwrite_0(hbm_apb_pwrite_0),
      .hbm_apb_pwdata_0(hbm_apb_pwdata_0),
      .hbm_apb_pstrb_0(hbm_apb_pstrb_0),
      .hbm_apb_pready_0(hbm_apb_pready_0),
      .hbm_apb_prdata_0(hbm_apb_prdata_0),
      .hbm_apb_pslverr_0(hbm_apb_pslverr_0),
      .hbm_apb_preset_n_1(hbm_apb_preset_n_1),
      .hbm_apb_paddr_1(hbm_apb_paddr_1),
      .hbm_apb_pprot_1(hbm_apb_pprot_1),
      .hbm_apb_psel_1(hbm_apb_psel_1),
      .hbm_apb_penable_1(hbm_apb_penable_1),
      .hbm_apb_pwrite_1(hbm_apb_pwrite_1),
      .hbm_apb_pwdata_1(hbm_apb_pwdata_1),
      .hbm_apb_pstrb_1(hbm_apb_pstrb_1),
      .hbm_apb_pready_1(hbm_apb_pready_1),
      .hbm_apb_prdata_1(hbm_apb_prdata_1),
      .hbm_apb_pslverr_1(hbm_apb_pslverr_1)
  );

  sh_ddr #(
      .DDR_PRESENT(0)
  ) SH_DDR (
      .clk       (clk_main_a0),
      .rst_n     (rst_main_n),
      .stat_clk  (clk_main_a0),
      .stat_rst_n(rst_main_n),

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

      .cl_sh_ddr_axi_awid   ('0),
      .cl_sh_ddr_axi_awaddr ('0),
      .cl_sh_ddr_axi_awlen  ('0),
      .cl_sh_ddr_axi_awsize ('0),
      .cl_sh_ddr_axi_awvalid('0),
      .cl_sh_ddr_axi_awburst('0),
      .cl_sh_ddr_axi_awuser (1'd0),
      .cl_sh_ddr_axi_awready(),
      .cl_sh_ddr_axi_wdata  ('0),
      .cl_sh_ddr_axi_wstrb  ('0),
      .cl_sh_ddr_axi_wlast  ('0),
      .cl_sh_ddr_axi_wvalid ('0),
      .cl_sh_ddr_axi_wready (),
      .cl_sh_ddr_axi_bid    (),
      .cl_sh_ddr_axi_bresp  (),
      .cl_sh_ddr_axi_bvalid (),
      .cl_sh_ddr_axi_bready ('0),
      .cl_sh_ddr_axi_arid   ('0),
      .cl_sh_ddr_axi_araddr ('0),
      .cl_sh_ddr_axi_arlen  ('0),
      .cl_sh_ddr_axi_arsize ('0),
      .cl_sh_ddr_axi_arvalid('0),
      .cl_sh_ddr_axi_arburst('0),
      .cl_sh_ddr_axi_aruser (1'd0),
      .cl_sh_ddr_axi_arready(),
      .cl_sh_ddr_axi_rid    (),
      .cl_sh_ddr_axi_rdata  (),
      .cl_sh_ddr_axi_rresp  (),
      .cl_sh_ddr_axi_rlast  (),
      .cl_sh_ddr_axi_rvalid (),
      .cl_sh_ddr_axi_rready ('0),

      .sh_ddr_stat_bus_addr ('0),
      .sh_ddr_stat_bus_wdata('0),
      .sh_ddr_stat_bus_wr   ('0),
      .sh_ddr_stat_bus_rd   ('0),
      .sh_ddr_stat_bus_ack  (cl_sh_ddr_stat_ack),
      .sh_ddr_stat_bus_rdata(cl_sh_ddr_stat_rdata),

      .ddr_sh_stat_int   (cl_sh_ddr_stat_int),
      .sh_cl_ddr_is_ready()
  );
endmodule
