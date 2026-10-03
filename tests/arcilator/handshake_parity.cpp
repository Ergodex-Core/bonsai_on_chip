// Shared, deterministic pilot driver; the repository RTL is compiled unchanged.
#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#ifndef PIPE_STAGES
#error PIPE_STAGES must match the elaborated RTL parameter
#endif
#ifndef REMOVE_BUBBLES
#error REMOVE_BUBBLES must match the elaborated RTL parameter
#endif
static_assert(PIPE_STAGES >= 1 && PIPE_STAGES <= 16);

#ifdef USE_ARC
#include "handshake_multistage_ctrl.h"
extern "C" void arcRuntimeIR_simStepOnce(uint8_t *, uint64_t, void (*)(uint8_t *));
extern "C" void arcRuntimeIR_simStepSettled(uint8_t *, uint64_t, void (*)(uint8_t *));
#else
#include "Vdut.h"
#include "verilated.h"
#endif

struct Port {
  void *data;
  unsigned bits;
  size_t bytes;
};
class Simulator {
#ifdef USE_ARC
  handshake_multistage_ctrl model;
#else
  VerilatedContext context;
  Vdut model{&context};
#endif
  std::unordered_map<std::string, Port> ports;

 public:
  Simulator() {
#ifdef USE_ARC
    for (const auto &p : handshake_multistage_ctrlLayout::io)
      ports.emplace(p.name, Port{model.storage.data() + p.offset, p.numBits, (p.numBits + 7) / 8});
#else
#define PORT(n, bits) ports.emplace(#n, Port{&model.n, bits, sizeof(model.n)})
    PORT(clk, 1);
    PORT(rst_n, 1);
    PORT(up_valid, 1);
    PORT(up_ready, 1);
    PORT(down_valid, 1);
    PORT(down_ready, 1);
    PORT(flush, 1);
    PORT(reg_enable, PIPE_STAGES);
    PORT(valids, PIPE_STAGES);
    PORT(busy, 1);
#undef PORT
#endif
    for (const auto &n : {"clk", "rst_n", "up_valid", "up_ready", "down_valid", "down_ready",
                          "flush", "reg_enable", "valids", "busy"})
      if (!ports.count(n))
        throw std::runtime_error("missing port " + std::string(n));
    if (ports.at("valids").bits != PIPE_STAGES || ports.at("reg_enable").bits != PIPE_STAGES)
      throw std::runtime_error("generated metadata disagrees with PIPE_STAGES");
  }
  void set(const char *name, uint32_t v) {
    const auto &p = ports.at(name);
    if (p.bytes > sizeof(v))
      throw std::runtime_error("unsupported input width");
    std::memcpy(p.data, &v, p.bytes);
  }
  uint32_t get(const char *name) const {
    const auto &p = ports.at(name);
    if (p.bytes > sizeof(uint32_t))
      throw std::runtime_error("unsupported output width");
    uint32_t v = 0;
    std::memcpy(&v, p.data, p.bytes);
    return v & ((uint32_t{1} << p.bits) - 1);
  }
  void evaluate() {
#ifdef USE_ARC
    auto step = handshake_multistage_ctrl::settlesInOneEval() ? arcRuntimeIR_simStepOnce
                                                              : arcRuntimeIR_simStepSettled;
    step(
        model.storage.data(), handshake_multistage_ctrlLayout::numStateBytes,
        +[](uint8_t *state) { handshake_multistage_ctrl_eval(state); });
#else
    model.eval();
#endif
  }
};

struct Inputs {
  bool clock = false, reset_n = true, valid = false, ready = false, flush = false;
};
struct Outputs {
  uint32_t ready, valid, enables, occupied, busy;
};

// Independent behavioral oracle: occupied pipeline slots move only when their
// receiver can accept. No DUT state or generated state offsets feed the oracle.
class PipelineOracle {
  std::array<bool, PIPE_STAGES> occupied{};
  bool previous_clock = false;
  std::array<bool, PIPE_STAGES> receivers(const Inputs &i) const {
    std::array<bool, PIPE_STAGES> accept{};
    if (REMOVE_BUBBLES) {
      bool receiver_accepts = i.ready;
      for (int k = PIPE_STAGES - 1; k >= 0; --k) {
        accept[k]        = !occupied[k] || receiver_accepts;
        receiver_accepts = accept[k];
      }
    } else {
      accept.fill(!occupied.back() || i.ready);
    }
    return accept;
  }

 public:
  Outputs apply(const Inputs &i) {
    if (!i.reset_n)
      occupied.fill(false);
    else if (!previous_clock && i.clock) {
      if (i.flush)
        occupied.fill(false);
      else {
        const auto old    = occupied;
        const auto accept = receivers(i);
        for (int k = 0; k < PIPE_STAGES; ++k)
          if (accept[k])
            occupied[k] = k == 0 ? i.valid : old[k - 1];
      }
    }
    previous_clock      = i.clock;
    const auto accept   = receivers(i);
    uint32_t valid_bits = 0, enables = 0;
    for (int k = 0; k < PIPE_STAGES; ++k) {
      if (occupied[k])
        valid_bits |= 1u << k;
      if (accept[k] && (!REMOVE_BUBBLES || (k == 0 ? i.valid : occupied[k - 1])))
        enables |= 1u << k;
    }
    return {accept[0], occupied.back(), enables, valid_bits, valid_bits != 0};
  }
};

