// Standalone Verilator test for fetch/coral_storage_axi_read.sv.
// Build separately with -GFETCH_DEPTH=N for N=1,4,7 and run with --depth N.
// If HBM_BASE is overridden at elaboration, pass the same --hbm-base value.
// Include a run with HBM_BASE=0x130000000 to cover upper address bits and carry.
// The controller must run compilation and execution on the allocated EC2 host.
#include "Vcoral_storage_axi_read.h"
#include "verilated.h"

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <random>
#include <stdexcept>
#include <string>

namespace {
void require(bool ok, const char* message) {
  if (!ok) throw std::runtime_error(message);
}

uint64_t number(const char* value) {
  char* end = nullptr;
  const uint64_t parsed = std::strtoull(value, &end, 0);
  require(value[0] != '\0' && end != value && *end == '\0', "invalid numeric argument");
  return parsed;
}

struct Config {
  unsigned depth = 4;
  uint64_t hbm_base = 0;
  unsigned seed = 7193;
};

struct Transaction {
  uint32_t address;
  unsigned sequence;
  uint64_t due;
  std::array<uint32_t, 4> data;
  uint8_t response;
  bool last;
};

// Address and sequence both affect every word, exposing address corruption,
// response duplication, or response reordering even when addresses repeat.
std::array<uint32_t, 4> payload(uint32_t address, unsigned sequence) {
  std::array<uint32_t, 4> words{};
  uint32_t value = address ^ (UINT32_C(0x9e3779b9) * (sequence + 1));
  for (unsigned i = 0; i < words.size(); ++i) {
    value ^= value << 13;
    value ^= value >> 17;
    value ^= value << 5;
    words[i] = value ^ (UINT32_C(0x85ebca6b) * (i + 1));
  }
  return words;
}

struct Test {
  static constexpr unsigned kRequests = 1000;
  Config config;
  Vcoral_storage_axi_read dut;
  std::mt19937 rng;
  std::deque<Transaction> backend;
  uint64_t cycles = 0;
  unsigned issued = 0, completed = 0, aborted = 0;
  unsigned peak = 0, simultaneous = 0, full_stalls = 0;
  unsigned ar_stalls = 0, response_stalls = 0;
  unsigned error_responses = 0, bad_last_responses = 0;
  unsigned reset_count = 0;
  bool producer_valid = false, backend_valid = false;
  uint32_t producer_address = 0;
  bool ar_held = false, response_held = false;
  uint64_t held_ar_address = 0;
  std::array<uint32_t, 4> held_data{};
  bool held_error = false;

  explicit Test(Config selected) : config(selected), rng(selected.seed) {}

  void edge() {
    dut.clk = 1;
    dut.eval();
    ++cycles;
  }

  // Reset the adapter, upstream producer, and backing AXI queue together.
  // Outstanding requests are deliberately canceled and counted, not treated
  // as completed. No pre-reset backend response is replayed after reset.
  void reset_pair() {
    aborted += unsigned(backend.size());
    backend.clear();
    producer_valid = false;
    backend_valid = false;
    ar_held = response_held = false;
    dut.request_valid = 0;
    dut.request_addr = 0;
    dut.response_ready = 0;
    dut.m_arready = 0;
    dut.m_rvalid = 0;
    dut.m_rresp = 0;
    dut.m_rlast = 1;
    for (unsigned i = 0; i < 4; ++i) dut.m_rdata[i] = 0;
    dut.reset = 1;
    for (unsigned i = 0; i < 4; ++i) {
      dut.clk = 0;
      dut.eval();
      edge();
    }
    dut.reset = 0;
    // With no accepted AR, even an unsolicited R beat must not be delivered.
    dut.clk = 0;
    dut.m_rvalid = 1;
    dut.response_ready = 1;
    dut.eval();
    require(!dut.response_valid && !dut.m_rready, "reset left response credit active");
    edge();
    dut.m_rvalid = 0;
    dut.response_ready = 0;
    ++reset_count;
  }

