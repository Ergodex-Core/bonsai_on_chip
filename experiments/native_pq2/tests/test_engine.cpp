// Native PQ2 AXI engine correctness/protocol benchmark. No RTL internals used.
#include "Vcoral_weight_axi.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

static void check(bool ok, const std::string &message) {
  if (!ok) throw std::runtime_error(message);
}
static uint32_t get32(std::istream &in) {
  uint8_t b[4]; in.read(reinterpret_cast<char *>(b), 4);
  check(bool(in), "truncated fixture");
  return uint32_t(b[0]) | uint32_t(b[1]) << 8 | uint32_t(b[2]) << 16 | uint32_t(b[3]) << 24;
}
struct Block {
  uint32_t address;
  std::array<uint8_t, 34> bytes;
  std::array<int32_t, 4> expected;
};
struct Fixture {
  std::string name;
  uint32_t units, alignment, stride, activation_scale;
  std::array<uint8_t, 128> activation;
  std::vector<Block> blocks;
};
static std::vector<Fixture> load(const std::string &path, uint32_t &rom_bytes) {
  std::ifstream in(path, std::ios::binary);
  check(bool(in), "open fixture");
  char magic[8]; in.read(magic, 8);
  check(std::string(magic, 8) == "PQ2FX002", "fixture version");
  rom_bytes = get32(in);
  check(rom_bytes == 1048576, "compile ROM_BYTES=1048576 for this harness");
  const uint32_t count = get32(in);
  check(count > 0 && count <= 100000, "fixture case count");
  std::vector<Fixture> out;
  for (uint32_t n = 0; n < count; ++n) {
    Fixture f;
    const uint32_t size = get32(in);
    check(size > 0 && size < 200, "fixture name size");
    f.name.resize(size); in.read(&f.name[0], size);
    check(f.name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") == std::string::npos,
          "fixture name is not a safe JSON label");
    f.units = get32(in); f.alignment = get32(in); f.stride = get32(in);
    f.activation_scale = get32(in);
    check(f.units >= 1 && f.units <= 32, "fixture units");
    in.read(reinterpret_cast<char *>(f.activation.data()), 128);
    for (uint32_t u = 0; u < f.units; ++u) {
      Block b; b.address = get32(in);
      check(b.address <= rom_bytes - 34, "fixture block address");
      in.read(reinterpret_cast<char *>(b.bytes.data()), 34);
      for (auto &value : b.expected) value = static_cast<int32_t>(get32(in));
      f.blocks.push_back(b);
    }
    check(bool(in), "fixture payload"); out.push_back(f);
  }
  check(in.peek() == std::char_traits<char>::eof(), "trailing fixture bytes");
  return out;
}

