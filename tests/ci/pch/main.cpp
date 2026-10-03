#include <cinttypes>
#include <cstdio>

#include "Vtop.h"
#include "verilated.h"

extern "C" int fixture_bias();

int main() {
  VerilatedContext context;
  Vtop model{&context};
  uint8_t state     = 0;
  uint64_t checksum = 1469598103934665603ULL;
  unsigned samples  = 0;
  if (fixture_bias() != 42) {
    std::fprintf(stderr, "Compilation flags changed the fixture result\n");
    return 1;
  }
  auto check = [&]() {
    const unsigned expected = (state ^ model.mask) + model.data;
    if (model.state != state || model.result != expected) {
      std::fprintf(stderr, "sample %u: state=%u/%u result=%u/%u\n", samples, model.state, state,
                   model.result, expected);
      return false;
    }
    checksum = (checksum ^ (model.result + (unsigned(model.state) << 16))) * 1099511628211ULL;
    ++samples;
    return true;
  };
  model.clk    = 0;
  model.reset  = 1;
  model.enable = 0;
  model.data   = 0;
  model.mask   = 0;
  model.eval();
  model.clk = 1;
  model.eval();
  for (unsigned i = 0; i < 4096; ++i) {
    model.clk    = 0;
    model.reset  = i % 113 == 0;
    model.enable = i % 5 != 0;
    model.data   = (i * 73 + 19) & 255;
    model.mask   = (i * 29 + 7) & 255;
    model.eval();
    if (!check())
      return 1;
    model.clk = 1;
    if (model.reset)
      state = 0;
    else if (model.enable)
      state = (state + model.data) & 255;
    model.eval();
    if (!check())
      return 1;
    // Input changes without a clock edge must leave sequential state intact.
    model.data ^= 255;
    model.mask ^= 91;
    model.eval();
    if (!check())
      return 1;
    model.clk = 0;
    model.eval();
    if (!check())
      return 1;
  }
  model.final();
  std::printf("PASS samples=%u checksum=%016" PRIx64 " flags=42\n", samples, checksum);
  return 0;
}
