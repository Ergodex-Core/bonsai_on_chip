// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// Correctness-first AXI512 line -> native HBM AXI256 bridge. One outstanding
// read and one outstanding write, independently. No throughput claim is made.
//
// Mapping v1: the upstream address is a local byte offset within a reserved
// 512 MiB allocation. Native physical address is (15 << 29) | local_offset.
// Every upper bit and all line attributes are checked BEFORE narrowing; no
// other HBM port may write this allocation. The image ABI stays <=256 MiB.
//
// Either reset input asynchronously asserts BOTH internal endpoint resets;
// release is synchronized independently. The caller MUST also reset/quiesce
// the upstream clients and HBM controller together, discard any prior image
// seal/epoch, and reload/verify/reseal. These reset inputs are whole-store reset,
// NOT MMCM-lock-derived resets. Controller reset/lock loss instead drops
// controller_ready_i (even when clk_hbm_i stops) and latches a fatal fault.
module hbm_line_bridge (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic clk_hbm_i,
    input  logic rst_hbm_ni,
    input  logic controller_ready_i,
    output logic ready_o,
    output logic fault_o,

    input logic [15:0] s_axi_awid,
    input logic [63:0] s_axi_awaddr,
    input logic [7:0] s_axi_awlen,
    input logic [2:0] s_axi_awsize,
    input logic [1:0] s_axi_awburst,
    input logic s_axi_awvalid,
    output logic s_axi_awready,
    input logic [511:0] s_axi_wdata,
    input logic [63:0] s_axi_wstrb,
    input logic s_axi_wlast,
    input logic s_axi_wvalid,
    output logic s_axi_wready,
    output logic [15:0] s_axi_bid,
    output logic [1:0] s_axi_bresp,
    output logic s_axi_bvalid,
    input logic s_axi_bready,
    input logic [15:0] s_axi_arid,
    input logic [63:0] s_axi_araddr,
    input logic [7:0] s_axi_arlen,
    input logic [2:0] s_axi_arsize,
    input logic [1:0] s_axi_arburst,
    input logic s_axi_arvalid,
    output logic s_axi_arready,
    output logic [15:0] s_axi_rid,
    output logic [511:0] s_axi_rdata,
    output logic [1:0] s_axi_rresp,
    output logic s_axi_rlast,
    output logic s_axi_rvalid,
    input logic s_axi_rready,

    output logic [5:0] m_axi_awid,
    output logic [33:0] m_axi_awaddr,
    output logic [3:0] m_axi_awlen,
    output logic [2:0] m_axi_awsize,
    output logic [1:0] m_axi_awburst,
    output logic m_axi_awvalid,
    input logic m_axi_awready,
    output logic [255:0] m_axi_wdata,
    output logic [31:0] m_axi_wstrb,
    output logic m_axi_wlast,
    output logic m_axi_wvalid,
    input logic m_axi_wready,
    input logic [5:0] m_axi_bid,
    input logic [1:0] m_axi_bresp,
    input logic m_axi_bvalid,
    output logic m_axi_bready,
    output logic [5:0] m_axi_arid,
    output logic [33:0] m_axi_araddr,
    output logic [3:0] m_axi_arlen,
    output logic [2:0] m_axi_arsize,
    output logic [1:0] m_axi_arburst,
    output logic m_axi_arvalid,
    input logic m_axi_arready,
    input logic [5:0] m_axi_rid,
    input logic [255:0] m_axi_rdata,
    input logic [1:0] m_axi_rresp,
    input logic m_axi_rlast,
    input logic m_axi_rvalid,
    output logic m_axi_rready
);
  localparam logic [1:0] OKAY = 2'b00, SLVERR = 2'b10, DECERR = 2'b11;
  // Reset synchronizers intentionally drive both asynchronous reset pins and
  // synchronous handshake qualification; deassertion has already been synced.
  /* verilator lint_off SYNCASYNCNET */
  logic paired_reset_n, core_reset_n, hbm_reset_n;
  (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *)logic [1:0] core_reset_sync_q;
  (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *)logic [1:0] hbm_reset_sync_q;
  /* verilator lint_on SYNCASYNCNET */
  assign paired_reset_n = rst_ni && rst_hbm_ni;
  assign core_reset_n = core_reset_sync_q[1];
  assign hbm_reset_n = hbm_reset_sync_q[1];
  always_ff @(posedge clk_i or negedge paired_reset_n) begin
    if (!paired_reset_n) core_reset_sync_q <= '0;
    else core_reset_sync_q <= {core_reset_sync_q[0], 1'b1};
  end
  always_ff @(posedge clk_hbm_i or negedge paired_reset_n) begin
    if (!paired_reset_n) hbm_reset_sync_q <= '0;
    else hbm_reset_sync_q <= {hbm_reset_sync_q[0], 1'b1};
  end

  logic fault_hbm_q, ready_seen_q, core_ready_seen_q, core_fault_q, core_fault_now;
  (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *)logic [1:0] ready_sync_q;
  (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *)logic [1:0] fault_sync_q;
  (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *)logic [1:0] core_fault_sync_q;
  always_ff @(posedge clk_i or negedge core_reset_n) begin
    if (!core_reset_n) begin
      ready_sync_q <= '0;
      fault_sync_q <= '0;
      core_ready_seen_q <= 0;
      core_fault_q <= 0;
    end else begin
      ready_sync_q <= {ready_sync_q[0], controller_ready_i && hbm_reset_n};
      fault_sync_q <= {fault_sync_q[0], fault_hbm_q};
      if (ready_sync_q[1]) core_ready_seen_q <= 1;
      if (core_fault_now) core_fault_q <= 1;
    end
  end
  // The source domain must retire its accepted AXI commands even if the HBM
  // clock has stopped. No HBM-clocked fault flag or acknowledgement is needed.
  assign core_fault_now = fault_sync_q[1] || (core_ready_seen_q && !ready_sync_q[1]);
  assign fault_o = core_fault_q || core_fault_now;
  assign ready_o = core_reset_n && ready_sync_q[1] && !fault_o;

  function automatic logic legal_line(input logic [63:0] addr, input logic [7:0] len,
                                      input logic [2:0] size, input logic [1:0] burst);
    // With 64B alignment this also bounds the complete line and prevents a
    // 4KiB crossing. Bit28 is local address; bits29..63 must all be zero.
    return addr < 64'h20000000 && addr[5:0] == 0 && len == 0 && size == 3'd6 && burst == 2'b01;
  endfunction

  typedef enum logic [2:0] {
    CW_COLLECT,
    CW_DRAIN,
    CW_SEND,
    CW_WAIT,
    CW_REPLY
  } cwstate_t;
  typedef enum logic [1:0] {
    CR_IDLE,
    CR_SEND,
    CR_WAIT,
    CR_REPLY
  } crstate_t;
  cwstate_t cwstate;
  crstate_t crstate;
  logic aw_seen_q, w_seen_q, aw_legal_q, wlast_q;
  logic [15:0] write_id_q, read_id_q;
  logic [28:0] write_addr_q, read_addr_q;
  logic [511:0] write_data_q, read_data_q;
  logic [63:0] write_strb_q;
  logic [1:0] write_resp_q, read_resp_q;
  logic wcmd_ready, wcmd_valid, wcmd_accept;
  logic [604:0] wcmd_data;
  logic wrsp_ready, wrsp_valid;
  logic [1:0] wrsp_data;
  logic rcmd_ready, rcmd_valid, rcmd_accept;
  logic [28:0] rcmd_data;
  logic rrsp_ready, rrsp_valid;
  logic [513:0] rrsp_data;

  assign s_axi_awready = core_reset_n && cwstate == CW_COLLECT && !aw_seen_q;
  // The first W may precede AW. Invalid multi-beat writes are drained through
  // WLAST without issuing any native HBM writes. A missing WLAST deliberately
  // blocks this write ID until paired reset rather than mistaking a late beat
  // for the next command.
  assign s_axi_wready = core_reset_n &&
      ((cwstate == CW_COLLECT && !w_seen_q) || cwstate == CW_DRAIN);
  assign s_axi_bid = write_id_q;
  assign s_axi_bresp = write_resp_q;
  assign s_axi_bvalid = core_reset_n && cwstate == CW_REPLY;
  assign s_axi_arready = core_reset_n && crstate == CR_IDLE;
  assign s_axi_rid = read_id_q;
  assign s_axi_rdata = read_data_q;
  assign s_axi_rresp = read_resp_q;
  assign s_axi_rlast = 1'b1;
  assign s_axi_rvalid = core_reset_n && crstate == CR_REPLY;

  always_ff @(posedge clk_i or negedge core_reset_n) begin
    if (!core_reset_n) begin
      cwstate <= CW_COLLECT;
      aw_seen_q <= 0;
      w_seen_q <= 0;
      aw_legal_q <= 0;
      wlast_q <= 0;
      write_id_q <= 0;
      write_addr_q <= 0;
      write_data_q <= 0;
      write_strb_q <= 0;
      write_resp_q <= OKAY;
      crstate <= CR_IDLE;
      read_id_q <= 0;
      read_addr_q <= 0;
      read_data_q <= 0;
      read_resp_q <= OKAY;
    end else begin
      case (cwstate)
        CW_COLLECT: begin
          if (s_axi_awvalid && s_axi_awready) begin
            aw_seen_q <= 1;
            aw_legal_q <= legal_line(s_axi_awaddr, s_axi_awlen, s_axi_awsize, s_axi_awburst);
            write_addr_q <= s_axi_awaddr[28:0];
            write_id_q <= s_axi_awid;
          end
          if (s_axi_wvalid && s_axi_wready) begin
            w_seen_q <= 1;
            write_data_q <= s_axi_wdata;
            write_strb_q <= s_axi_wstrb;
            wlast_q <= s_axi_wlast;
          end
          if (aw_seen_q && w_seen_q) begin
            write_resp_q <= !aw_legal_q ? DECERR : SLVERR;
            if (!wlast_q) cwstate <= CW_DRAIN;
            else if (!aw_legal_q || !ready_o) cwstate <= CW_REPLY;
            else cwstate <= CW_SEND;
          end
        end
        CW_DRAIN: if (s_axi_wvalid && s_axi_wlast) cwstate <= CW_REPLY;
        CW_SEND:  if (wcmd_ready) cwstate <= CW_WAIT;
        CW_WAIT:
        if (wrsp_valid) begin
          write_resp_q <= wrsp_data;
          cwstate <= CW_REPLY;
        end
        CW_REPLY:
        if (s_axi_bready) begin
          aw_seen_q <= 0;
          w_seen_q  <= 0;
          cwstate   <= CW_COLLECT;
        end
        default:  cwstate <= CW_COLLECT;
      endcase
      case (crstate)
        CR_IDLE:
        if (s_axi_arvalid) begin
          read_id_q <= s_axi_arid;
          read_addr_q <= s_axi_araddr[28:0];
          read_data_q <= '0;
          read_resp_q <= !legal_line(
              s_axi_araddr, s_axi_arlen, s_axi_arsize, s_axi_arburst
          ) ? DECERR : SLVERR;
          crstate <= ready_o && legal_line(
              s_axi_araddr, s_axi_arlen, s_axi_arsize, s_axi_arburst
          ) ? CR_SEND : CR_REPLY;
        end
        CR_SEND:  if (rcmd_ready) crstate <= CR_WAIT;
        CR_WAIT:
        if (rrsp_valid) begin
          read_data_q <= rrsp_data[511:0];
          read_resp_q <= rrsp_data[513:512];
          crstate <= CR_REPLY;
        end
        CR_REPLY: if (s_axi_rready) crstate <= CR_IDLE;
        default:  crstate <= CR_IDLE;
      endcase
      // Preserve responses already held under backpressure. For unfinished
      // commands, terminate upstream once and leave the native command/ID in
      // its drain state until global reset. Late mailbox replies are discarded.
      if (fault_o) begin
        if (cwstate == CW_SEND || cwstate == CW_WAIT) begin
          write_resp_q <= SLVERR;
          cwstate <= CW_REPLY;
        end
        if (crstate == CR_SEND || crstate == CR_WAIT) begin
          read_resp_q <= SLVERR;
          read_data_q <= '0;
          crstate <= CR_REPLY;
        end
      end
    end
  end

  hbm_cdc_mailbox #(
      .WIDTH(605)
  ) write_command (
      .src_clk_i  (clk_i),
      .src_rst_ni (core_reset_n),
      .src_valid_i(cwstate == CW_SEND && !fault_o),
      .src_ready_o(wcmd_ready),
      .src_data_i ({write_addr_q, write_strb_q, write_data_q}),
      .dst_clk_i  (clk_hbm_i),
      .dst_rst_ni (hbm_reset_n),
      .dst_valid_o(wcmd_valid),
      .dst_ready_i(wcmd_accept),
      .dst_data_o (wcmd_data)
  );
  hbm_cdc_mailbox #(
      .WIDTH(29)
  ) read_command (
      .src_clk_i  (clk_i),
      .src_rst_ni (core_reset_n),
      .src_valid_i(crstate == CR_SEND && !fault_o),
      .src_ready_o(rcmd_ready),
      .src_data_i (read_addr_q),
      .dst_clk_i  (clk_hbm_i),
      .dst_rst_ni (hbm_reset_n),
      .dst_valid_o(rcmd_valid),
      .dst_ready_i(rcmd_accept),
      .dst_data_o (rcmd_data)
  );

  typedef enum logic [1:0] {
    HW_IDLE,
    HW_SEND,
    HW_WAIT,
    HW_REPLY
  } hwstate_t;
  typedef enum logic [2:0] {
    HR_IDLE,
    HR_ADDR,
    HR_FIRST,
    HR_SECOND,
    HR_DRAIN,
    HR_REPLY
  } hrstate_t;
  hwstate_t hwstate;
  hrstate_t hrstate;
  logic [28:0] hwaddr_q, hraddr_q;
  logic [511:0] hwdata_q, hrdata_q;
  logic [63:0] hwstrb_q;
  logic hw_aw_pending_q, hw_data_pending_q, hw_second_q;
  logic [1:0] hwresp_q;
  logic hrerror_q, response_fault;

  assign wcmd_accept = hwstate == HW_IDLE;
  assign rcmd_accept = hrstate == HR_IDLE;
  assign m_axi_awid = 6'd0;
  assign m_axi_awaddr = {5'd15, hwaddr_q};
  assign m_axi_awlen = 4'd1;
  assign m_axi_awsize = 3'd5;
  assign m_axi_awburst = 2'b01;
  assign m_axi_awvalid = hbm_reset_n && hwstate == HW_SEND && hw_aw_pending_q;
  assign m_axi_wdata = hw_second_q ? hwdata_q[511:256] : hwdata_q[255:0];
  assign m_axi_wstrb = hw_second_q ? hwstrb_q[63:32] : hwstrb_q[31:0];
  assign m_axi_wlast = hw_second_q;
  assign m_axi_wvalid = hbm_reset_n && hwstate == HW_SEND && hw_data_pending_q;
  // Also drain unsolicited/late responses. A fault permanently prevents ID
  // reuse; the existing accepted command still finishes/drains if possible.
  assign m_axi_bready = 1'b1;
  assign m_axi_arid = 6'd0;
  assign m_axi_araddr = {5'd15, hraddr_q};
  assign m_axi_arlen = 4'd1;
  assign m_axi_arsize = 3'd5;
  assign m_axi_arburst = 2'b01;
  assign m_axi_arvalid = hbm_reset_n && hrstate == HR_ADDR;
  assign m_axi_rready = 1'b1;

  always_comb begin
    response_fault = 1'b0;
    if (m_axi_bvalid && (hwstate != HW_WAIT || m_axi_bid != 0 || m_axi_bresp != OKAY))
      response_fault = 1'b1;
    if (m_axi_rvalid) begin
      if (m_axi_rid != 0 || m_axi_rresp != OKAY) response_fault = 1'b1;
      case (hrstate)
        HR_FIRST:  if (m_axi_rlast) response_fault = 1'b1;
        HR_SECOND: if (!m_axi_rlast) response_fault = 1'b1;
        HR_DRAIN:  response_fault = 1'b1;
        default:   response_fault = 1'b1;
      endcase
    end
  end

  always_ff @(posedge clk_hbm_i or negedge hbm_reset_n) begin
    if (!hbm_reset_n) begin
      ready_seen_q <= 0;
      fault_hbm_q <= 0;
      core_fault_sync_q <= 0;
      hwstate <= HW_IDLE;
      hwaddr_q <= 0;
      hwdata_q <= 0;
      hwstrb_q <= 0;
      hw_aw_pending_q <= 0;
      hw_data_pending_q <= 0;
      hw_second_q <= 0;
      hwresp_q <= OKAY;
      hrstate <= HR_IDLE;
      hraddr_q <= 0;
      hrdata_q <= 0;
      hrerror_q <= 0;
    end else begin
      if (controller_ready_i) ready_seen_q <= 1;
      core_fault_sync_q <= {core_fault_sync_q[0], fault_o};
      if ((ready_seen_q && !controller_ready_i) || response_fault || core_fault_sync_q[1])
        fault_hbm_q <= 1;
      case (hwstate)
        HW_IDLE:
        if (wcmd_valid) begin
          hwaddr_q <= wcmd_data[604:576];
          hwstrb_q <= wcmd_data[575:512];
          hwdata_q <= wcmd_data[511:0];
          hw_aw_pending_q <= 1;
          hw_data_pending_q <= 1;
          hw_second_q <= 0;
          hwresp_q <= SLVERR;
          hwstate <= controller_ready_i && !fault_hbm_q && !core_fault_sync_q[1] &&
              !response_fault ? HW_SEND : HW_REPLY;
        end
        HW_SEND: begin
          if (m_axi_awvalid && m_axi_awready) hw_aw_pending_q <= 0;
          if (m_axi_wvalid && m_axi_wready) begin
            if (hw_second_q) hw_data_pending_q <= 0;
            else hw_second_q <= 1;
          end
          if ((!hw_aw_pending_q || m_axi_awready) &&
              (!hw_data_pending_q || (hw_second_q && m_axi_wready)))
            hwstate <= HW_WAIT;
        end
        HW_WAIT:
        if (m_axi_bvalid) begin
          hwresp_q <= !fault_hbm_q && !response_fault && controller_ready_i && m_axi_bid == 0 &&
              m_axi_bresp == OKAY ? OKAY : SLVERR;
          hwstate <= HW_REPLY;
        end
        HW_REPLY: if (wrsp_ready) hwstate <= HW_IDLE;
        default:  hwstate <= HW_IDLE;
      endcase
      case (hrstate)
        HR_IDLE:
        if (rcmd_valid) begin
          hraddr_q <= rcmd_data;
          hrdata_q <= '0;
          hrerror_q <= !controller_ready_i || fault_hbm_q || core_fault_sync_q[1] || response_fault;
          hrstate <= controller_ready_i && !fault_hbm_q && !core_fault_sync_q[1] &&
              !response_fault ? HR_ADDR : HR_REPLY;
        end
        HR_ADDR:  if (m_axi_arready) hrstate <= HR_FIRST;
        HR_FIRST:
        if (m_axi_rvalid) begin
          hrdata_q[255:0] <= m_axi_rdata;
          hrerror_q <= fault_hbm_q || response_fault || !controller_ready_i ||
              m_axi_rid != 0 || m_axi_rresp != OKAY || m_axi_rlast;
          hrstate <= m_axi_rlast ? HR_REPLY : HR_SECOND;
        end
        HR_SECOND:
        if (m_axi_rvalid) begin
          hrdata_q[511:256] <= m_axi_rdata;
          hrerror_q <= hrerror_q || fault_hbm_q || response_fault || !controller_ready_i ||
              m_axi_rid != 0 || m_axi_rresp != OKAY || !m_axi_rlast;
          hrstate <= m_axi_rlast ? HR_REPLY : HR_DRAIN;
        end
        HR_DRAIN: if (m_axi_rvalid && m_axi_rlast) hrstate <= HR_REPLY;
        HR_REPLY: if (rrsp_ready) hrstate <= HR_IDLE;
        default:  hrstate <= HR_IDLE;
      endcase
    end
  end

  hbm_cdc_mailbox #(
      .WIDTH(2)
  ) write_response (
      .src_clk_i  (clk_hbm_i),
      .src_rst_ni (hbm_reset_n),
      .src_valid_i(hwstate == HW_REPLY),
      .src_ready_o(wrsp_ready),
      .src_data_i (hwresp_q),
      .dst_clk_i  (clk_i),
      .dst_rst_ni (core_reset_n),
      .dst_valid_o(wrsp_valid),
      .dst_ready_i(cwstate == CW_WAIT || fault_o),
      .dst_data_o (wrsp_data)
  );
  hbm_cdc_mailbox #(
      .WIDTH(514)
  ) read_response (
      .src_clk_i  (clk_hbm_i),
      .src_rst_ni (hbm_reset_n),
      .src_valid_i(hrstate == HR_REPLY),
      .src_ready_o(rrsp_ready),
      .src_data_i ({hrerror_q ? SLVERR : OKAY, hrerror_q ? 512'd0 : hrdata_q}),
      .dst_clk_i  (clk_i),
      .dst_rst_ni (core_reset_n),
      .dst_valid_o(rrsp_valid),
      .dst_ready_i(crstate == CR_WAIT || fault_o),
      .dst_data_o (rrsp_data)
  );
endmodule