  void cycle(unsigned segment_cycle) {
    // Each segment starts by filling all credits, holding a valid response
    // under backpressure, then draining with dense traffic. This guarantees
    // full-depth coverage and simultaneous push/pop for depths above one.
    const bool directed = segment_cycle < 64;
    if (!producer_valid && issued < kRequests && (directed || rng() % 5 != 0)) {
      producer_valid = true;
      constexpr uint32_t boundary_addresses[] = {
          0, 0, 16, UINT32_C(0x7ffffff0), UINT32_C(0x80000000),
          UINT32_C(0xfffffff0), UINT32_C(0xfffffff0), 16};
      producer_address = issued < 8 ? boundary_addresses[issued] :
          uint32_t(rng()) & UINT32_C(0xfffffff0);
    }
    if (!backend_valid && !backend.empty() && backend.front().due <= cycles &&
        (directed || rng() % 4 != 0)) {
      backend_valid = true;
    }

    dut.clk = 0;
    dut.request_valid = producer_valid;
    dut.request_addr = producer_address;
    // Directed AR stalls happen with free credits after the initial fill/hold.
    dut.m_arready = directed ? !(segment_cycle >= 40 && segment_cycle < 45) : rng() % 4 != 0;
    dut.response_ready = directed ? segment_cycle >= 20 : rng() % 3 != 0;
    dut.m_rvalid = backend_valid;
    dut.m_rresp = backend_valid ? backend.front().response : 0;
    dut.m_rlast = backend_valid ? backend.front().last : true;
    for (unsigned i = 0; i < 4; ++i)
      dut.m_rdata[i] = backend_valid ? backend.front().data[i] : 0;
    dut.eval();

    require(dut.m_arid == 0, "AXI requests must use the same ID zero");
    require(dut.m_arlen == 0 && dut.m_arsize == 4 && dut.m_arburst == 1,
            "AXI request must be one aligned 128-bit INCR beat");
    if (dut.m_arvalid) {
      require(producer_valid, "AXI address without an upstream request");
      require(dut.m_araddr == config.hbm_base + uint64_t(producer_address),
              "AXI address differs from HBM_BASE plus native byte offset");
    }
    if (ar_held) {
      require(dut.m_arvalid && dut.m_araddr == held_ar_address,
              "AXI AR changed under backpressure");
    }
    if (response_held) {
      require(dut.response_valid && bool(dut.response_error) == held_error,
              "native response control changed under backpressure");
      for (unsigned i = 0; i < 4; ++i)
        require(dut.response_data[i] == held_data[i],
                "native response data changed under backpressure");
    }

    const bool request_fire = dut.request_valid && dut.request_ready;
    const bool ar_fire = dut.m_arvalid && dut.m_arready;
    const bool response_fire = dut.response_valid && dut.response_ready;
    const bool r_fire = dut.m_rvalid && dut.m_rready;
    require(request_fire == ar_fire, "request and AXI AR handshakes disagree");
    require(response_fire == r_fire, "response and AXI R handshakes disagree");
    require(backend.size() <= config.depth, "configured outstanding depth exceeded");
    if (dut.response_valid) {
      require(backend_valid && !backend.empty(), "response without accepted request");
      const Transaction& expected = backend.front();
      require(expected.data == payload(expected.address, expected.sequence),
              "backend scoreboard payload corruption");
      for (unsigned i = 0; i < 4; ++i)
        require(dut.response_data[i] == expected.data[i], "response data/order mismatch");
      require(bool(dut.response_error) == (expected.response != 0 || !expected.last),
              "RRESP or missing RLAST error was lost");
    }

    ar_held = dut.m_arvalid && !dut.m_arready;
    held_ar_address = dut.m_araddr;
    response_held = dut.response_valid && !dut.response_ready;
    held_error = dut.response_error;
    for (unsigned i = 0; i < 4; ++i) held_data[i] = dut.response_data[i];
    ar_stalls += ar_held;
    response_stalls += response_held;
    if (backend.size() == config.depth && producer_valid && !response_fire) {
      require(!request_fire, "request accepted while all credits remain occupied");
      ++full_stalls;
    }
    simultaneous += request_fire && response_fire;
    edge();

    if (response_fire) {
      require(!backend.empty(), "response credit underflow");
      error_responses += backend.front().response != 0;
      bad_last_responses += !backend.front().last;
      backend.pop_front();
      backend_valid = false;
      ++completed;
    }
    if (request_fire) {
      Transaction transaction{};
      transaction.address = producer_address;
      transaction.sequence = issued;
      transaction.due = cycles + (directed ? 0 : rng() % 20);
      transaction.data = payload(producer_address, issued);
      transaction.response = issued % 13 == 0 ? 2 : issued % 29 == 0 ? 3 : issued % 37 == 0 ? 1 : 0;
      transaction.last = issued % 17 != 0;
      backend.push_back(transaction);
      producer_valid = false;
      ++issued;
    }
    require(backend.size() <= config.depth, "credit overflow after simultaneous handshakes");
    if (backend.size() > peak) peak = unsigned(backend.size());
    require(issued == completed + aborted + backend.size(), "request accounting mismatch");
  }

