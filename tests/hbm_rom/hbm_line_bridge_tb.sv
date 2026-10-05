// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// Native bridge protocol/CDC test. Does not model HBM physics or prove timing.
`timescale 1ns / 1ps
module hbm_line_bridge_tb;
  logic clk_i;
  logic rst_ni;
  logic clk_hbm_i;
  logic rst_hbm_ni;
  logic controller_ready_i;
  logic ready_o;
  logic fault_o;
  logic [15:0] s_axi_awid;
  logic [63:0] s_axi_awaddr;
  logic [7:0] s_axi_awlen;
  logic [2:0] s_axi_awsize;
  logic [1:0] s_axi_awburst;
  logic s_axi_awvalid;
  logic s_axi_awready;
  logic [511:0] s_axi_wdata;
  logic [63:0] s_axi_wstrb;
  logic s_axi_wlast;
  logic s_axi_wvalid;
  logic s_axi_wready;
  logic [15:0] s_axi_bid;
  logic [1:0] s_axi_bresp;
  logic s_axi_bvalid;
  logic s_axi_bready;
  logic [15:0] s_axi_arid;
  logic [63:0] s_axi_araddr;
  logic [7:0] s_axi_arlen;
  logic [2:0] s_axi_arsize;
  logic [1:0] s_axi_arburst;
  logic s_axi_arvalid;
  logic s_axi_arready;
  logic [15:0] s_axi_rid;
  logic [511:0] s_axi_rdata;
  logic [1:0] s_axi_rresp;
  logic s_axi_rlast;
  logic s_axi_rvalid;
  logic s_axi_rready;
  logic [5:0] m_axi_awid;
  logic [33:0] m_axi_awaddr;
  logic [3:0] m_axi_awlen;
  logic [2:0] m_axi_awsize;
  logic [1:0] m_axi_awburst;
  logic m_axi_awvalid;
  logic m_axi_awready;
  logic [255:0] m_axi_wdata;
  logic [31:0] m_axi_wstrb;
  logic m_axi_wlast;
  logic m_axi_wvalid;
  logic m_axi_wready;
  logic [5:0] m_axi_bid;
  logic [1:0] m_axi_bresp;
  logic m_axi_bvalid;
  logic m_axi_bready;
  logic [5:0] m_axi_arid;
  logic [33:0] m_axi_araddr;
  logic [3:0] m_axi_arlen;
  logic [2:0] m_axi_arsize;
  logic [1:0] m_axi_arburst;
  logic m_axi_arvalid;
  logic m_axi_arready;
  logic [5:0] m_axi_rid;
  logic [255:0] m_axi_rdata;
  logic [1:0] m_axi_rresp;
  logic m_axi_rlast;
  logic m_axi_rvalid;
  logic m_axi_rready;

  hbm_line_bridge dut (.*);
  logic   run_hbm = 1;
  integer checks = 0;
  integer native_reads, native_writes;
  always #5 clk_i = ~clk_i;
  always #3 if (run_hbm) clk_hbm_i = ~clk_hbm_i;
  always @(posedge clk_hbm_i) begin
    if (!rst_ni) begin
      native_reads  <= 0;
      native_writes <= 0;
    end else begin
      if (m_axi_arvalid && m_axi_arready) native_reads <= native_reads + 1;
      if (m_axi_awvalid && m_axi_awready) native_writes <= native_writes + 1;
    end
  end

  task automatic check(input logic okay, input string label);
    if (!okay) $fatal(1, "HBM bridge check failed: %s", label);
    checks++;
  endtask

  task automatic reset_bridge;
    run_hbm = 1;
    rst_ni = 0;
    rst_hbm_ni = 0;
    controller_ready_i = 0;
    s_axi_awvalid = 0;
    s_axi_wvalid = 0;
    s_axi_arvalid = 0;
    s_axi_bready = 0;
    s_axi_rready = 0;
    m_axi_awready = 0;
    m_axi_wready = 0;
    m_axi_arready = 0;
    m_axi_bvalid = 0;
    m_axi_rvalid = 0;
    repeat (4) @(negedge clk_i);
    rst_ni = 1;
    rst_hbm_ni = 1;
    repeat (4) @(negedge clk_hbm_i);
    controller_ready_i = 1;
    wait (ready_o);
    @(negedge clk_i);
    check(!fault_o, "paired reset clears bridge fault");
  endtask

  task automatic send_aw(input logic [63:0] address, input logic [15:0] id);
    @(negedge clk_i);
    s_axi_awaddr = address;
    s_axi_awid = id;
    s_axi_awvalid = 1;
    do @(posedge clk_i); while (!s_axi_awready);
    @(negedge clk_i);
    s_axi_awvalid = 0;
  endtask

  task automatic send_w(input logic [511:0] data, input logic [63:0] strb, input logic last);
    @(negedge clk_i);
    s_axi_wdata  = data;
    s_axi_wstrb  = strb;
    s_axi_wlast  = last;
    s_axi_wvalid = 1;
    do @(posedge clk_i); while (!s_axi_wready);
    @(negedge clk_i);
    s_axi_wvalid = 0;
  endtask

  task automatic expect_b(input logic [15:0] id, input logic [1:0] resp);
    wait (s_axi_bvalid);
    repeat (3) begin
      @(negedge clk_i);
      check(s_axi_bvalid && s_axi_bid == id && s_axi_bresp == resp,
            "stable source write response including original ID");
    end
    s_axi_bready = 1;
    @(negedge clk_i);
    s_axi_bready = 0;
  endtask

  task automatic send_ar(input logic [63:0] address, input logic [15:0] id);
    @(negedge clk_i);
    s_axi_araddr = address;
    s_axi_arid = id;
    s_axi_arvalid = 1;
    do @(posedge clk_i); while (!s_axi_arready);
    @(negedge clk_i);
    s_axi_arvalid = 0;
  endtask

  task automatic expect_r(input logic [15:0] id, input logic [1:0] resp, input logic [511:0] data);
    wait (s_axi_rvalid);
    repeat (3) begin
      @(negedge clk_i);
      check(
          s_axi_rvalid && s_axi_rid == id && s_axi_rresp == resp &&
            s_axi_rlast && s_axi_rdata == data,
          "stable source line response and original ID");
    end
    s_axi_rready = 1;
    @(negedge clk_i);
    s_axi_rready = 0;
  endtask

  task automatic native_ar(input logic [28:0] address);
    wait (m_axi_arvalid);
    repeat (3) begin
      @(negedge clk_hbm_i);
      check(
          m_axi_arvalid && m_axi_araddr == {5'd15,address} && m_axi_arid == 0 &&
          m_axi_arlen == 1 && m_axi_arsize == 5 && m_axi_arburst == 1,
          "stable native read address mapping and two beats");
    end
    m_axi_arready = 1;
    @(negedge clk_hbm_i);
    m_axi_arready = 0;
  endtask

  task automatic native_r(input logic [255:0] data, input logic last, input logic [5:0] id,
                          input logic [1:0] resp);
    @(negedge clk_hbm_i);
    m_axi_rdata = data;
    m_axi_rlast = last;
    m_axi_rid = id;
    m_axi_rresp = resp;
    m_axi_rvalid = 1;
    do @(posedge clk_hbm_i); while (!m_axi_rready);
    @(negedge clk_hbm_i);
    m_axi_rvalid = 0;
  endtask

  task automatic native_b(input logic [5:0] id, input logic [1:0] resp);
    @(negedge clk_hbm_i);
    m_axi_bid = id;
    m_axi_bresp = resp;
    m_axi_bvalid = 1;
    do @(posedge clk_hbm_i); while (!m_axi_bready);
    @(negedge clk_hbm_i);
    m_axi_bvalid = 0;
  endtask

  // Accept both native W beats before AW, including stalls on each channel.
  task automatic native_write(input logic [28:0] address, input logic [511:0] data,
                              input logic [63:0] strb, input logic [5:0] bid,
                              input logic [1:0] bresp, input logic respond = 1);
    wait (m_axi_wvalid);
    repeat (3) begin
      @(negedge clk_hbm_i);
      check(m_axi_wvalid && !m_axi_wlast && m_axi_wdata == data[255:0] && m_axi_wstrb == strb[31:0],
            "first native W beat stable while stalled");
    end
    m_axi_wready = 1;
    @(negedge clk_hbm_i);
    m_axi_wready = 0;
    repeat (3) begin
      @(negedge clk_hbm_i);
      check(
          m_axi_wvalid && m_axi_wlast && m_axi_wdata == data[511:256] && m_axi_wstrb == strb[63:32],
          "second native W beat stable while stalled");
    end
    m_axi_wready = 1;
    @(negedge clk_hbm_i);
    m_axi_wready = 0;
    check(
        m_axi_awvalid && m_axi_awaddr == {5'd15,address} && m_axi_awid == 0 &&
        m_axi_awlen == 1 && m_axi_awsize == 5 && m_axi_awburst == 1,
        "AW remains stable and independent after both W beats");
    m_axi_awready = 1;
    @(negedge clk_hbm_i);
    m_axi_awready = 0;
    if (respond) native_b(bid, bresp);
  endtask

  task automatic prove_fault_closed;
    integer reads_before, writes_before;
    wait (fault_o);
    check(!ready_o, "fault closes readiness");
    reads_before  = native_reads;
    writes_before = native_writes;
    send_ar(64'h400, 16'h101);
    expect_r(16'h101, 2'b10, 0);
    send_aw(64'h400, 16'h102);
    send_w('1, '1, 1);
    expect_b(16'h102, 2'b10);
    check(
        reads_before == native_reads && writes_before == native_writes &&
          !m_axi_awvalid && !m_axi_arvalid && !m_axi_wvalid,
        "faulted ID is never reused for new native transactions");
  endtask

  logic [511:0] pattern;
  integer old_count;
  initial begin
    clk_i = 0;
    clk_hbm_i = 0;
    s_axi_awid = 0;
    s_axi_awaddr = 0;
    s_axi_awlen = 0;
    s_axi_awsize = 6;
    s_axi_awburst = 1;
    s_axi_wdata = 0;
    s_axi_wstrb = 0;
    s_axi_wlast = 1;
    s_axi_arid = 0;
    s_axi_araddr = 0;
    s_axi_arlen = 0;
    s_axi_arsize = 6;
    s_axi_arburst = 1;
    m_axi_bid = 0;
    m_axi_bresp = 0;
    m_axi_rid = 0;
    m_axi_rdata = 0;
    m_axi_rresp = 0;
    m_axi_rlast = 0;
    for (integer i = 0; i < 64; i++) pattern[i*8+:8] = 8'(i);
    reset_bridge();

    // Source W precedes AW and native W precedes AW; IDs and byte strobes survive.
    send_w(pattern, 64'hffff0000a55affff, 1);
    repeat (3) @(negedge clk_i);
    check(!m_axi_awvalid && !m_axi_wvalid, "no native write before source AW");
    send_aw(64'h100, 16'hbeef);
    native_write(29'h100, pattern, 64'hffff0000a55affff, 0, 0);
    expect_b(16'hbeef, 0);
    send_ar(64'h100, 16'hcafe);
    native_ar(29'h100);
    native_r(pattern[255:0], 0, 0, 0);
    repeat (7) @(negedge clk_hbm_i);
    native_r(pattern[511:256], 1, 0, 0);
    expect_r(16'hcafe, 0, pattern);
    check(ready_o && !fault_o, "normal unrelated-clock traffic remains ready");

    // Highest full line, upper address aliases, and all line attribute errors.
    send_ar(64'h1fffffc0, 3);
    native_ar(29'h1fffffc0);
    native_r('1, 0, 0, 0);
    native_r('1, 1, 0, 0);
    expect_r(3, 0, '1);
    old_count = native_reads;
    send_ar(64'h20000000, 4);
    expect_r(4, 3, 0);
    send_ar(64'h8000000100, 4);
    expect_r(4, 3, 0);
    send_ar(64'h101, 4);
    expect_r(4, 3, 0);
    s_axi_arlen = 1;
    send_ar(64'h100, 4);
    expect_r(4, 3, 0);
    s_axi_arlen  = 0;
    s_axi_arsize = 5;
    send_ar(64'h100, 4);
    expect_r(4, 3, 0);
    s_axi_arsize  = 6;
    s_axi_arburst = 0;
    send_ar(64'h100, 4);
    expect_r(4, 3, 0);
    s_axi_arburst = 1;
    check(native_reads == old_count && ready_o, "illegal requests never wrap or fault valid image");
    old_count = native_writes;
    send_aw(64'h20000000, 5);
    send_w(pattern, '1, 1);
    expect_b(5, 3);
    s_axi_awlen = 2;
    send_aw(64'h100, 5);
    send_w(pattern, '1, 0);
    send_w(pattern, '1, 0);
    send_w(pattern, '1, 1);
    expect_b(5, 3);
    s_axi_awlen = 0;
    check(native_writes == old_count, "illegal write burst drains with no native mutation");

    // Missing final RLAST: return an upstream error while drain waits; late
    // extra beat is swallowed without creating another source response.
    send_ar(64'h200, 6);
    native_ar(29'h200);
    native_r('1, 0, 0, 0);
    native_r('1, 0, 0, 0);
    expect_r(6, 2, 0);
    prove_fault_closed();
    native_r('1, 1, 0, 0);
    repeat (10) @(negedge clk_i);
    check(!s_axi_rvalid, "late drain completion does not duplicate source response");

    reset_bridge();
    send_ar(64'h200, 7);
    native_ar(29'h200);
    native_r('1, 1, 0, 0);
    expect_r(7, 2, 0);
    prove_fault_closed();
    reset_bridge();
    send_ar(64'h200, 8);
    native_ar(29'h200);
    native_r('1, 0, 3, 0);
    native_r('1, 1, 0, 0);
    expect_r(8, 2, 0);
    prove_fault_closed();
    reset_bridge();
    send_ar(64'h200, 9);
    native_ar(29'h200);
    native_r('1, 0, 0, 2);
    native_r('1, 1, 0, 0);
    expect_r(9, 2, 0);
    prove_fault_closed();
    reset_bridge();
    send_ar(64'h200, 10);
    native_ar(29'h200);
    native_r('1, 0, 0, 0);
    native_r('1, 1, 0, 2);
    expect_r(10, 2, 0);
    prove_fault_closed();
    reset_bridge();
    send_aw(64'h200, 11);
    send_w(pattern, '1, 1);
    native_write(29'h200, pattern, '1, 1, 0);
    expect_b(11, 2);
    prove_fault_closed();
    reset_bridge();
    send_aw(64'h200, 12);
    send_w(pattern, '1, 1);
    native_write(29'h200, pattern, '1, 0, 2);
    expect_b(12, 2);
    prove_fault_closed();
    reset_bridge();
    native_r('1, 1, 0, 0);
    prove_fault_closed();
    reset_bridge();
    native_b(0, 0);
    prove_fault_closed();

    // Stop the HBM clock with both accepted source operations still waiting.
    // Controller readiness is a level independent of that stopped clock.
    reset_bridge();
    @(negedge clk_hbm_i);
    run_hbm = 0;
    send_ar(64'h200, 13);
    send_aw(64'h200, 14);
    send_w(pattern, '1, 1);
    @(negedge clk_i);
    controller_ready_i = 0;
    expect_r(13, 2, 0);
    expect_b(14, 2);
    check(fault_o && !ready_o, "clock loss completes both accepted source commands");
    controller_ready_i = 1;
    run_hbm = 1;
    repeat (12) @(negedge clk_i);
    prove_fault_closed();
    check(!s_axi_rvalid && !s_axi_bvalid, "relock cannot replay canceled completions");

    // Repeat with native AR/AW and both W beats ALREADY accepted, so the HBM
    // controller would own transactions when its clock stops/reset discards
    // them. Neither upstream completion may depend on a native response.
    reset_bridge();
    send_ar(64'h200, 17);
    native_ar(29'h200);
    send_aw(64'h200, 18);
    send_w(pattern, '1, 1);
    native_write(29'h200, pattern, '1, 0, 0, 0);
    @(negedge clk_hbm_i);
    run_hbm = 0;
    @(negedge clk_i);
    controller_ready_i = 0;
    expect_r(17, 2, 0);
    expect_b(18, 2);
    controller_ready_i = 1;
    run_hbm = 1;
    repeat (12) @(negedge clk_i);
    prove_fault_closed();
    native_r('1, 0, 0, 0);
    native_r('1, 1, 0, 0);
    native_b(0, 0);
    repeat (10) @(negedge clk_i);
    check(!s_axi_rvalid && !s_axi_bvalid,
          "late native responses cannot replay canceled completions");

    // An already-held response must not change when another channel faults.
    reset_bridge();
    send_ar(64'h100, 19);
    native_ar(29'h100);
    native_r(pattern[255:0], 0, 0, 0);
    native_r(pattern[511:256], 1, 0, 0);
    wait (s_axi_rvalid);
    native_b(0, 2);
    wait (fault_o);
    expect_r(19, 0, pattern);
    prove_fault_closed();

    reset_bridge();
    send_aw(64'h100, 20);
    send_w(pattern, '1, 1);
    native_write(29'h100, pattern, '1, 0, 0);
    wait (s_axi_bvalid);
    native_r('1, 1, 0, 2);
    wait (fault_o);
    expect_b(20, 0);
    prove_fault_closed();

    // Paired global reset cancels outstanding traffic only with both clients
    // reset; fresh response cannot include pre-reset mailbox payload.
    reset_bridge();
    send_ar(64'h200, 15);
    native_ar(29'h200);
    native_r('1, 0, 0, 0);
    reset_bridge();
    send_ar(64'h300, 16);
    native_ar(29'h300);
    native_r(pattern[255:0], 0, 0, 0);
    native_r(pattern[511:256], 1, 0, 0);
    expect_r(16, 0, pattern);
    $display("PASS hbm_line_bridge_tb: %0d checks, core=10ns HBM=6ns; no physical HBM claim",
             checks);
    $finish;
  end
  initial begin
    #200000;
    $fatal(1, "HBM bridge test timeout");
  end
endmodule
