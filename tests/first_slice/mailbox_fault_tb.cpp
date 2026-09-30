// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// Fault timing regression for the actual mailbox/store/dot RTL. The line
// responder below is a synthetic test model, not actual model/FPGA evidence.
#include <cstdint>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>

#include "Vfirst_slice_top.h"
#include "verilated.h"

static void check(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}

enum class Fault { None, BeforeFirst, BetweenLines, PendingResponse, StalledRequest };

class Harness {
 public:
  VerilatedContext context;
  Vfirst_slice_top dut{&context};
  uint64_t ticks = 0, reads = 0, responses = 0;
  Fault fault   = Fault::None;
  bool injected = false, hold_memory = false;

  void tick() {
    if (!injected && fault == Fault::BetweenLines && responses == 1 && ticks == response_at + 3)
      drop_backend();
    if (!injected && fault == Fault::StalledRequest && dut.mem_req_valid_o)
      drop_backend();
    if (injected && fault == Fault::StalledRequest && ticks >= fault_at + 6)
      hold_memory = false;
    dut.clk_i           = 0;
    dut.mem_req_ready_i = !hold_memory;
    dut.mem_rsp_valid_i = pending && ticks >= *pending;
    dut.mem_rsp_error_i = 0;
    for (unsigned i = 0; i < 16; ++i)
      dut.mem_rsp_data_i[i] = 0x55555555;
    dut.eval();
    bool request_accepted = false;
    if (dut.rst_ni) {
      if (held_request)
        check(dut.mem_req_valid_o && dut.mem_req_addr_o == *held_request,
              "held physical request withdrawn/changed during fault");
      held_request = dut.mem_req_valid_o && !dut.mem_req_ready_i
                         ? std::optional<uint64_t>(dut.mem_req_addr_o)
                         : std::nullopt;
      if (dut.mem_rsp_valid_i && dut.mem_rsp_ready_o) {
        check(pending.has_value(), "response had no owner");
        pending.reset();
        ++responses;
        response_at = ticks;
      }
      if (dut.mem_req_valid_o && dut.mem_req_ready_i) {
        check(!pending, "outstanding physical read ID reused");
        check(dut.mem_req_addr_o < 128 && dut.mem_req_addr_o % 64 == 0,
              "physical read escaped synthetic image");
        pending = ticks + 9;
        ++reads;
        request_accepted = true;
      }
    }
    dut.clk_i = 1;
    dut.eval();
    ++ticks;
    dut.clk_i = 0;
    dut.eval();
    if (request_accepted && fault == Fault::PendingResponse && !injected)
      drop_backend();
  }

  void reset() {
    pending.reset();
    held_request.reset();
    dut.rst_ni                  = 0;
    dut.backend_ready_i         = 1;
    dut.loader_idle_i           = 1;
    dut.loader_write_rejected_i = 0;
    dut.s_axi_awvalid = dut.s_axi_wvalid = dut.s_axi_arvalid = 0;
    dut.s_axi_bready = dut.s_axi_rready = 0;
    fault                               = Fault::None;
    injected = hold_memory = false;
    tick();
    tick();
    dut.rst_ni = 1;
    tick();
  }

  unsigned write(uint32_t address, uint32_t value) {
    dut.s_axi_awaddr  = address;
    dut.s_axi_awvalid = 1;
    dut.s_axi_wdata   = value;
    dut.s_axi_wstrb   = 15;
    dut.s_axi_wvalid  = 1;
    dut.s_axi_bready  = 0;
    bool aw = false, w = false;
    for (unsigned budget = 0; budget < 100; ++budget) {
      dut.eval();
      const bool take_aw = dut.s_axi_awvalid && dut.s_axi_awready;
      const bool take_w  = dut.s_axi_wvalid && dut.s_axi_wready;
      tick();
      if (take_aw) {
        aw                = true;
        dut.s_axi_awvalid = 0;
      }
      if (take_w) {
        w                = true;
        dut.s_axi_wvalid = 0;
      }
      if (dut.s_axi_bvalid) {
        check(aw && w, "write response before address/data handshakes");
        const unsigned response = dut.s_axi_bresp;
        // START committed on this edge; drop readiness before the dot accepts
        // the command on the next edge, rather than testing pre-START refusal.
        if (address == 0x1010 && value == 1 && fault == Fault::BeforeFirst && !injected)
          drop_backend();
        dut.s_axi_bready = 1;
        tick();
        dut.s_axi_bready = 0;
        return response;
      }
    }
    throw std::runtime_error("OCL write timed out");
  }

  uint32_t read(uint32_t address) {
    dut.s_axi_araddr  = address;
    dut.s_axi_arvalid = 1;
    dut.s_axi_rready  = 0;
    for (unsigned budget = 0; budget < 100; ++budget) {
      dut.eval();
      const bool accepted = dut.s_axi_arvalid && dut.s_axi_arready;
      tick();
      if (accepted)
        dut.s_axi_arvalid = 0;
      if (dut.s_axi_rvalid) {
        check(dut.s_axi_rresp == 0, "OCL read error");
        const auto value = dut.s_axi_rdata;
        dut.s_axi_rready = 1;
        tick();
        dut.s_axi_rready = 0;
        return value;
      }
    }
    throw std::runtime_error("OCL read timed out");
  }

