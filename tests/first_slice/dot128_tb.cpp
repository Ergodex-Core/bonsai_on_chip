// SPDX-License-Identifier: Apache-2.0
// Independent byte-level arithmetic oracle and latency/backpressure memory model.
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "Vdot128.h"
#include "verilated.h"

using Bytes                      = std::vector<uint8_t>;
using Activations                = std::array<uint8_t, 128>;
constexpr uint64_t kAddressLimit = uint64_t{1} << 48;
constexpr uint32_t kEpoch        = 0x10301234u;

static void check(bool condition, const std::string &message) {
  if (!condition)
    throw std::runtime_error(message);
}

struct Result {
  int32_t sum;
  uint16_t scale;
  uint8_t status;
  uint64_t cycles, stalls, reads;
  bool fatal;
  bool operator==(const Result &other) const {
    return sum == other.sum && scale == other.scale && status == other.status &&
           cycles == other.cycles && stalls == other.stalls && reads == other.reads &&
           fatal == other.fatal;
  }
};

static Result oracle(const Bytes &payload, const Activations &activations, bool ternary) {
  check(payload.size() == (ternary ? 32u : 18u), "oracle payload length");
  const uint16_t scale = ternary ? 0x3c00u : uint16_t(payload[0] | (uint16_t(payload[1]) << 8));
  if (!ternary && (scale & 0x7c00u) == 0x7c00u)
    return {0, 0, 7, 0, 0, 0, false};
  int64_t sum = 0;
  for (size_t i = 0; i < activations.size(); ++i) {
    const int32_t x = activations[i] < 128 ? activations[i] : int32_t(activations[i]) - 256;
    int32_t weight;
    if (ternary) {
      const uint8_t code = (payload[i / 4] >> ((i % 4) * 2)) & 3;
      if (code == 3)
        return {0, 0, 7, 0, 0, 0, false};
      weight = code == 2 ? -1 : code;
    } else {
      weight = (payload[2 + i / 8] & (uint8_t{1} << (i % 8))) ? 1 : -1;
    }
    sum += int64_t(x) * weight;
  }
  check(sum >= -16384 && sum <= 16384, "oracle integer bound");
  return {int32_t(sum), scale, 0, 0, 0, 0, false};
}

class Harness {
  struct Request {
    uint64_t offset;
    uint8_t tag;
    uint32_t epoch;
    bool operator==(const Request &other) const {
      return offset == other.offset && tag == other.tag && epoch == other.epoch;
    }
  };
  struct Response {
    std::array<uint8_t, 64> data{};
    uint8_t tag = 0, status = 0;
    uint32_t epoch = 0;
    uint64_t due   = 0;
  };
  VerilatedContext context;
  Vdot128 dut{&context};
  std::mt19937 random{0x103u};
  std::unordered_map<uint64_t, std::array<uint8_t, 64>> memory;
  std::optional<Response> pending;
  std::optional<Request> held_request;
  std::optional<Result> held_result;
  bool active = false, result_seen = false;
  uint64_t accepted_at = 0, expected_base = 0, accepted_reads = 0, observed_stalls = 0;
  uint32_t accepted_epoch = 0;
  unsigned fault_read     = 0;
  uint8_t fault_status    = 0;
  bool wrong_tag = false, wrong_epoch = false;

  Request request() const {
    return {dut.mem_req_offset_o, uint8_t(dut.mem_req_tag_o), dut.mem_req_epoch_o};
  }
  void drive_memory() {
    dut.mem_req_ready_i  = !block_requests && (!randomized || random() % 4 != 0);
    dut.mem_rsp_valid_i  = pending && ticks >= pending->due && !block_responses;
    dut.mem_rsp_status_i = 0;
    dut.mem_rsp_tag_i    = 0;
    dut.mem_rsp_epoch_i  = 0;
    for (unsigned word = 0; word < 16; ++word)
      dut.mem_rsp_data_i[word] = 0;
    if (dut.mem_rsp_valid_i) {
      dut.mem_rsp_tag_i    = pending->tag;
      dut.mem_rsp_epoch_i  = pending->epoch;
      dut.mem_rsp_status_i = pending->status;
      for (unsigned byte = 0; byte < 64; ++byte)
        dut.mem_rsp_data_i[byte / 4] |= uint32_t(pending->data[byte]) << ((byte % 4) * 8);
    }
  }

