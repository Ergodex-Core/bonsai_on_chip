`timescale 1ns / 1ps
module f2_memory_bridge_tb;
  logic clk_i;
  logic rst_ni;
  logic ddr_ready_i;
  logic loader_enable_i;
  logic [63:0] image_base_i;
  logic [63:0] image_bytes_i;
  logic loader_idle_o;
  logic backend_ready_o;
  logic loader_write_rejected_o;
  logic mem_req_valid_i;
  logic mem_req_ready_o;
  logic [63:0] mem_req_addr_i;
  logic mem_rsp_valid_o;
  logic mem_rsp_ready_i;
  logic [511:0] mem_rsp_data_o;
  logic mem_rsp_error_o;
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
  logic [15:0] m_axi_awid;
  logic [63:0] m_axi_awaddr;
  logic [7:0] m_axi_awlen;
  logic [2:0] m_axi_awsize;
  logic [1:0] m_axi_awburst;
  logic m_axi_awvalid;
  logic m_axi_awready;
  logic [511:0] m_axi_wdata;
  logic [63:0] m_axi_wstrb;
  logic m_axi_wlast;
  logic m_axi_wvalid;
  logic m_axi_wready;
  logic [15:0] m_axi_bid;
  logic [1:0] m_axi_bresp;
  logic m_axi_bvalid;
  logic m_axi_bready;
  logic [15:0] m_axi_arid;
  logic [63:0] m_axi_araddr;
  logic [7:0] m_axi_arlen;
  logic [2:0] m_axi_arsize;
  logic [1:0] m_axi_arburst;
  logic m_axi_arvalid;
  logic m_axi_arready;
  logic [15:0] m_axi_rid;
  logic [511:0] m_axi_rdata;
  logic [1:0] m_axi_rresp;
  logic m_axi_rlast;
  logic m_axi_rvalid;
  logic m_axi_rready;
  f2_memory_bridge dut (.*);
  initial begin
    clk_i = '0;
    rst_ni = '0;
    ddr_ready_i = '0;
    loader_enable_i = '0;
    image_base_i = '0;
    image_bytes_i = '0;
    mem_req_valid_i = '0;
    mem_req_addr_i = '0;
    mem_rsp_ready_i = '0;
    s_axi_awid = '0;
    s_axi_awaddr = '0;
    s_axi_awlen = '0;
    s_axi_awsize = '0;
    s_axi_awburst = '0;
    s_axi_awvalid = '0;
    s_axi_wdata = '0;
    s_axi_wstrb = '0;
    s_axi_wlast = '0;
    s_axi_wvalid = '0;
    s_axi_bready = '0;
    s_axi_arid = '0;
    s_axi_araddr = '0;
    s_axi_arlen = '0;
    s_axi_arsize = '0;
    s_axi_arburst = '0;
    s_axi_arvalid = '0;
    s_axi_rready = '0;
    m_axi_awready = '0;
    m_axi_wready = '0;
    m_axi_bid = '0;
    m_axi_bresp = '0;
    m_axi_bvalid = '0;
    m_axi_arready = '0;
    m_axi_rid = '0;
    m_axi_rdata = '0;
    m_axi_rresp = '0;
    m_axi_rlast = '0;
    m_axi_rvalid = '0;
  end
  always #2 clk_i = ~clk_i;
  initial begin
    #200000;
    $fatal(1, "watchdog");
  end
  logic [511:0] memory[0:63];
  logic aw_seen, w_seen, read_pending, extra_pending;
  logic [ 63:0] aw_addr;
  logic [511:0] w_data;
  logic [ 63:0] w_strb;
  integer ticks, writes, reads, rejected, checks, read_delay;
  integer total_writes = 0, total_reads = 0, total_rejected = 0;
  logic [1:0] inject_bresp, inject_rresp;
  logic inject_rid, inject_extra;
  always @(posedge clk_i) begin
    if (!rst_ni) begin
      ticks <= 0;
      writes <= 0;
      reads <= 0;
      rejected <= 0;
      aw_seen <= 0;
      w_seen <= 0;
      read_pending <= 0;
      extra_pending <= 0;
      m_axi_bvalid <= 0;
      m_axi_rvalid <= 0;
      m_axi_awready <= 0;
      m_axi_wready <= 0;
      m_axi_arready <= 0;
    end else begin
      ticks <= ticks + 1;
      // Different, intermittent readiness exercises independent AW/W acceptance.
      m_axi_awready <= ticks % 3 == 0 && !aw_seen && !m_axi_bvalid;
      m_axi_wready <= ticks % 4 == 1 && !w_seen && !m_axi_bvalid;
      m_axi_arready <= ticks % 3 == 1 && !read_pending && !m_axi_rvalid;
      if (loader_write_rejected_o) begin
        rejected <= rejected + 1;
        total_rejected <= total_rejected + 1;
      end
      if (m_axi_awvalid && m_axi_awready) begin
        if (m_axi_awlen != 0 || m_axi_awsize != 6 || m_axi_awburst != 1 || m_axi_awaddr[5:0] != 0)
          $fatal(1, "DDR write is not one aligned line");
        aw_addr <= m_axi_awaddr;
        aw_seen <= 1;
      end
      if (m_axi_wvalid && m_axi_wready) begin
        if (!m_axi_wlast) $fatal(1, "DDR WLAST");
        w_data <= m_axi_wdata;
        w_strb <= m_axi_wstrb;
        w_seen <= 1;
      end
      if (aw_seen && w_seen && !m_axi_bvalid) begin
        for (int lane = 0; lane < 64; lane++)
        if (w_strb[lane]) memory[6'((aw_addr-64'h1000)>>6)][lane*8+:8] <= w_data[lane*8+:8];
        writes <= writes + 1;
        total_writes <= total_writes + 1;
        aw_seen <= 0;
        w_seen <= 0;
        m_axi_bvalid <= 1;
        m_axi_bresp <= inject_bresp;
        m_axi_bid <= 0;
      end
      if (m_axi_bvalid && m_axi_bready) m_axi_bvalid <= 0;
      if (m_axi_arvalid && m_axi_arready) begin
        if (m_axi_arlen != 0 || m_axi_arsize != 6 || m_axi_arburst != 1 || m_axi_araddr[5:0] != 0)
          $fatal(1, "DDR read is not one aligned line");
        reads <= reads + 1;
        total_reads <= total_reads + 1;
        read_pending <= 1;
        read_delay <= 3;
        m_axi_rdata <= memory[6'((m_axi_araddr-64'h1000)>>6)];
        m_axi_rresp <= inject_rresp;
        m_axi_rid <= {15'd0, inject_rid};
        m_axi_rlast <= !inject_extra;
        extra_pending <= inject_extra;
      end
      if (read_pending && !m_axi_rvalid) begin
        if (read_delay == 0) begin
          m_axi_rvalid <= 1;
          read_pending <= 0;
        end else read_delay <= read_delay - 1;
      end
      if (m_axi_rvalid && m_axi_rready) begin
        m_axi_rvalid <= 0;
        if (extra_pending) begin
          extra_pending <= 0;
          read_pending <= 1;
          read_delay <= 3;
          m_axi_rlast <= 1;
          m_axi_rdata <= '1;
        end
      end
    end
  end
  task automatic check(input logic condition, input string label);
    if (!condition) $fatal(1, "FAIL %s", label);
    checks++;
    $display("PASS %s", label);
  endtask
  task automatic host_aw(input logic [63:0] addr, input logic [7:0] len, input logic [2:0] size);
    @(negedge clk_i);
    s_axi_awvalid = 1;
    s_axi_awaddr = addr;
    s_axi_awlen = len;
    s_axi_awsize = size;
    s_axi_awburst = 1;
    s_axi_awid = 16'h42;
    do @(posedge clk_i); while (!s_axi_awready);
    @(negedge clk_i);
    s_axi_awvalid = 0;
  endtask
  task automatic host_w(input logic [511:0] data, input logic [63:0] strb, input logic last);
    @(negedge clk_i);
    s_axi_wvalid = 1;
    s_axi_wdata  = data;
    s_axi_wstrb  = strb;
    s_axi_wlast  = last;
    do @(posedge clk_i); while (!s_axi_wready);
    @(negedge clk_i);
    s_axi_wvalid = 0;
  endtask
  task automatic host_b(input logic [1:0] expected);
    do @(posedge clk_i); while (!s_axi_bvalid);
    check(s_axi_bresp == expected && s_axi_bid == 16'h42, "host write response");
    repeat (3) begin
      @(posedge clk_i);
      check(s_axi_bvalid && s_axi_bresp == expected, "B backpressure stability");
    end
    @(negedge clk_i);
    s_axi_bready = 1;
    @(negedge clk_i);
    s_axi_bready = 0;
  endtask
  task automatic engine_read(input logic [63:0] addr, input logic error,
                             input logic [511:0] expected);
    @(negedge clk_i);
    mem_req_addr_i  = addr;
    mem_req_valid_i = 1;
    do @(posedge clk_i); while (!mem_req_ready_o);
    @(negedge clk_i);
    mem_req_valid_i = 0;
    do @(posedge clk_i); while (!mem_rsp_valid_o);
    check(mem_rsp_error_o == error && mem_rsp_data_o === expected, "engine read result");
    repeat (3) begin
      @(posedge clk_i);
      check(mem_rsp_valid_o && mem_rsp_data_o === expected, "engine response backpressure");
    end
    @(negedge clk_i);
    mem_rsp_ready_i = 1;
    @(negedge clk_i);
    mem_rsp_ready_i = 0;
  endtask
  task automatic host_read(input logic [63:0] addr, input logic [7:0] len, input logic [2:0] size,
                           input logic [1:0] expected_resp);
    @(negedge clk_i);
    s_axi_araddr = addr;
    s_axi_arlen = len;
    s_axi_arsize = size;
    s_axi_arburst = 1;
    s_axi_arvalid = 1;
    s_axi_arid = 16'h17;
    do @(posedge clk_i); while (!s_axi_arready);
    @(negedge clk_i);
    s_axi_arvalid = 0;
    for (int beat = 0; beat <= int'(len); beat++) begin
      do @(posedge clk_i); while (!s_axi_rvalid);
      check(
          s_axi_rresp == expected_resp && s_axi_rid == 16'h17 && s_axi_rlast == (beat == int'(len)),
          "host read beat identity");
      if (expected_resp == 0)
        check(s_axi_rdata === memory[6'(((addr&~((64'd1<<size)-1))-64'h1000+(64'(beat)<<size))>>6)],
              "host read DDR data");
      else check(s_axi_rdata == 0, "host error zero data");
      repeat (2) begin
        @(posedge clk_i);
        check(s_axi_rvalid, "host R backpressure");
      end
      @(negedge clk_i);
      s_axi_rready = 1;
      @(negedge clk_i);
      s_axi_rready = 0;
    end
  endtask
  task automatic coordinated_reset();
    @(negedge clk_i);
    rst_ni = 0;
    repeat (4) @(negedge clk_i);
    rst_ni = 1;
    @(negedge clk_i);
    check(backend_ready_o, "coordinated reset restores backend readiness");
  endtask
  integer before_writes, before_reads;
  initial begin
    #1;
    checks = 0;
    inject_bresp = 0;
    inject_rresp = 0;
    inject_rid = 0;
    inject_extra = 0;
    for (int i = 0; i < 64; i++) memory[i] = '0;
    image_base_i  = 64'h1000;
    image_bytes_i = 4096;
    ddr_ready_i   = 1;
    repeat (4) @(negedge clk_i);
    rst_ni = 1;
    check(backend_ready_o, "DDR ready exposed");
    // Locked writes drain but never reach memory.
    host_aw(64'h1000, 1, 6);
    host_w('1, '1, 0);
    host_w('1, '1, 1);
    host_b(2);
    check(writes == 0 && rejected == 1, "locked burst rejected with no DDR write");
    loader_enable_i = 1;
    host_aw(64'h1000, 1, 6);
    loader_enable_i = 0;
    check(!loader_idle_o, "gate closure waits for accepted burst");
    host_w({16{32'h12345678}}, '1, 0);
    host_w({16{32'h89abcdef}}, '1, 1);
    host_b(0);
    @(posedge clk_i);
    check(loader_idle_o && writes == 2, "admitted burst drained after close");
    engine_read(64'h1000, 0, {16{32'h12345678}});
    host_read(64'h1000, 1, 6, 0);
    // Narrow 32-bit MMIO writes are converted to aligned DDR lines with byte enables.
    loader_enable_i = 1;
    host_aw(64'h1004, 0, 2);
    host_w({448'd0, 32'hcafebabe, 32'd0}, 64'hf0, 1);
    host_b(0);
    check(memory[0][63:32] == 32'hcafebabe && memory[0][31:0] == 32'h12345678,
          "narrow write preserves adjacent bytes");
    host_read(64'h1004, 0, 2, 0);
    // The pinned AWS shell BFM emits SIZE=6 even for narrow host accesses.
    host_aw(64'h1084, 0, 6);
    host_w({448'd0, 32'h10203040, 32'd0}, 64'hf0, 1);
    host_b(0);
    check(memory[2][63:32] == 32'h10203040 && memory[2][31:0] == 0,
          "size6 32-bit mmap write preserves lower lanes");
    host_read(64'h1084, 0, 6, 0);
    host_aw(64'h1088, 0, 6);
    host_w({384'd0, 64'h1122334455667788, 64'd0}, 64'hff00, 1);
    host_b(0);
    check(memory[2][127:64] == 64'h1122334455667788, "size6 64-bit mmap write retains byte lanes");
    host_read(64'h1088, 0, 6, 0);
    host_aw(64'h1081, 0, 6);
    host_w({440'd0, 64'h8877665544332211, 8'd0}, 64'h1fe, 1);
    host_b(0);
    check(memory[2][71:8] == 64'h8877665544332211 && memory[2][7:0] == 0,
          "documented AWS byte-unaligned write");
    // Unaligned first beat advances to the next aligned boundary, not addr+size.
    host_aw(64'h113e, 1, 6);
    host_w({16'h1234, 496'd0}, 64'hc000000000000000, 0);
    host_w({16{32'h76543210}}, '1, 1);
    host_b(0);
    check(
        memory[4][511:496] == 16'h1234 && memory[4][495:0] == 0 && memory[5] == {16{32'h76543210}},
        "unaligned full burst uses aligned second beat");
    host_read(64'h113e, 1, 6, 0);
    host_aw(64'h1181, 1, 2);
    host_w({480'd0, 24'habcdef, 8'd0}, 64'he, 0);
    host_w({448'd0, 32'h12345678, 32'd0}, 64'hf0, 1);
    host_b(0);
    check(memory[6][63:0] == 64'h12345678abcdef00,
          "unaligned narrow burst aligns following transfer");
    host_read(64'h1181, 1, 2, 0);
    // Last-line byte reads/writes stay in the allocation/page despite SIZE=6.
    host_aw(64'h1ff8, 0, 6);
    host_w({64'hfedcba9876543210, 448'd0}, 64'hff00000000000000, 1);
    host_b(0);
    check(memory[63][511:448] == 64'hfedcba9876543210,
          "last allocation line accepts unaligned size6 write");
    host_read(64'h1ff8, 0, 6, 0);
    before_writes = writes;
    host_aw(64'h113e, 0, 6);
    host_w('1, 64'hc000000000000001, 1);
    host_b(2);
    check(writes == before_writes, "first-beat strobes before address rejected");
    host_aw(64'h1ff8, 1, 6);
    host_w('1, 64'hff00000000000000, 0);
    host_w('1, '1, 1);
    host_b(2);
    check(writes == before_writes, "unaligned burst cannot cross allocation/page end");
    // Enlarge allocation to isolate the independent 4KiB boundary check.
    image_bytes_i = 8192;
    host_aw(64'h1fff, 1, 6);
    host_w('1, 64'h8000000000000000, 0);
    host_w('1, '1, 1);
    host_b(2);
    host_read(64'h1fff, 1, 6, 2);
    check(writes == before_writes, "unaligned 4KiB crossing denied inside allocation");
    image_bytes_i = 4096;
    before_writes = writes;
    host_aw(64'h1fc0, 1, 6);
    host_w('1, '1, 0);
    host_w('1, '1, 1);
    host_b(2);
    host_aw(64'h0fc0, 0, 6);
    host_w('1, '1, 1);
    host_b(2);
    host_aw(64'h1001, 0, 2);
    host_w('1, 64'hf, 1);
    host_b(2);
    host_aw(64'h1004, 0, 2);
    host_w('1, '1, 1);
    host_b(2);
    check(writes == before_writes, "out-of-range, crossing and invalid strobe denial");
    before_reads = reads;
    engine_read(64'h1001, 1, 0);
    host_read(64'h2000, 1, 6, 2);
    check(reads == before_reads, "invalid reads never touch DDR");
    image_base_i  = 64'hffffffffffffffc0;
    image_bytes_i = 128;
    host_aw(64'hffffffffffffffc0, 0, 6);
    host_w('1, '1, 1);
    host_b(2);
    check(writes == before_writes, "65-bit allocation overflow rejected");
    image_base_i  = 64'h1000;
    image_bytes_i = 4096;
    // Both read clients contend; each must complete without replacing the other.
    fork
      engine_read(64'h1040, 0, {16{32'h89abcdef}});
      host_read(64'h1000, 0, 6, 0);
    join
    // All-zero data demonstrates why digest equality cannot replace error status.
    inject_rresp = 2;
    engine_read(64'h10c0, 1, 0);
    inject_rresp = 0;
    check(!backend_ready_o, "nonzero RRESP fails closed even for zero payload");
    before_reads = reads;
    engine_read(64'h1000, 1, 0);
    check(reads == before_reads, "RRESP fault prevents DDR ID reuse");
    coordinated_reset();
    inject_bresp = 2;
    host_aw(64'h10c0, 0, 6);
    host_w('0, '1, 1);
    host_b(2);
    inject_bresp = 0;
    check(!backend_ready_o && memory[3] == 0,
          "nonzero BRESP fails closed despite matching zero bytes");
    before_writes = writes;
    host_aw(64'h10c0, 0, 6);
    host_w('0, '1, 1);
    host_b(2);
    check(writes == before_writes, "BRESP fault denies later DDR writes");
    coordinated_reset();
    inject_extra = 1;
    engine_read(64'h1000, 1, 0);
    inject_extra = 0;
    check(!backend_ready_o, "unexpected RLAST fails closed after drain");
    before_reads = reads;
    engine_read(64'h1040, 1, 0);
    check(reads == before_reads, "faulted DDR ID is not reused");
    $display("PASS f2_memory_bridge checks=%0d DDR_writes=%0d DDR_reads=%0d denied_AW=%0d", checks,
             total_writes, total_reads, total_rejected);
    $finish;
  end
endmodule