  void write_ok(uint32_t address, uint32_t value) {
    check(write(address, value) == 0, "OCL write rejected unexpectedly");
  }

  void seal_synthetic_memory() {
    // Match host verification words explicitly; these are fixture metadata for
    // the synthetic responder, not a claim of cryptographic DDR verification.
    write_ok(0x118, 128);
    for (unsigned i = 0; i < 8; ++i)
      write_ok(0x120 + 4 * i, 0);
    write_ok(0x100, 1);
    write_ok(0x100, 2);
    for (unsigned i = 0; i < 8; ++i)
      write_ok(0x140 + 4 * i, 0);
    write_ok(0x100, 3);
    check((read(4) & 7) == 3, "synthetic image did not seal");
    for (unsigned i = 0; i < 32; ++i)
      write_ok(0x1080 + 4 * i, 0x01010101);
    write_ok(0x101c, 2);
    write_ok(0x1020, 128);
    write_ok(0x102c, 1);
  }

  void start(uint32_t cookie, uint32_t offset) {
    write_ok(0x1018, cookie);
    write_ok(0x1024, offset);
    write_ok(0x1010, 1);
  }

  void completion(unsigned expected_status, uint32_t expected_cookie) {
    bool done = false;
    for (unsigned i = 0; i < 200; ++i) {
      if (read(0x100c) & 2) {
        done = true;
        break;
      }
    }
    check(done, "job never completed after backend fault");
    check(read(0x1014) == expected_status, "wrong completion status");
    check(read(0x1038) == expected_cookie, "stale completion cookie");
  }

  void check_abort() {
    check(injected, "requested fault was never injected");
    const auto status = read(0x100c);
    check((status & 0x37) == 0x36, "busy/done/error/store-fault/abort latch mismatch");
    check(read(0x1030) == 0 && read(0x1034) == 0, "aborted result was not zeroed");
    for (uint32_t address = 0x1040; address <= 0x1054; address += 4)
      check(read(address) == 0, "aborted job reused old/live dot counters");
    for (unsigned i = 0; i < 32; ++i)
      tick();
    check(!pending && !dut.mem_req_valid_o, "physical transaction did not drain");
    check(reads == responses, "physical read was abandoned or duplicated");
    const auto old_reads = reads;
    dut.backend_ready_i  = 1;
    for (unsigned i = 0; i < 8; ++i)
      tick();
    check(read(0x1014) == 4 && read(0x1030) == 0, "late dot result replaced failed completion");
    write_ok(0x1010, 2);
    check(write(0x1010, 1) == 2, "START after ACK bypassed sticky fault latch");
    for (unsigned i = 0; i < 20; ++i)
      tick();
    check(reads == old_reads && read(0x100c) & 0x20, "faulted engine reused a request ID");
  }

 private:
  std::optional<uint64_t> pending, held_request;
  uint64_t response_at = 0, fault_at = 0;
  void drop_backend() {
    dut.backend_ready_i = 0;
    injected            = true;
    fault_at            = ticks;
  }
};

int main() {
  try {
    unsigned cases = 0;
    for (Fault mode :
         {Fault::BeforeFirst, Fault::BetweenLines, Fault::PendingResponse, Fault::StalledRequest}) {
      Harness h;
      h.reset();
      h.seal_synthetic_memory();
      // First complete a real RTL operation to populate nonzero counters, then
      // prove that the abort cannot report those old measurements as its own.
      h.start(100, 0);
      h.completion(0, 100);
      check(h.read(0x1030) == 128 && h.read(0x1040) > 0 && h.read(0x1050) == 1,
            "baseline synthetic operation/counters invalid");
      h.write_ok(0x1010, 2);
      const auto prior_reads = h.reads;
      h.responses            = 0;
      h.reads                = 0;
      check(prior_reads == 1, "unexpected baseline read inventory");
      h.fault       = mode;
      h.hold_memory = mode == Fault::StalledRequest;
      h.start(103, mode == Fault::BetweenLines ? 63 : 0);
      h.completion(4, 103);
      h.check_abort();
      check(h.reads == (mode == Fault::BeforeFirst ? 0u : 1u), "fault admitted an extra line");
      // Only coordinated reset restores the command path.
      h.reset();
      h.seal_synthetic_memory();
      h.start(104, 0);
      h.completion(0, 104);
      check(h.read(0x1030) == 128 && !(h.read(0x100c) & 0x20), "reset did not recover mailbox");
      ++cases;
    }
    std::cout << "{\"status\":\"PASS\",\"fault_scenarios\":" << cases
              << ",\"successful_baselines\":4,\"coordinated_reset_recoveries\":4,"
                 "\"scope\":\"integrated mailbox/store/dot with synthetic line responder\"}\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "MAILBOX_FAULT_FAIL " << error.what() << '\n';
    return 1;
  }
}