 public:
  uint64_t ticks = 0, completed = 0, total_reads = 0, total_stalls = 0;
  bool randomized = true, block_requests = false, block_responses = false;

  Result result() const {
    return {int32_t(dut.result_acc_o), uint16_t(dut.result_scale_o),  uint8_t(dut.result_status_o),
            dut.result_cycles_o,       dut.result_mem_stall_cycles_o, dut.result_read_requests_o,
            bool(dut.fatal_o)};
  }

  void tick() {
    dut.clk_i = 0;
    drive_memory();
    dut.eval();
    if (dut.rst_ni) {
      if (held_request)
        check(dut.mem_req_valid_o && request() == *held_request,
              "request changed under backpressure");
      if (held_result)
        check(dut.result_valid_o && result() == *held_result, "result changed under backpressure");
      held_request = dut.mem_req_valid_o && !dut.mem_req_ready_i ? std::optional<Request>(request())
                                                                 : std::nullopt;
      held_result  = dut.result_valid_o && !dut.result_ready_i ? std::optional<Result>(result())
                                                               : std::nullopt;
      if (dut.cmd_valid_i && dut.cmd_ready_o) {
        check(!active && !pending, "command accepted before prior job drained");
        active          = true;
        result_seen     = false;
        accepted_at     = ticks;
        expected_base   = dut.cmd_weight_offset_i & ~uint64_t{63};
        accepted_epoch  = dut.cmd_epoch_i;
        accepted_reads  = 0;
        observed_stalls = 0;
      }
      if (active && ((dut.mem_req_valid_o && !dut.mem_req_ready_i) ||
                     (dut.mem_rsp_ready_o && !dut.mem_rsp_valid_i)))
        ++observed_stalls;
      if (dut.mem_rsp_valid_i && dut.mem_rsp_ready_o) {
        check(pending.has_value(), "response without outstanding request");
        pending.reset();
      }
      if (dut.mem_req_valid_o && dut.mem_req_ready_i) {
        check(active && !pending, "more than one outstanding memory read");
        const auto req = request();
        check(accepted_reads < 2, "too many line reads");
        check(req.offset == expected_base + accepted_reads * 64,
              "incorrect line offset / 4K split");
        check(req.tag == accepted_reads && req.epoch == accepted_epoch,
              "request identity mismatch");
        check((req.offset & 63) == 0 && req.offset < kAddressLimit, "unaligned or wrapped request");
        ++accepted_reads;
        ++total_reads;
        Response response;
        response.tag    = req.tag;
        response.epoch  = req.epoch;
        response.due    = ticks + 1 + (randomized ? random() % 17 : 0);
        const auto line = memory.find(req.offset);
        if (line == memory.end())
          response.status = 2;
        else
          response.data = line->second;
        if (fault_read == accepted_reads) {
          response.status = fault_status;
          response.tag ^= wrong_tag ? 0x40 : 0;
          response.epoch ^= wrong_epoch ? 1 : 0;
        }
        pending = response;
      }
      if (dut.result_valid_o && dut.result_ready_i) {
        check(active && result_seen && !pending, "completion without a drained job");
        active = false;
        ++completed;
        total_stalls += observed_stalls;
      }
    } else {
      check(!dut.cmd_ready_o && !dut.mem_req_valid_o && !dut.mem_rsp_ready_o && !dut.result_valid_o,
            "handshake active during reset");
    }
    dut.clk_i = 1;
    context.timeInc(1);
    dut.eval();
    ++ticks;
    if (active && dut.result_valid_o && !result_seen) {
      result_seen = true;
      check(dut.result_cycles_o == ticks - 1 - accepted_at, "elapsed-cycle counter mismatch");
      check(dut.result_mem_stall_cycles_o == observed_stalls, "memory-stall counter mismatch");
      check(dut.result_read_requests_o == accepted_reads, "read-request counter mismatch");
    }
    dut.clk_i = 0;
    context.timeInc(1);
    dut.eval();
  }

