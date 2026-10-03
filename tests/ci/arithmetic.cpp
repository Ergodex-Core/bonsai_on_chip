#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#ifdef USE_ARC
#include "arithmetic_top.h"
#else
#include "Vdut.h"
#include "verilated.h"
#endif

int main(int argc, char **argv) {
  try {
    if (argc != 2) throw std::runtime_error("expected trace output path");
    std::ofstream trace(argv[1]);
    if (!trace) throw std::runtime_error("cannot open trace");
#ifdef USE_ARC
    arithmetic_top model;
    auto put = [&](const char *name, uint32_t value) {
      for (const auto &p : arithmetic_topLayout::io) {
        if (std::string(p.name) == name) {
          std::memcpy(model.storage.data() + p.offset, &value, (p.numBits + 7) / 8);
          return;
        }
      }
      throw std::runtime_error(std::string("missing port ") + name);
    };
    auto get = [&](const char *name) -> uint32_t {
      for (const auto &p : arithmetic_topLayout::io) {
        if (std::string(p.name) == name) {
          uint32_t value = 0;
          std::memcpy(&value, model.storage.data() + p.offset, (p.numBits + 7) / 8);
          return p.numBits == 32 ? value : value & ((1u << p.numBits) - 1);
        }
      }
      throw std::runtime_error(std::string("missing port ") + name);
    };
#else
    VerilatedContext context;
    Vdut model{&context};
    auto put = [&](const char *name, uint32_t value) {
#define INPUT(n) if (std::string(name) == #n) { model.n = value; return; }
      INPUT(a); INPUT(b); INPUT(c); INPUT(cin); INPUT(shift); INPUT(mode);
#undef INPUT
      throw std::runtime_error("missing input");
    };
    auto get = [&](const char *name) -> uint32_t {
#define OUTPUT(n) if (std::string(name) == #n) return model.n;
      OUTPUT(sum); OUTPUT(cout); OUTPUT(sum8); OUTPUT(cout8);
      OUTPUT(shifted); OUTPUT(compressed_sum); OUTPUT(compressed_carry);
#undef OUTPUT
      throw std::runtime_error("missing output");
    };
#endif
    uint64_t samples = 0;
    trace << "a,b,c,cin,shift,mode,sum,cout,sum8,cout8,shifted,compressed_sum,compressed_carry\n";
    auto check = [&](uint32_t a, uint32_t b, uint32_t c, uint32_t carry, uint32_t shift, uint32_t mode) {
      put("a", a); put("b", b); put("c", c); put("cin", carry); put("shift", shift); put("mode", mode);
      model.eval();
      const uint64_t total = uint64_t(a) + b + carry;
      const uint32_t total8 = (a & 255) + (b & 255) + carry;
      // Explicit sign extension avoids implementation-defined signed right shift.
      uint32_t shifted = a;
      if (mode == 0) shifted = a << shift;
      if (mode == 1 || mode == 2) shifted = a >> shift;
      if (mode == 2 && shift && (a & 0x80000000u)) shifted |= ~uint32_t(0) << (32 - shift);
      const std::array<const char *, 7> names{"sum", "cout", "sum8", "cout8", "shifted", "compressed_sum", "compressed_carry"};
      const std::array<uint32_t, 7> expected{uint32_t(total), uint32_t(total >> 32), total8 & 255, total8 >> 8,
                                           shifted, a ^ b ^ c, (a & b) | (a & c) | (b & c)};
      trace << a << ',' << b << ',' << c << ',' << carry << ',' << shift << ',' << mode;
      for (size_t i = 0; i < names.size(); ++i) {
        const auto value = get(names[i]);
        if (value != expected[i]) throw std::runtime_error("sample " + std::to_string(samples) + " " + names[i] +
            " expected=" + std::to_string(expected[i]) + " actual=" + std::to_string(value));
        trace << ',' << value;
      }
      trace << '\n'; ++samples;
    };
    // Exhaustive 8-bit adder inputs including carry-in; also exercise upper bits.
    for (uint32_t a = 0; a < 256; ++a)
      for (uint32_t b = 0; b < 256; ++b)
        for (uint32_t cin = 0; cin < 2; ++cin)
          check(a, b, a ^ b, cin, (a + b) & 31, (a ^ b) & 3);
    const std::array<uint32_t, 8> boundaries{0, 1, 0x7fffffff, 0x80000000, 0xffffffff, 0xaaaaaaaa, 0x55555555, 0xfffffffe};
    for (auto a : boundaries) for (auto b : boundaries)
      for (uint32_t shift = 0; shift < 32; ++shift)
        for (uint32_t mode = 0; mode < 4; ++mode)
          for (uint32_t cin = 0; cin < 2; ++cin) check(a, b, ~a, cin, shift, mode);
    uint32_t seed = 0x5eed1234;
    auto random = [&]() { seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5; return seed; };
    for (unsigned i = 0; i < 4096; ++i) {
      const auto a = random(), b = random(), c = random(), config = random();
      check(a, b, c, config & 1, (config >> 1) & 31, (config >> 6) & 3);
    }
    trace.flush();
    if (!trace) throw std::runtime_error("trace write failed");
    std::cout << "PASS samples=" << samples << " checked_outputs=" << samples * 7 << '\n';
  } catch (const std::exception &error) {
    std::cerr << "FAIL: " << error.what() << '\n'; return 1;
  }
}