struct Options {
  std::string fixtures;
  unsigned latency = 20, beat_ii = 1, capacity = 8, stall_percent = 20;
  unsigned jitter = 3, seed = 7193, mmio_stall_max = 3;
};
struct Counters {
  uint64_t requests = 0, responses = 0, bytes = 0, request_stalls = 0;
  uint64_t response_stalls = 0, reads = 0, writes = 0, peak = 0;
};
struct Response { uint32_t address; uint64_t ready_at; bool error; };
struct Signals { bool aw, w, ar, request, response; uint32_t address; };
struct Sim {
  Vcoral_weight_axi d;
  Options opt;
  std::vector<uint8_t> rom;
  std::deque<Response> pending;
  std::mt19937 rng;
  Counters counts;
  uint64_t ticks = 0, next_response = 0, fail_request = 0;
  bool request_held = false;
  uint32_t held_address = 0;
  static constexpr uint32_t M = 0x60000000, R = 0x40000000;
  Sim(const Options &options, uint32_t bytes) : opt(options), rom(bytes), rng(opt.seed) {
    reset();
  }
  Signals step() {
    d.clk = 0;
    d.storage_ready = pending.size() < opt.capacity && rng() % 100 >= opt.stall_percent;
    d.storage_rsp_valid = !pending.empty() && ticks >= pending.front().ready_at && ticks >= next_response;
    d.storage_rsp_error = !pending.empty() && pending.front().error;
    for (unsigned word = 0; word < 4; ++word) {
      uint32_t value = 0;
      if (!pending.empty()) for (unsigned byte = 0; byte < 4; ++byte) {
        uint32_t address = pending.front().address + 4 * word + byte;
        if (address < rom.size()) value |= uint32_t(rom[address]) << (8 * byte);
      }
      d.storage_rsp_data[word] = value;
    }
    d.eval();
    if (!d.reset && request_held)
      check(d.storage_valid && d.storage_addr == held_address, "storage request changed under backpressure");
    Signals signals{bool(d.s_awvalid && d.s_awready), bool(d.s_wvalid && d.s_wready),
                    bool(d.s_arvalid && d.s_arready), bool(d.storage_valid && d.storage_ready),
                    bool(d.storage_rsp_valid && d.storage_rsp_ready), uint32_t(d.storage_addr)};
    request_held = !d.reset && d.storage_valid && !d.storage_ready;
    held_address = d.storage_addr;
    if (!d.reset && d.storage_valid && !d.storage_ready) ++counts.request_stalls;
    if (!d.reset && d.storage_rsp_valid && !d.storage_rsp_ready) ++counts.response_stalls;
    d.clk = 1; d.eval();
    if (signals.response && !d.reset) {
      pending.pop_front(); ++counts.responses; next_response = ticks + opt.beat_ii;
    }
    if (signals.request && !d.reset) {
      check(signals.address % 16 == 0, "storage request must be 128-bit aligned");
      check(signals.address < rom.size(), "storage request out of bounds");
      check(pending.size() < opt.capacity, "storage capacity exceeded");
      ++counts.requests; counts.bytes += 16;
      const uint64_t due = ticks + opt.latency + (opt.jitter ? rng() % (opt.jitter + 1) : 0);
      pending.push_back({signals.address, due, counts.requests == fail_request});
      counts.peak = std::max<uint64_t>(counts.peak, pending.size());
    }
    ++ticks;
    return signals;
  }
  void reset() {
    // Reset covers both DUT and storage transaction domain. Outstanding replies
    // cannot survive a platform reset unless its adapter supplies an epoch.
    pending.clear(); request_held = false; next_response = 0; fail_request = 0;
    d.s_arvalid = d.s_awvalid = d.s_wvalid = d.s_rready = d.s_bready = 0;
    d.reset = 1; for (int n = 0; n < 4; ++n) step(); d.reset = 0;
  }
  unsigned stall_count() { return opt.mmio_stall_max ? rng() % (opt.mmio_stall_max + 1) : 0; }
  unsigned write(uint32_t address, uint32_t value, unsigned order = 0, uint8_t mask = 15) {
    ++counts.writes;
    d.s_awaddr = address; d.s_awid = 17; d.s_awsize = 2; d.s_awlen = 0; d.s_wlast = 1;
    for (int n = 0; n < 4; ++n) d.s_wdata[n] = 0;
    d.s_wdata[(address % 16) / 4] = value; d.s_wstrb = uint32_t(mask) << (address % 16);
    bool aw = false, w = false;
    for (unsigned n = 0; !aw || !w; ++n) {
      check(n < 100000, "AXI write acceptance timeout");
      d.s_awvalid = !aw && (order != 1 || n >= 3);
      d.s_wvalid = !w && (order != 2 || n >= 3);
      auto signals = step(); aw |= signals.aw; w |= signals.w;
    }
    d.s_awvalid = d.s_wvalid = 0;
    for (unsigned n = 0; !d.s_bvalid; ++n) { check(n < 100000, "AXI B timeout"); step(); }
    const unsigned response = d.s_bresp, id = d.s_bid;
    check(id == 17, "AXI write ID");
    for (unsigned n = stall_count(); n > 0; --n) {
      step(); check(d.s_bvalid && d.s_bresp == response && d.s_bid == id, "AXI B changed while stalled");
    }
    d.s_bready = 1; step(); d.s_bready = 0;
    return response;
  }
  uint32_t read(uint32_t address, unsigned expected_response = 0) {
    ++counts.reads;
    d.s_araddr = address; d.s_arid = 23; d.s_arsize = 2; d.s_arlen = 0; d.s_arvalid = 1;
    for (unsigned n = 0;; ++n) { check(n < 100000, "AXI AR timeout"); if (step().ar) break; }
    d.s_arvalid = 0;
    for (unsigned n = 0; !d.s_rvalid; ++n) { check(n < 100000, "AXI R timeout"); step(); }
    check(d.s_rresp == expected_response, "AXI read response");
    check(d.s_rid == 23 && d.s_rlast, "AXI read ID/last");
    std::array<uint32_t, 4> data;
    for (int i = 0; i < 4; ++i) data[i] = d.s_rdata[i];
    for (unsigned n = stall_count(); n > 0; --n) {
      step(); check(d.s_rvalid && d.s_rresp == expected_response && d.s_rid == 23 && d.s_rlast,
                    "AXI R control changed while stalled");
      for (int i = 0; i < 4; ++i) check(data[i] == d.s_rdata[i], "AXI R data changed while stalled");
    }
    d.s_rready = 1; step(); d.s_rready = 0;
    return data[(address % 16) / 4];
  }
  void set_fixture(const Fixture &f) {
    check(pending.empty(), "fixture changed with storage responses outstanding");
    std::fill(rom.begin(), rom.end(), 0);
    for (const auto &block : f.blocks)
      std::copy(block.bytes.begin(), block.bytes.end(), rom.begin() + block.address);
  }
  void configure(const Fixture &f) {
    check(write(M + 8, f.units) == 0, "set unit count");
    for (unsigned u = 0; u < f.units; ++u)
      check(write(M + 0x100 + 4 * u, f.blocks[u].address, u % 3) == 0, "set native block address");
    for (unsigned lane = 0; lane < 128; lane += 4) {
      uint32_t value = 0;
      for (unsigned j = 0; j < 4; ++j) value |= uint32_t(f.activation[lane + j]) << (8 * j);
      check(write(M + 0x200 + lane, value, (lane / 4) % 3) == 0, "set INT8 activations");
    }
    check(write(M + 0x280, f.activation_scale) == 0, "set unchanged activation FP32 scale");
  }
  unsigned wait_done(bool expect_fault = false) {
    for (unsigned n = 0; n < 100000; ++n) {
      unsigned status = read(M);
      if (!(status & 1) && (status & 6)) {
        check(status == (expect_fault ? 4U : 2U), "unexpected terminal engine status");
        return status;
      }
    }
    throw std::runtime_error("engine completion timeout");
  }
  unsigned verify(const Fixture &f) {
    unsigned checks = 0;
    for (unsigned unit = 0; unit < f.units; ++unit) {
      for (unsigned group = 0; group < 4; ++group) {
        const int32_t actual = static_cast<int32_t>(read(M + 0x400 + 16 * unit + 4 * group));
        check(actual == f.blocks[unit].expected[group], "integer subgroup mismatch in " + f.name +
              " unit=" + std::to_string(unit) + " subgroup=" + std::to_string(group));
        ++checks;
      }
      // Mirror the public firmware helper, including its repeated packed-scale read.
      uint32_t packed = read(M + 0x600 + 4 * (unit / 2));
      uint16_t scale = uint16_t(packed >> (16 * (unit % 2)));
      uint16_t expected = uint16_t(f.blocks[unit].bytes[0]) | uint16_t(f.blocks[unit].bytes[1]) << 8;
      check(scale == expected, "weight FP16 scale bits changed");
    }
    return checks;
  }
};