  void reset() {
    // This is a coordinated consumer + backend reset; outstanding reads cancel.
    pending.reset();
    held_request.reset();
    held_result.reset();
    active         = false;
    result_seen    = false;
    block_requests = block_responses = false;
    dut.cmd_valid_i                  = 0;
    dut.result_ready_i               = 0;
    dut.rst_ni                       = 0;
    tick();
    tick();
    check(!dut.fatal_o && result().sum == 0 && result().scale == 0, "reset did not clear results");
    dut.rst_ni = 1;
    tick();
    check(dut.cmd_ready_o && !dut.result_valid_o, "reset did not return to idle");
  }

  void inject(unsigned read, uint8_t status, bool tag = false, bool epoch = false) {
    fault_read   = read;
    fault_status = status;
    wrong_tag    = tag;
    wrong_epoch  = epoch;
  }

  void begin(const Bytes &payload, const Activations &activations, bool ternary, uint64_t offset) {
    check(dut.cmd_ready_o && !active, "engine not idle at command start");
    check(offset < kAddressLimit, "test offset exceeds interface width");
    memory.clear();
    const uint64_t base = offset & ~uint64_t{63};
    for (unsigned line = 0; line < 2 && base + line * 64 < kAddressLimit; ++line) {
      std::array<uint8_t, 64> bytes;
      for (auto &byte : bytes)
        byte = uint8_t(random());
      memory[base + line * 64] = bytes;
    }
    for (size_t i = 0; i < payload.size() && offset + i < kAddressLimit; ++i)
      memory[(offset + i) & ~uint64_t{63}][(offset + i) & 63] = payload[i];
    dut.cmd_weight_offset_i = offset;
    dut.cmd_format_i        = ternary;
    dut.cmd_epoch_i         = kEpoch;
    for (unsigned word = 0; word < 32; ++word)
      dut.cmd_activations_i[word] = 0;
    for (unsigned byte = 0; byte < 128; ++byte)
      dut.cmd_activations_i[byte / 4] |= uint32_t(activations[byte]) << ((byte % 4) * 8);
    dut.cmd_valid_i    = 1;
    dut.result_ready_i = 0;
    tick();
    dut.cmd_valid_i = 0;
    // The accepted descriptor and activations must have been latched.
    dut.cmd_weight_offset_i = 123;
    dut.cmd_epoch_i         = ~kEpoch;
    dut.cmd_format_i        = !ternary;
    for (unsigned word = 0; word < 32; ++word)
      dut.cmd_activations_i[word] = random();
  }

  void await_result() {
    unsigned budget = 2000;
    while (!dut.result_valid_o && budget--)
      tick();
    check(dut.result_valid_o, "result timeout");
  }

  Result run(const Bytes &payload, const Activations &activations, bool ternary, uint64_t offset,
             std::optional<uint8_t> status_override = std::nullopt, bool expect_fatal = false) {
    begin(payload, activations, ternary, offset);
    await_result();
    Result expected = oracle(payload, activations, ternary);
    if (status_override)
      expected = {0, 0, *status_override, 0, 0, 0, expect_fatal};
    const Result actual = result();
    check(actual.status == expected.status, "result status mismatch");
    check(actual.sum == expected.sum, "exact integer arithmetic mismatch");
    check(actual.scale == expected.scale, "raw scale mismatch");
    check(actual.fatal == expect_fatal, "fatal latch mismatch");
    // Competing command contents cannot alter a held result or start a new job.
    dut.cmd_valid_i = 1;
    for (unsigned hold = 0, count = 1 + random() % 13; hold < count; ++hold) {
      check(!dut.cmd_ready_o, "accepted command while completion was held");
      tick();
      check(result() == actual, "held result changed");
    }
    dut.cmd_valid_i    = 0;
    dut.result_ready_i = 1;
    tick();
    dut.result_ready_i = 0;
    check(!dut.result_valid_o && bool(dut.cmd_ready_o) == !expect_fatal, "completion retirement");
    if (expect_fatal) {
      for (unsigned hold = 0; hold < 5; ++hold) {
        tick();
        check(dut.fatal_o && !dut.cmd_ready_o && !dut.mem_req_valid_o, "fatal allowed ID reuse");
      }
    }
    inject(0, 0);
    return actual;
  }
};