int main(int argc, char **argv) {
  try {
    if (argc != 2)
      throw std::runtime_error("usage: handshake_parity TRACE.csv");
    std::ofstream trace(argv[1]);
    if (!trace)
      throw std::runtime_error("cannot create trace");
    trace << "step,phase,stages,remove_bubbles,clock,reset_n,up_valid,down_ready,flush,up_ready,"
             "down_valid,reg_enable,valids,busy\n";
    Simulator sim;
    PipelineOracle oracle;
    Inputs input;
    uint64_t samples = 0, cycles = 0;
    bool previous_sample_clock = false;
    auto sample                = [&](const char *phase) {
      if (input.clock && !previous_sample_clock)
        ++cycles;
      previous_sample_clock = input.clock;
      sim.set("clk", input.clock);
      sim.set("rst_n", input.reset_n);
      sim.set("up_valid", input.valid);
      sim.set("down_ready", input.ready);
      sim.set("flush", input.flush);
      sim.evaluate();
      const Outputs expected = oracle.apply(input);
      const Outputs actual{sim.get("up_ready"), sim.get("down_valid"), sim.get("reg_enable"),
                           sim.get("valids"), sim.get("busy")};
      const std::array<uint32_t, 5> e{expected.ready, expected.valid, expected.enables,
                                      expected.occupied, expected.busy};
      const std::array<uint32_t, 5> a{actual.ready, actual.valid, actual.enables, actual.occupied,
                                      actual.busy};
      const std::array<const char *, 5> names{"up_ready", "down_valid", "reg_enable", "valids",
                                              "busy"};
      for (size_t k = 0; k < a.size(); ++k)
        if (a[k] != e[k])
          throw std::runtime_error("sample " + std::to_string(samples) + " phase " + phase + " " +
                                                  names[k] + " expected=" + std::to_string(e[k]) +
                                                  " actual=" + std::to_string(a[k]));
      trace << samples++ << ',' << phase << ',' << PIPE_STAGES << ',' << REMOVE_BUBBLES << ','
            << input.clock << ',' << input.reset_n << ',' << input.valid << ',' << input.ready
            << ',' << input.flush;
      for (auto value : a)
        trace << ',' << value;
      trace << '\n';
    };
    auto cycle = [&](bool valid, bool ready, bool flush) {
      input.clock = false;
      input.valid = valid;
      input.ready = ready;
      input.flush = flush;
      sample("before-rise");
      input.clock = true;
      sample("after-rise");
      input.clock = false;
      sample("after-fall");
    };
    // Establish an actual falling reset edge before observing initial state.
    sim.set("clk", 0);
    sim.set("rst_n", 1);
    sim.set("up_valid", 0);
    sim.set("down_ready", 0);
    sim.set("flush", 0);
    sim.evaluate();
    input.reset_n = false;
    sample("async-reset-low");
    input.reset_n = true;
    sample("release-reset-low");
    // Fill, hold under downstream backpressure, replace, drain, and make bubbles.
    for (int k = 0; k < PIPE_STAGES + 4; ++k)
      cycle(true, false, false);
    for (int k = 0; k < 8; ++k)
      cycle(k % 2, false, false);
    for (int k = 0; k < 8; ++k)
      cycle(true, true, false);
    for (int k = 0; k < PIPE_STAGES + 2; ++k)
      cycle(false, true, false);
    for (int k = 0; k < 20; ++k)
      cycle(k % 3 == 0, k % 4 == 0, false);
    // Flush is synchronous, including when a full pipeline is stalled.
    for (int k = 0; k < PIPE_STAGES + 2; ++k)
      cycle(true, false, false);
    cycle(true, false, true);
    cycle(true, true, true);
    cycle(false, false, false);
    // Reset while full with clock high: clear must occur without another edge.
    for (int k = 0; k < PIPE_STAGES + 2; ++k)
      cycle(true, false, false);
    input.clock = true;
    sample("high-before-reset");
    input.reset_n = false;
    sample("async-reset-high");
    input.reset_n = true;
    sample("release-reset-high");
    input.clock = false;
    sample("fall-after-reset");
    // Reset dominates flush and valid under backpressure. Repeated evaluation
    // at an unchanged clock must not clock data into the just-cleared pipeline.
    for (int k = 0; k < PIPE_STAGES + 2; ++k)
      cycle(true, false, false);
    input.reset_n = false;
    input.flush = true;
    input.clock = true;
    sample("reset-flush-stalled-rise");
    sample("reset-flush-repeat-no-edge");
    input.reset_n = true;
    sample("release-reset-flush-no-edge");
    input.clock = false;
    sample("reset-flush-fall");
    cycle(true, false, true);
    // Deterministic stimuli, including input toggles without a clock edge.
    uint32_t random = 0x5eed1234u;
    for (int k = 0; k < 2048; ++k) {
      random ^= random << 13;
      random ^= random >> 17;
      random ^= random << 5;
      input.clock   = false;
      input.reset_n = k % 127 != 0;
      input.valid   = random & 1;
      input.ready   = random & 2;
      input.flush   = (random & 31) == 0;
      sample("random-low");
      input.ready = !input.ready;
      sample("ready-toggle-no-edge");
      input.clock = true;
      sample("random-rise");
      input.flush = !input.flush;
      sample("flush-toggle-no-edge");
      input.clock = false;
      sample("random-fall");
    }
    input.reset_n = true;
    input.flush   = false;
    for (int k = 0; k < PIPE_STAGES + 2; ++k)
      cycle(false, true, false);
    if (sim.get("busy"))
      throw std::runtime_error("final drain did not empty pipeline");
    trace.flush();
    if (!trace)
      throw std::runtime_error("trace write failed");
    std::cout << "PASS stages=" << PIPE_STAGES << " remove_bubbles=" << REMOVE_BUBBLES
              << " rising_edges=" << cycles << " samples=" << samples
              << " checked_outputs=" << samples * 5 << '\n';
    return 0;
  } catch (const std::exception &e) {
    std::cerr << "FAIL: " << e.what() << '\n';
    return 1;
  }
}