static void protocol_tests(Sim &s, const Fixture &fixture) {
  s.set_fixture(fixture); s.configure(fixture);
  check(s.read(Sim::M + 0x280) == fixture.activation_scale, "activation scale readback");
  for (unsigned address = 4096; address < 4160; address += 4) {
    uint32_t expected = 0;
    for (unsigned j = 0; j < 4; ++j) expected |= uint32_t(s.rom[address + j]) << (8 * j);
    check(s.read(Sim::R + address) == expected, "CPU ROM byte path mismatch");
  }
  check(s.write(Sim::R, 0) == 3, "ROM write protection");
  check(s.read(Sim::R + uint32_t(s.rom.size()), 3) == 0, "ROM capacity bound");
  check(s.write(Sim::M + 8, 0) == 2 && s.write(Sim::M + 8, 33) == 2, "invalid unit count");
  check(s.write(Sim::M + 0x200, 0, 0, 7) == 2, "partial MMIO write rejected");
  check(s.write(Sim::M + 0x100, uint32_t(s.rom.size()) - 33) == 0, "set truncated address");
  check(s.write(Sim::M + 4, 1) == 2, "truncated block must be rejected");
  check(s.read(Sim::M) == 4 && s.pending.empty(), "bad address status/no backend request");
  s.configure(fixture);
  // A backend error is injected into the first accepted request, with latency
  // increased so a pipelined implementation has several replies to drain.
  const unsigned old_latency = s.opt.latency;
  s.opt.latency = std::max(32U, old_latency);
  s.fail_request = s.counts.requests + 1;
  check(s.write(Sim::M + 4, 1) == 0, "start fault test");
  check(s.write(Sim::M + 0x200, 0) == 2, "busy mutation must be rejected");
  s.wait_done(true);
  for (unsigned n = 0; !s.pending.empty(); ++n) {
    check(n < 100000, "accepted replies not drained following engine fault"); s.step();
  }
  s.fail_request = 0;
  s.configure(fixture); check(s.write(Sim::M + 4, 1) == 0, "fault recovery dispatch");
  s.wait_done(); s.verify(fixture);
  // CPU ROM must also recover, with the original response ID and no stale beat.
  s.fail_request = s.counts.requests + 1;
  s.read(Sim::R + 4096, 2); s.fail_request = 0;
  s.read(Sim::R + 4096);
  // Reset while multiple storage replies may be outstanding; platform reset
  // synchronously flushes the storage adapter as documented by Sim::reset.
  check(s.write(Sim::M + 4, 1) == 0, "reset test dispatch");
  for (unsigned n = 0; n < 12; ++n) s.step();
  s.reset();
  check(s.read(Sim::M) == 0 && s.read(Sim::M + 16) == 0, "reset status/cycle counter");
  check(s.read(Sim::M + 8) == 32, "reset default unit count");
  s.configure(fixture); check(s.write(Sim::M + 4, 1) == 0, "post-reset dispatch");
  s.wait_done(); s.verify(fixture);
  s.opt.latency = old_latency;
}