static Bytes native_payload(std::mt19937 &random, uint16_t scale = 0x3555) {
  Bytes bytes(18);
  bytes[0] = uint8_t(scale);
  bytes[1] = uint8_t(scale >> 8);
  for (unsigned i = 2; i < 18; ++i)
    bytes[i] = uint8_t(random());
  return bytes;
}

static Bytes ternary_payload(std::mt19937 &random) {
  Bytes bytes(32, 0);
  for (unsigned i = 0; i < 128; ++i)
    bytes[i / 4] |= uint8_t((random() % 3) << ((i % 4) * 2));
  return bytes;
}

static Activations activations(std::mt19937 &random) {
  Activations bytes;
  for (auto &byte : bytes)
    byte = uint8_t(random());
  return bytes;
}

int main(int argc, char **argv) {
  try {
    std::string external_block;
    uint64_t external_offset = 0;
    for (int i = 1; i < argc; ++i) {
      const std::string option = argv[i];
      check(i + 1 < argc, "option needs an argument");
      if (option == "--q1-block")
        external_block = argv[++i];
      else if (option == "--offset")
        external_offset = std::stoull(argv[++i], nullptr, 0);
      else
        throw std::runtime_error("unknown option: " + option);
    }
    Harness h;
    h.reset();
    std::mt19937 random{0x103beefu};
    unsigned native_cases = 0, ternary_cases = 0, error_cases = 0, reset_cases = 0;
    const std::array<uint16_t, 8> scales = {0, 0x8000, 1, 0x8001, 0x3c00, 0xbc00, 0x7bff, 0xfbff};
    // All offsets exercise one/two-line assembly, including real 4 KiB crossings.
    for (unsigned offset = 0; offset < 64; ++offset) {
      h.run(native_payload(random, scales[offset % scales.size()]), activations(random), false,
            0xfc0 + offset);
      ++native_cases;
      h.run(ternary_payload(random), activations(random), true, 0xfc0 + offset);
      ++ternary_cases;
    }
    Activations minimum;
    minimum.fill(0x80);
    for (bool ternary : {false, true}) {
      for (bool negative : {false, true}) {
        Bytes payload(ternary ? 32 : 18,
                      ternary ? (negative ? 0xaa : 0x55) : (negative ? 0 : 0xff));
        if (!ternary) {
          payload[0] = 0;
          payload[1] = 0x3c;
        }
        const auto value = h.run(payload, minimum, ternary, 4090);
        check(value.sum == (negative ? 16384 : -16384), "signed -128 endpoint");
        ternary ? ++ternary_cases : ++native_cases;
      }
    }
    // Every lane separately proves zero-weight behavior, rather than zero activations alone.
    for (unsigned lane = 0; lane < 128; ++lane) {
      Bytes payload(32, 0x55);
      payload[lane / 4] &= uint8_t(~(3u << ((lane % 4) * 2)));
      Activations x{};
      x[lane] = 0x80;
      check(h.run(payload, x, true, 0x103f).sum == 0, "zero-weight lane");
      ++ternary_cases;
    }
    for (unsigned i = 0; i < 128; ++i) {
      const bool ternary = i & 1;
      h.run(ternary ? ternary_payload(random) : native_payload(random), activations(random),
            ternary, (uint64_t{1} << 36) + (random() % 8192));
      ternary ? ++ternary_cases : ++native_cases;
    }
    for (unsigned lane : {0u, 63u, 127u}) {
      Bytes payload(32, 0);
      payload[lane / 4] |= uint8_t(3u << ((lane % 4) * 2));
      h.run(payload, activations(random), true, 4095);
      ++error_cases;
    }
    for (uint16_t scale : {0x7c00, 0xfc00, 0x7e00, 0xfe00}) {
      h.run(native_payload(random, scale), activations(random), false, 4090);
      ++error_cases;
    }
    for (uint8_t status = 1; status <= 7; ++status) {
      for (unsigned read : {1u, 2u}) {
        h.inject(read, status);
        const auto result = h.run(native_payload(random), activations(random), false, 4090, status);
        check(result.reads == read, "read continued after backend error");
        ++error_cases;
      }
    }
    for (bool ternary : {false, true}) {
      const auto payload  = ternary ? ternary_payload(random) : native_payload(random);
      const auto boundary = kAddressLimit - payload.size();
      check(h.run(payload, activations(random), ternary, boundary).reads == 1,
            "last legal payload was not accepted in final line");
      ternary ? ++ternary_cases : ++native_cases;
      const auto crossing = h.run(payload, activations(random), ternary, boundary + 1, 2);
      check(crossing.reads == 0 && crossing.cycles == 0,
            "first illegal payload was not rejected before dispatch");
      ++error_cases;
      const auto result = h.run(ternary ? ternary_payload(random) : native_payload(random),
                                activations(random), ternary, kAddressLimit - 3, 2);
      check(result.reads == 0 && result.cycles == 0, "wrapped address dispatched");
      ++error_cases;
    }
    for (unsigned read : {1u, 2u}) {
      for (bool corrupt_epoch : {false, true}) {
        h.inject(read, 0, !corrupt_epoch, corrupt_epoch);
        h.run(native_payload(random), activations(random), false, 4090, 7, true);
        h.reset();
        ++error_cases;
      }
    }
    h.randomized = false;
    for (unsigned phase = 0; phase < 4; ++phase) {
      h.block_requests  = phase == 0;
      h.block_responses = phase == 1;
      h.begin(native_payload(random), activations(random), false, 4090);
      if (phase == 3)
        h.await_result();
      else
        for (unsigned tick = 0, count = phase == 2 ? 20 : 5; tick < count; ++tick)
          h.tick();
      h.reset();
      for (unsigned tick = 0; tick < 5; ++tick)
        h.tick();
      h.run(native_payload(random), activations(random), false, 0);
      ++native_cases;
      ++reset_cases;
    }
    unsigned external_cases = 0;
    if (!external_block.empty()) {
      std::ifstream stream(external_block, std::ios::binary);
      check(bool(stream), "cannot open external native block");
      const Bytes payload{std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
      check(payload.size() == 18, "external block must contain exactly 18 native bytes");
      h.randomized = true;
      for (unsigned i = 0; i < 8; ++i) {
        h.run(payload, activations(random), false, external_offset);
        ++external_cases;
      }
    }
    check(h.total_stalls > 0, "no backpressure/latency exercised");
    check(h.completed == native_cases + ternary_cases + error_cases + external_cases,
          "completion inventory mismatch");
    std::cout << "{\"status\":\"PASS\",\"native_unit_cases\":" << native_cases
              << ",\"ternary_unit_cases\":" << ternary_cases
              << ",\"expected_error_cases\":" << error_cases
              << ",\"coordinated_reset_cases\":" << reset_cases
              << ",\"external_native_block_cases\":" << external_cases
              << ",\"completed_commands\":" << h.completed << ",\"line_requests\":" << h.total_reads
              << ",\"memory_wait_cycles\":" << h.total_stalls << ",\"testbench_cycles\":" << h.ticks
              << ",\"seed\":\"0x103beef\"}\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "DOT128 FAIL: " << error.what() << '\n';
    return 1;
  }
}