  void run() {
    reset_pair();
    unsigned segment_cycle = 0;
    bool midflight_reset = false;
    while (issued < kRequests || !backend.empty() || producer_valid) {
      require(cycles < 1000000, "adapter progress timeout");
      if (!midflight_reset && issued >= kRequests / 2 && !backend.empty()) {
        reset_pair();
        midflight_reset = true;
        segment_cycle = 0;
      }
      cycle(segment_cycle++);
    }
    require(issued == kRequests && completed + aborted == kRequests,
            "final request/response accounting mismatch");
    require(midflight_reset && aborted > 0 && reset_count == 2,
            "paired reset did not cancel outstanding requests");
    require(peak == config.depth && full_stalls > 0, "maximum credit depth was not exercised");
    require(ar_stalls > 0 && response_stalls > 0, "both backpressure directions must be exercised");
    require(error_responses > 0 && bad_last_responses > 0, "response error coverage missing");
    // This adapter has no same-edge credit bypass: at depth one, request and
    // response transfers alternate. Larger depths must show simultaneous I/O.
    if (config.depth == 1) require(simultaneous == 0, "unexpected depth-one credit bypass");
    else require(simultaneous > 0, "simultaneous request/response coverage missing");
    dut.final();
    std::printf("{\"status\":\"PASS\",\"fetch_depth\":%u,\"hbm_base\":\"0x%llx\","
                "\"seed\":%u,\"requests\":%u,\"completed\":%u,\"reset_canceled\":%u,"
                "\"peak_outstanding\":%u,\"simultaneous_transfers\":%u,\"full_credit_stalls\":%u,"
                "\"ar_stalls\":%u,\"response_stalls\":%u,\"rresp_errors\":%u,"
                "\"missing_rlast\":%u,\"clock_cycles\":%llu}\n",
                config.depth, static_cast<unsigned long long>(config.hbm_base), config.seed,
                issued, completed, aborted, peak, simultaneous, full_stalls, ar_stalls,
                response_stalls, error_responses, bad_last_responses,
                static_cast<unsigned long long>(cycles));
  }
};
}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  try {
    Config config;
    for (int i = 1; i < argc; ++i) {
      const std::string option(argv[i]);
      require(i + 1 < argc, "every option requires a value");
      const uint64_t value = number(argv[++i]);
      if (option == "--depth") {
        require(value >= 1 && value <= 32, "depth must be in [1,32]");
        config.depth = unsigned(value);
      } else if (option == "--hbm-base") {
        require(value <= UINT64_MAX - UINT32_MAX, "HBM base plus offset must not overflow");
        config.hbm_base = value;
      } else if (option == "--seed") {
        require(value <= UINT32_MAX, "seed exceeds 32 bits");
        config.seed = unsigned(value);
      } else {
        throw std::runtime_error("unknown option: " + option);
      }
    }
    Test test(config);
    test.run();
  } catch (const std::exception& error) {
    std::fprintf(stderr, "FAIL: %s\n", error.what());
    return 1;
  }
  return 0;
}
