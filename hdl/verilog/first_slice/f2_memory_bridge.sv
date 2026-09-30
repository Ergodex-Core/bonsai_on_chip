// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// F2 PCIS loader/readback and read-only line client to DDR AXI512.
// Deliberately serialized: each PCIS beat becomes one aligned DDR transaction.
// This is a correctness bridge, not a bandwidth or AXI throughput benchmark.
module f2_memory_bridge (
    input logic clk_i,
    rst_ni,
    input logic ddr_ready_i,
    loader_enable_i,
    input logic [63:0] image_base_i,
    image_bytes_i,
    output logic loader_idle_o,
    backend_ready_o,
    loader_write_rejected_o,
    input logic mem_req_valid_i,
    output logic mem_req_ready_o,
    input logic [63:0] mem_req_addr_i,
    output logic mem_rsp_valid_o,
    input logic mem_rsp_ready_i,
    output logic [511:0] mem_rsp_data_o,
    output logic mem_rsp_error_o,

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
    s_axi_wvalid,
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
    s_axi_rvalid,
    input logic s_axi_rready,

    output logic [15:0] m_axi_awid,
    output logic [63:0] m_axi_awaddr,
    output logic [7:0] m_axi_awlen,
    output logic [2:0] m_axi_awsize,
    output logic [1:0] m_axi_awburst,
    output logic m_axi_awvalid,
    input logic m_axi_awready,
    output logic [511:0] m_axi_wdata,
    output logic [63:0] m_axi_wstrb,
    output logic m_axi_wlast,
    m_axi_wvalid,
    input logic m_axi_wready,
    input logic [15:0] m_axi_bid,
    input logic [1:0] m_axi_bresp,
    input logic m_axi_bvalid,
    output logic m_axi_bready,
    output logic [15:0] m_axi_arid,
    output logic [63:0] m_axi_araddr,
    output logic [7:0] m_axi_arlen,
    output logic [2:0] m_axi_arsize,
    output logic [1:0] m_axi_arburst,
    output logic m_axi_arvalid,
    input logic m_axi_arready,
    input logic [15:0] m_axi_rid,
    input logic [511:0] m_axi_rdata,
    input logic [1:0] m_axi_rresp,
    input logic m_axi_rlast,
    m_axi_rvalid,
    output logic m_axi_rready
);
  // PCIS preserves byte addresses even with AxSIZE=6 (pinned AWS sh_bfm).
  // An unaligned first beat ends at its aligned transfer boundary; subsequent
  // INCR beats are aligned. Validate that complete envelope without wraparound.
  function automatic logic legal_burst(input logic [63:0] addr, input logic [7:0] len,
                                       input logic [2:0] size, input logic [1:0] burst);
    logic [64:0] span, end_addr, image_end, alignment_mask, aligned_addr;
    span = ({57'd0, len} + 65'd1) << size;
    image_end = {1'b0, image_base_i} + {1'b0, image_bytes_i};
    alignment_mask = (65'd1 << size) - 65'd1;
    aligned_addr = {1'b0, addr} & ~alignment_mask;
    end_addr = aligned_addr + span;
    return size <= 3'd6 && burst == 2'b01 &&
      image_base_i[5:0] == 0 && image_bytes_i[5:0] == 0 &&
      image_bytes_i >= 64 && !image_end[64] && !end_addr[64] &&
      aligned_addr >= {1'b0, image_base_i} && end_addr <= image_end &&
      image_end <= 65'h400000000 &&
      ({53'd0, aligned_addr[11:0]} + span) <= 65'd4096;
  endfunction

  function automatic logic [63:0] lane_mask(input logic [5:0] lane, input logic [2:0] size);
    logic [63:0] mask, aligned_lane;
    mask = size == 6 ? 64'hffffffffffffffff : (64'd1 << (7'd1 << size)) - 64'd1;
    aligned_lane = {58'd0, lane} & ~((64'd1 << size) - 64'd1);
    return (mask << aligned_lane) & (64'hffffffffffffffff << lane);
  endfunction

  function automatic logic [63:0] next_beat(input logic [63:0] addr, input logic [2:0] size);
    return (addr & ~((64'd1 << size) - 64'd1)) + (64'd1 << size);
  endfunction

  function automatic logic legal_line(input logic [63:0] addr);
    return addr[5:0] == 0 && legal_burst(addr, 0, 6, 1);
  endfunction

  typedef enum logic [2:0] {
    W_IDLE,
    W_DATA,
    W_SEND,
    W_DDR_RESP,
    W_RESP
  } wstate_t;
  wstate_t ws;
  logic [63:0] wa;
  logic [7:0] wleft;
  logic [2:0] wsize;
  logic [15:0] wid;
  logic wpermit, werror, aw_pending, w_pending;
  logic [511:0] wd;
  logic [63:0] wstrb;
  logic protocol_fault;

  assign backend_ready_o = ddr_ready_i && !protocol_fault;
  // AWVALID also closes the same-cycle admission/drain observation race.
  assign loader_idle_o = ws == W_IDLE && !s_axi_awvalid;
  assign s_axi_awready = ws == W_IDLE;
  assign s_axi_wready = ws == W_DATA;
  assign s_axi_bid = wid;
  assign s_axi_bresp = werror ? 2'b10 : 2'b00;
  assign s_axi_bvalid = ws == W_RESP;
  assign m_axi_awid = 16'd0;
  assign m_axi_awaddr = {wa[63:6], 6'd0};
  assign m_axi_awlen = 8'd0;
  assign m_axi_awsize = 3'd6;
  assign m_axi_awburst = 2'b01;
  assign m_axi_awvalid = ws == W_SEND && aw_pending;
  assign m_axi_wdata = wd;
  assign m_axi_wstrb = wstrb;
  assign m_axi_wlast = 1'b1;
  assign m_axi_wvalid = ws == W_SEND && w_pending;
  assign m_axi_bready = ws == W_DDR_RESP;

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      ws <= W_IDLE;
      wa <= 0;
      wleft <= 0;
      wsize <= 0;
      wid <= 0;
      wpermit <= 0;
      werror <= 0;
      aw_pending <= 0;
      w_pending <= 0;
      wd <= 0;
      wstrb <= 0;
      loader_write_rejected_o <= 0;
    end else begin
      loader_write_rejected_o <= 0;
      case (ws)
        W_IDLE:
        if (s_axi_awvalid) begin
          wa <= s_axi_awaddr;
          wleft <= s_axi_awlen;
          wsize <= s_axi_awsize;
          wid <= s_axi_awid;
          wpermit <= loader_enable_i && backend_ready_o && legal_burst(
              s_axi_awaddr, s_axi_awlen, s_axi_awsize, s_axi_awburst
          );
          werror <= !(loader_enable_i && backend_ready_o && legal_burst(
              s_axi_awaddr, s_axi_awlen, s_axi_awsize, s_axi_awburst
          ));
          loader_write_rejected_o <= !(loader_enable_i && backend_ready_o && legal_burst(
              s_axi_awaddr, s_axi_awlen, s_axi_awsize, s_axi_awburst
          ));
          ws <= W_DATA;
        end
        W_DATA:
        if (s_axi_wvalid) begin
          // Illegal byte strobes and WLAST cannot escape the admitted range.
          // Malformed transactions are failed, never "repaired" with host data.
          if (s_axi_wlast != (wleft == 0) || (s_axi_wstrb & ~lane_mask(wa[5:0], wsize)) != 0) begin
            werror <= 1;
          end
          if (wpermit && s_axi_wlast == (wleft == 0) && (s_axi_wstrb & ~lane_mask(
                  wa[5:0], wsize
              )) == 0) begin
            wd <= s_axi_wdata;
            wstrb <= s_axi_wstrb;
            aw_pending <= 1;
            w_pending <= 1;
            ws <= W_SEND;
          end else if (wleft == 0) begin
            ws <= W_RESP;
          end else begin
            wleft <= wleft - 8'd1;
            wa <= next_beat(wa, wsize);
          end
        end
        W_SEND: begin
          if (m_axi_awready) aw_pending <= 0;
          if (m_axi_wready) w_pending <= 0;
          if ((!aw_pending || m_axi_awready) && (!w_pending || m_axi_wready)) ws <= W_DDR_RESP;
        end
        W_DDR_RESP:
        if (m_axi_bvalid) begin
          if (m_axi_bresp != 0 || m_axi_bid != 0) werror <= 1;
          if (wleft == 0) ws <= W_RESP;
          else begin
            wleft <= wleft - 8'd1;
            wa <= next_beat(wa, wsize);
            ws <= W_DATA;
          end
        end
        W_RESP:  if (s_axi_bready) ws <= W_IDLE;
        default: ws <= W_IDLE;
      endcase
    end
  end

  typedef enum logic [2:0] {
    R_IDLE,
    R_ADDR,
    R_DATA,
    R_DRAIN,
    R_RESP
  } rstate_t;
  rstate_t rs;
  logic read_engine, prefer_engine;
  logic [ 63:0] ra;
  logic [  7:0] rleft;
  logic [  2:0] rsize;
  logic [ 15:0] rid;
  logic [511:0] rd;
  logic rerror, rpermit;
  logic take_engine;
  assign take_engine = mem_req_valid_i && (!s_axi_arvalid || prefer_engine);
  assign mem_req_ready_o = rs == R_IDLE && take_engine;
  assign s_axi_arready = rs == R_IDLE && !take_engine;
  assign mem_rsp_valid_o = rs == R_RESP && read_engine;
  assign mem_rsp_data_o = rd;
  assign mem_rsp_error_o = rerror;
  assign s_axi_rvalid = rs == R_RESP && !read_engine;
  assign s_axi_rdata = rd;
  assign s_axi_rresp = rerror ? 2'b10 : 2'b00;
  assign s_axi_rid = rid;
  assign s_axi_rlast = rleft == 0;
  assign m_axi_arid = 16'd0;
  assign m_axi_araddr = {ra[63:6], 6'd0};
  assign m_axi_arlen = 8'd0;
  assign m_axi_arsize = 3'd6;
  assign m_axi_arburst = 2'b01;
  assign m_axi_arvalid = rs == R_ADDR;
  assign m_axi_rready = rs == R_DATA || rs == R_DRAIN;

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      rs <= R_IDLE;
      read_engine <= 0;
      prefer_engine <= 1;
      ra <= 0;
      rleft <= 0;
      rsize <= 0;
      rid <= 0;
      rd <= 0;
      rerror <= 0;
      rpermit <= 0;
      protocol_fault <= 0;
    end else begin
      // DDR errors may be invisible through host MMIO even when returned bytes
      // happen to match. Any error response or wrong ID fails closed until
      // a coordinated shell reset. Missing RLAST stays in R_DRAIN indefinitely:
      // host timeout is a failed job, never permission to reuse this DDR ID.
      if (ws == W_DDR_RESP && m_axi_bvalid && (m_axi_bid != 0 || m_axi_bresp != 0))
        protocol_fault <= 1;
      case (rs)
        R_IDLE: begin
          if (take_engine) begin
            read_engine <= 1;
            prefer_engine <= 0;
            ra <= mem_req_addr_i;
            rleft <= 0;
            rsize <= 6;
            rid <= 0;
            rpermit <= backend_ready_o && legal_line(mem_req_addr_i);
            rerror <= !(backend_ready_o && legal_line(mem_req_addr_i));
            rd <= 0;
            rs <= backend_ready_o && legal_line(mem_req_addr_i) ? R_ADDR : R_RESP;
          end else if (s_axi_arvalid) begin
            read_engine <= 0;
            prefer_engine <= 1;
            ra <= s_axi_araddr;
            rleft <= s_axi_arlen;
            rsize <= s_axi_arsize;
            rid <= s_axi_arid;
            rd <= 0;
            rpermit <= backend_ready_o && legal_burst(
                s_axi_araddr, s_axi_arlen, s_axi_arsize, s_axi_arburst
            );
            rerror <= !(backend_ready_o && legal_burst(
                s_axi_araddr, s_axi_arlen, s_axi_arsize, s_axi_arburst
            ));
            rs <= backend_ready_o && legal_burst(
                s_axi_araddr, s_axi_arlen, s_axi_arsize, s_axi_arburst
            ) ? R_ADDR : R_RESP;
          end
        end
        R_ADDR:  if (m_axi_arready) rs <= R_DATA;
        R_DATA:
        if (m_axi_rvalid) begin
          rerror <= m_axi_rresp != 0 || m_axi_rid != 0 || !m_axi_rlast;
          rd <= m_axi_rresp == 0 && m_axi_rid == 0 && m_axi_rlast ? m_axi_rdata : 512'd0;
          if (m_axi_rid != 0 || m_axi_rresp != 0 || !m_axi_rlast) protocol_fault <= 1;
          rs <= m_axi_rlast ? R_RESP : R_DRAIN;
        end
        R_DRAIN: if (m_axi_rvalid && m_axi_rlast) rs <= R_RESP;
        R_RESP:
        if ((read_engine && mem_rsp_ready_i) || (!read_engine && s_axi_rready)) begin
          if (rleft == 0) rs <= R_IDLE;
          else begin
            rleft <= rleft - 8'd1;
            ra <= next_beat(ra, rsize);
            rd <= 0;
            if (rpermit && backend_ready_o) begin
              rerror <= 0;
              rs <= R_ADDR;
            end else begin
              rerror <= 1;
              rs <= R_RESP;
            end
          end
        end
        default: rs <= R_IDLE;
      endcase
    end
  end
endmodule