int main(int argc, char **argv) {
  Verilated::commandArgs(argc, argv);
  try {
    Options options;
    for (int n = 1; n < argc; ++n) {
      std::string arg(argv[n]); check(n + 1 < argc, "option requires a value");
      std::string value(argv[++n]);
      if (arg == "--fixtures") options.fixtures = value;
      else if (arg == "--latency") options.latency = std::stoul(value);
      else if (arg == "--beat-ii") options.beat_ii = std::stoul(value);
      else if (arg == "--capacity") options.capacity = std::stoul(value);
      else if (arg == "--stall-percent") options.stall_percent = std::stoul(value);
      else if (arg == "--latency-jitter") options.jitter = std::stoul(value);
      else if (arg == "--seed") options.seed = std::stoul(value);
      else if (arg == "--mmio-stall-max") options.mmio_stall_max = std::stoul(value);
      else throw std::runtime_error("unknown option " + arg);
    }
    check(!options.fixtures.empty(), "--fixtures is required");
    check(options.latency >= 1 && options.latency <= 10000 && options.beat_ii >= 1 && options.beat_ii <= 10000,
          "latency/beat II outside 1..10000");
    check(options.capacity >= 1 && options.capacity <= 1024 && options.stall_percent <= 95,
          "invalid storage capacity/stall percentage");
    uint32_t rom_bytes = 0;
    auto fixtures = load(options.fixtures, rom_bytes);
    const auto start = std::chrono::steady_clock::now();
    Sim sim(options, rom_bytes);
    unsigned checks = 0;
    for (const auto &fixture : fixtures) {
      sim.set_fixture(fixture);
      sim.counts.peak = 0;
      const Counters before = sim.counts;
      const uint64_t command_start = sim.ticks;
      sim.configure(fixture);
      const uint64_t dispatch = sim.ticks;
      check(sim.write(Sim::M + 4, 1) == 0, "engine dispatch");
      sim.wait_done();
      const uint64_t completed = sim.ticks;
      checks += sim.verify(fixture);
      const uint64_t finished = sim.ticks;
      const Counters after = sim.counts;
      const uint32_t busy = sim.read(Sim::M + 16);
      check(sim.read(Sim::M + 0x280) == fixture.activation_scale, "FP32 activation scale bits changed");
      check(sim.pending.empty(), "storage reply outstanding after successful completion");
      std::cout << "{\"kind\":\"case\",\"name\":\"" << fixture.name
                << "\",\"units\":" << fixture.units << ",\"alignment\":" << fixture.alignment
                << ",\"stride\":" << fixture.stride << ",\"busy_cycles\":" << busy
                << ",\"axi_testbench_command_cycles\":" << finished - command_start
                << ",\"configuration_cycles\":" << dispatch - command_start
                << ",\"dispatch_to_observed_done_cycles\":" << completed - dispatch
                << ",\"readback_cycles\":" << finished - completed
                << ",\"mmio_reads\":" << after.reads - before.reads
                << ",\"mmio_writes\":" << after.writes - before.writes
                << ",\"storage_requests\":" << after.requests - before.requests
                << ",\"storage_bytes\":" << after.bytes - before.bytes
                << ",\"useful_native_bytes\":" << fixture.units * 34
                << ",\"native_products\":" << fixture.units * 128
                << ",\"peak_outstanding\":" << after.peak
                << ",\"request_stall_cycles\":" << after.request_stalls - before.request_stalls
                << ",\"response_stall_cycles\":" << after.response_stalls - before.response_stalls
                << ",\"status\":\"PASS\"}\n";
    }
    // Use a 32-output case to exercise queued errors at the configured capacity.
    auto full = std::find_if(fixtures.begin(), fixtures.end(), [](const Fixture &f) { return f.units == 32; });
    check(full != fixtures.end(), "protocol suite needs a 32-output fixture");
    protocol_tests(sim, *full);
    const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    std::cout << "{\"kind\":\"summary\",\"status\":\"PASS\",\"cases\":" << fixtures.size()
              << ",\"integer_subgroup_checks\":" << checks
              << ",\"clock_cycles\":" << sim.ticks << ",\"simulation_wall_seconds\":" << seconds
              << ",\"simulated_cycles_per_second\":" << sim.ticks / seconds
              << ",\"protocol_tests\":\"PASS\",\"storage_requests_including_protocol\":" << sim.counts.requests
              << ",\"scope\":\"engine RTL simulation; no full token or hardware timing claim\"}\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "FAIL: " << error.what() << '\n'; return 1;
  }
}
