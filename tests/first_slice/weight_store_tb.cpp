// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// Public-port scoreboard; no DUT internals are read, forced, or bypassed.
#include <array>
#include <cstdint>
#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>

#include "Vweight_store.h"
#include "verilated.h"

namespace {
using Line = std::array<uint32_t, 16>;
void Require(bool value, const std::string &message) {
  if (!value)
    throw std::runtime_error(message);
}
Line MemoryLine(uint64_t address) {
  Line data{};
  for (unsigned i = 0; i < data.size(); ++i)
    data[i] = uint32_t(address) ^ (0x9e3779b9U * (i + 1));
  return data;
}
struct Expected {
  uint64_t offset;
  uint32_t epoch;
  unsigned status;
  bool issued    = false;
  bool completed = false;
  Line data{};
};
struct Physical {
  unsigned tag;
  uint64_t address;
  unsigned delay;
};
struct Response {
  unsigned tag;
  uint32_t epoch;
  unsigned status;
  Line data;
  bool operator==(const Response &other) const {
    return tag == other.tag && epoch == other.epoch && status == other.status && data == other.data;
  }
};
class Bench {
 public:
  VerilatedContext context;
  Vweight_store dut{&context};
  std::map<unsigned, Expected> outstanding;
  std::map<unsigned, unsigned> retire_count;
  std::optional<Physical> physical;
  bool accept_memory = true, return_memory = true, response_error = false;
  unsigned memory_delay = 0;
  uint64_t cycles = 0, accepted = 0, retired = 0, physical_reads = 0, physical_returns = 0;
  bool accepted_last = false;
  std::optional<unsigned> retired_last;

  Bench() {
    dut.clk_i                   = 0;
    dut.rst_ni                  = 0;
    dut.cfg_write_i             = 0;
    dut.cfg_waddr_i             = 0;
    dut.cfg_raddr_i             = 0;
    dut.cfg_wdata_i             = 0;
    dut.cfg_wstrb_i             = 15;
    dut.loader_idle_i           = 1;
    dut.backend_ready_i         = 1;
    dut.client_fault_i          = 0;
    dut.loader_write_rejected_i = 0;
    dut.req_valid_i             = 0;
    dut.req_offset_i            = 0;
    dut.req_tag_i               = 0;
    dut.req_epoch_i             = 0;
    dut.rsp_ready_i             = 0;
    dut.mem_req_ready_i         = 0;
    dut.mem_rsp_valid_i         = 0;
    dut.mem_rsp_error_i         = 0;
    for (unsigned i = 0; i < 16; ++i)
      dut.mem_rsp_data_i[i] = 0;
    RawClock();
    RawClock();
    dut.rst_ni = 1;
    dut.eval();
  }
  ~Bench() { dut.final(); }
  uint32_t Read(unsigned address) {
    dut.cfg_raddr_i = address;
    dut.eval();
    Require(!dut.cfg_rerror_o, "unexpected CSR read error");
    return dut.cfg_rdata_o;
  }
  Response CurrentResponse() {
    Response result{dut.rsp_tag_o, dut.rsp_epoch_o, dut.rsp_status_o, {}};
    for (unsigned i = 0; i < result.data.size(); ++i)
      result.data[i] = dut.rsp_data_o[i];
    return result;
  }
  void Tick() {
    const unsigned state = Read(4) & 7;
    dut.mem_req_ready_i  = accept_memory;
    dut.mem_rsp_valid_i  = physical && physical->delay == 0 && return_memory;
    dut.mem_rsp_error_i  = response_error;
    if (physical) {
      const auto data = MemoryLine(physical->address);
      for (unsigned i = 0; i < data.size(); ++i)
        dut.mem_rsp_data_i[i] = data[i];
    }
    dut.eval();
    if (held_response) {
      Require(dut.rsp_valid_o && CurrentResponse() == *held_response,
              "held response changed under backpressure/fault");
    }
    if (held_request) {
      Require(dut.mem_req_valid_o && dut.mem_req_addr_o == *held_request,
              "held backend request changed under backpressure/fault");
    }
    const bool memory_response = dut.mem_rsp_valid_i && dut.mem_rsp_ready_o;
    const bool fatal = dut.client_fault_i || ((state == 3 || state == 4) && !dut.backend_ready_i) ||
                       (memory_response && dut.mem_rsp_error_i);
    const bool already_faulted = state == 5;
    accepted_last              = dut.req_valid_i && dut.req_ready_o;
    retired_last.reset();
    if (accepted_last) {
      Require(outstanding.size() < 16, "more than sixteen accepted outstanding requests");
      Require(!outstanding.count(dut.req_tag_i), "duplicate tag was accepted");
      unsigned status = dut.req_offset_i % 64 ? 1
                                              : (dut.req_offset_i > dut.image_bytes_o - 64
                                                     ? 2
                                                     : (dut.req_epoch_i != dut.epoch_o ? 3 : 0));
      Expected expected{dut.req_offset_i, dut.req_epoch_i, status};
      expected.completed = status != 0;
      outstanding.emplace(dut.req_tag_i, expected);
      ++accepted;
    }
    if (memory_response) {
      Require(physical.has_value(), "backend response has no accepted physical request");
      auto found = outstanding.find(physical->tag);
      if (found != outstanding.end() && !found->second.completed) {
        found->second.completed = true;
        found->second.status    = fatal || already_faulted ? 4 : 0;
        found->second.data      = found->second.status ? Line{} : MemoryLine(physical->address);
      }
      physical.reset();
      ++physical_returns;
    } else if (physical && physical->delay) {
      --physical->delay;
    }
    if (dut.mem_req_valid_o && dut.mem_req_ready_i) {
      Require(!physical, "multiple physical requests without independent IDs");
      auto found = outstanding.end();
      for (auto it = outstanding.begin(); it != outstanding.end(); ++it) {
        if (!it->second.issued && it->second.offset + dut.image_base_o == dut.mem_req_addr_o) {
          found = it;
          break;
        }
      }
      // After a fatal event, the logical response may already have retired,
      // while an already-presented physical request still must handshake.
      unsigned tag = late_tag;
      if (found != outstanding.end()) {
        tag                  = found->first;
        found->second.issued = true;
      } else {
        Require(already_faulted && held_request.has_value(), "untracked backend read");
      }
      physical = Physical{tag, dut.mem_req_addr_o, memory_delay};
      ++physical_reads;
    }
    if (fatal && state != 0 && state != 1 && state != 2) {
      for (auto &item : outstanding) {
        if (!item.second.completed) {
          item.second.completed = true;
          item.second.status    = 4;
          item.second.data      = {};
        }
      }
      if (accepted_last) {
        auto &item     = outstanding.at(dut.req_tag_i);
        item.completed = true;
        item.status    = 4;
        item.data      = {};
      }
    }
    if (dut.rsp_valid_o) {
      const auto response = CurrentResponse();
      const auto found    = outstanding.find(response.tag);
      Require(found != outstanding.end(), "unexpected/duplicate logical response");
      Require(found->second.completed, "logical response preceded its oracle completion");
      Require(response.epoch == found->second.epoch && response.status == found->second.status &&
                  response.data == found->second.data,
              "logical response tag/epoch/status/data mismatch");
      if (dut.rsp_ready_i) {
        outstanding.erase(found);
        ++retire_count[response.tag];
        ++retired;
        retired_last = response.tag;
      }
    }
    held_response =
        dut.rsp_valid_o && !dut.rsp_ready_i ? std::optional(CurrentResponse()) : std::nullopt;
    if (dut.mem_req_valid_o && !dut.mem_req_ready_i) {
      held_request = dut.mem_req_addr_o;
      for (const auto &item : outstanding)
        if (item.second.offset + dut.image_base_o == dut.mem_req_addr_o)
          late_tag = item.first;
    } else {
      held_request.reset();
    }
    RawClock();
    ++cycles;
  }
  void Write(unsigned address, uint32_t value, bool error = false, unsigned strobe = 15) {
    dut.cfg_write_i = 1;
    dut.cfg_waddr_i = address;
    dut.cfg_wdata_i = value;
    dut.cfg_wstrb_i = strobe;
    dut.eval();
    Require(bool(dut.cfg_werror_o) == error,
            "unexpected CSR write acceptance at " + std::to_string(address));
    Tick();
    dut.cfg_write_i = 0;
    dut.cfg_wstrb_i = 15;
    dut.eval();
  }
  void Configure() {
    Write(0x110, 0x10000);
    Write(0x114, 0);
    Write(0x118, 0x10000);
    Write(0x11c, 0);
    for (unsigned i = 0; i < 8; ++i)
      Write(0x120 + 4 * i, 0);
  }
  void Seal() {
    Configure();
    Write(0x100, 1);
    Write(0x100, 2);
    for (unsigned i = 0; i < 8; ++i)
      Write(0x140 + 4 * i, 0);
    Write(0x100, 3);
    Require(dut.ready_o && !dut.loader_enable_o, "seal did not enable read-only access");
  }
  void Offer(unsigned tag, uint64_t offset, std::optional<uint32_t> epoch = std::nullopt) {
    dut.req_valid_i  = 1;
    dut.req_tag_i    = tag;
    dut.req_offset_i = offset;
    dut.req_epoch_i  = epoch.value_or(dut.epoch_o);
  }
  void Request(unsigned tag, uint64_t offset, std::optional<uint32_t> epoch = std::nullopt) {
    Offer(tag, offset, epoch);
    Tick();
    Require(accepted_last, "expected request was not accepted");
    dut.req_valid_i = 0;
  }
  template <typename Predicate>
  void Until(Predicate predicate, unsigned limit, const std::string &message) {
    for (unsigned i = 0; i < limit; ++i) {
      dut.eval();
      if (predicate())
        return;
      Tick();
    }
    Require(predicate(), message);
  }
  void Drain() {
    dut.req_valid_i = 0;
    dut.rsp_ready_i = 1;
    Until([&] { return outstanding.empty() && !physical && !dut.mem_req_valid_o; }, 1000,
          "drain timeout");
    for (unsigned i = 0; i < 4; ++i)
      Tick();
    Require(accepted == retired, "lost logical response");
  }

 private:
  std::optional<Response> held_response;
  std::optional<uint64_t> held_request;
  unsigned late_tag = 0;
  void RawClock() {
    dut.clk_i = 0;
    dut.eval();
    context.timeInc(1);
    dut.clk_i = 1;
    dut.eval();
    context.timeInc(1);
    dut.clk_i = 0;
    dut.eval();
  }
};

void SealControls() {
  Bench b;
  Require(!b.dut.ready_o && !b.dut.loader_enable_o, "empty store is accessible");
  b.Offer(1, 0);
  b.Tick();
  Require(!b.accepted_last, "unsealed request accepted");
  b.dut.req_valid_i = 0;
  b.Write(0x100, 1, true);
  b.Write(0x110, 0x10000);
  b.Write(0x118, 0x10000);
  for (unsigned i = 0; i < 7; ++i)
    b.Write(0x120 + 4 * i, 0);
  b.Write(0x100, 1, true);  // All-zero defaults are not valid hash writes.
  b.Write(0x13c, 0);
  b.Write(0x100, 1);
  Require(b.dut.loader_enable_o, "loader did not open");
  b.Write(0x110, 0x20000, true);
  b.dut.loader_idle_i = 0;
  b.Write(0x100, 2);
  Require(!b.dut.loader_enable_o && !b.Read(0x104), "verification did not close/drain loader");
  b.Write(0x140, 0, true);
  b.Write(0x100, 3, true);
  b.dut.loader_idle_i = 1;
  Require(b.Read(0x104) == 1, "drain status missing");
  for (unsigned i = 0; i < 7; ++i)
    b.Write(0x140 + 4 * i, 0);
  b.Write(0x100, 3, true);
  b.Write(0x15c, 1);
  b.Write(0x100, 3, true);
  b.Write(0x15c, 0);
  b.Write(0x100, 3);
  Require(b.dut.ready_o && b.dut.image_base_o == 0x10000 && b.dut.epoch_o == 1,
          "wrong sealed metadata");
  b.Write(0x110, 0x20000, true);
  b.Write(0x120, 1, true);
  b.Write(0x140, 1, true);
  const auto rejected           = b.Read(0x060);
  b.dut.loader_write_rejected_i = 1;
  b.Write(0x110, 0x20000, true);
  b.dut.loader_write_rejected_i = 0;
  Require(b.Read(0x060) == rejected + 2, "simultaneous rejections were not counted");
  b.Write(0x100, 4);
  b.accept_memory = false;
  b.Request(4, 256);
  b.Write(0x100, 5);
  for (unsigned i = 0; i < 5; ++i)
    b.Tick();
  Require((b.Read(4) & 7) == 4 && !b.dut.ready_o,
          "STOP completed before outstanding reads drained");
  b.accept_memory = true;
  b.Drain();
  b.Until([&] { return (b.Read(4) & 7) == 3; }, 10, "STOP failed to return sealed");
  Require(b.Read(0x050) == 1 && b.dut.ready_o, "drained completion snapshot wrong");
}

void VolatileImageLoss(unsigned loss_state) {
  Bench b;
  b.Configure();
  b.dut.backend_ready_i = 0;
  b.Write(0x100, 1, true);
  Require((b.Read(4) & 7) == 0, "not-ready empty backend unexpectedly loaded");
  b.dut.backend_ready_i = 1;
  b.Write(0x100, 1);
  if (loss_state >= 2) {
    b.Write(0x100, 2);
    for (unsigned i = 0; i < 8; ++i)
      b.Write(0x140 + 4 * i, 0);
  }
  if (loss_state == 3)
    b.Write(0x100, 3);
  b.dut.backend_ready_i = 0;
  b.Tick();
  Require(b.dut.fault_o && !b.dut.ready_o && !b.dut.loader_enable_o,
          "volatile backend loss did not invalidate image");
  b.dut.backend_ready_i = 1;
  for (unsigned i = 0; i < 8; ++i)
    b.Tick();
  b.Write(0x100, 1, true);
  b.Write(0x100, 3, true);
  b.Write(0x110, 0, true);
  Require(b.dut.fault_o && !b.dut.ready_o && !b.dut.loader_enable_o,
          "backend recovery revived old image without reload");
  b.dut.rst_ni = 0;
  b.Tick();
  b.dut.rst_ni = 1;
  b.Tick();
  b.Write(0x100, 1, true);  // Reset invalidates descriptor/hash-valid masks.
  b.Seal();
  Require(b.dut.ready_o, "fresh configure/verify/seal after coordinated reset failed");
}

void ImageBounds() {
  Bench b;
  b.Configure();
  const uint64_t capacity = b.Read(8) == 3 ? 0x20000000ULL : 0x400000000ULL;
  b.Write(0x118, 0x10000040);
  b.Write(0x100, 1, true);  // A physical allocation never enlarges v0 aperture.
  b.Write(0x118, 64);
  b.Write(0x110, uint32_t(capacity - 32));
  b.Write(0x114, uint32_t((capacity - 32) >> 32));
  b.Write(0x100, 1, true);  // Unaligned and overflowing end.
  b.Write(0x110, uint32_t(capacity));
  b.Write(0x114, uint32_t(capacity >> 32));
  b.Write(0x100, 1, true);  // Upper-address aliases are rejected before narrowing.
  b.Write(0x110, uint32_t(capacity - 64));
  b.Write(0x114, uint32_t((capacity - 64) >> 32));
  b.Write(0x100, 1);
  b.Write(0x100, 2);
  for (unsigned i = 0; i < 8; ++i)
    b.Write(0x140 + 4 * i, 0);
  b.Write(0x100, 3);
  b.Request(1, 0);
  b.Request(2, 64);
  b.Drain();
  Require(b.physical_reads == 1 && b.dut.ready_o,
          "last legal line or first out-of-range line behavior changed");
}

void CreditsAndBackpressure() {
  Bench b;
  b.Seal();
  b.accept_memory = false;
  for (unsigned tag = 0; tag < 16; ++tag)
    b.Request(tag, tag * 64);
  Require(b.outstanding.size() == 16, "sixteen credits not available");
  b.Offer(16, 16 * 64);
  for (unsigned i = 0; i < 8; ++i) {
    b.Tick();
    Require(!b.accepted_last, "seventeenth request exceeded credit capacity");
  }
  b.accept_memory = true;
  b.Until([&] { return b.dut.rsp_valid_o; }, 20, "no response after backend resumed");
  for (unsigned i = 0; i < 8; ++i) {
    b.Tick();
    Require(!b.accepted_last && b.outstanding.size() == 16, "held response released a credit");
  }
  b.dut.rsp_ready_i = 1;
  b.Until([&] { return b.accepted == 17; }, 20, "consumed response failed to release credit");
  b.Drain();
  Require(b.retired == 17 && b.physical_reads == 17, "credit test lost or duplicated a read");
}

void InvalidRequests() {
  Bench b;
  b.Seal();
  b.Request(1, 1);
  b.Request(2, 0x10000);
  b.Request(3, 0, b.dut.epoch_o + 1);
  b.Drain();
  Require(b.physical_reads == 0 && !b.dut.fault_o && b.dut.ready_o,
          "invalid request reached memory or broke seal");
}

void HeldResponseFatalAndLateDrain() {
  Bench b;
  b.Seal();
  b.Request(1, 64);
  b.Until([&] { return b.dut.rsp_valid_o; }, 20, "first response did not arrive");
  const auto held = b.CurrentResponse();
  b.return_memory = false;
  b.Request(2, 128);
  b.Request(3, 192);
  b.Until([&] { return b.physical.has_value(); }, 20, "second physical read did not start");
  // Deliberately malformed client traffic: duplicate outstanding tag must not transfer.
  b.Offer(3, 704);
  b.Tick();
  Require(!b.accepted_last && b.Read(0x040) == 7 && b.Read(0x044) == 704,
          "duplicate tag was accepted or sticky protocol evidence missing");
  b.dut.req_valid_i    = 0;
  b.dut.client_fault_i = 1;
  b.Tick();
  b.dut.client_fault_i = 0;
  for (unsigned i = 0; i < 6; ++i)
    b.Tick();
  Require(b.dut.fault_o && b.CurrentResponse() == held, "fatal fault changed held response");
  Require(b.Read(0x040) == 7 && b.Read(0x044) == 704,
          "later fatal event overwrote first sticky fault");
  b.dut.rsp_ready_i = 1;
  b.Until([&] { return b.outstanding.empty(); }, 30, "fatal did not retire every accepted request");
  Require(b.physical.has_value() && b.dut.mem_rsp_ready_o, "late physical response cannot drain");
  b.Offer(4, 256);
  for (unsigned i = 0; i < 5; ++i) {
    b.Tick();
    Require(!b.accepted_last, "faulted store reused credit before backend drain");
  }
  b.dut.req_valid_i = 0;
  b.Write(0x100, 1, true);
  b.return_memory = true;
  b.Drain();
  Require(b.retired == 3 && b.physical_reads == 2 && b.physical_returns == 2,
          "late backend drain produced extra reads/responses");
  b.Offer(4, 256);
  b.Tick();
  Require(!b.accepted_last, "fatal store reopened without coordinated reset");
}

void FaultWithHeldPhysicalRequest() {
  Bench b;
  b.Seal();
  b.accept_memory = false;
  b.Request(8, 512);
  b.Until([&] { return b.dut.mem_req_valid_o; }, 10, "no held physical request");
  b.dut.client_fault_i = 1;
  b.Tick();
  b.dut.client_fault_i = 0;
  b.dut.rsp_ready_i    = 1;
  b.Until([&] { return b.outstanding.empty(); }, 20,
          "held request did not get logical fatal response");
  for (unsigned i = 0; i < 5; ++i)
    b.Tick();
  Require(b.dut.mem_req_valid_o && b.dut.mem_req_addr_o == 0x10200,
          "fault canceled a stalled physical request");
  b.accept_memory = true;
  b.Drain();
  Require(b.physical_reads == 1 && b.physical_returns == 1 && b.retired == 1,
          "stalled physical request was not drained exactly once");
}

void SustainedRefillFairness(bool valid_reads) {
  Bench b;
  b.Seal();
  b.accept_memory = false;
  for (unsigned tag = 0; tag < 16; ++tag)
    b.Request(tag, tag * 64 + (valid_reads ? 0 : 1));
  b.accept_memory   = true;
  b.dut.rsp_ready_i = 1;
  unsigned next_tag = 16;
  for (unsigned cycle = 0; cycle < 1200 && next_tag < 96; ++cycle) {
    b.Offer(next_tag, next_tag * 64 + (valid_reads ? 0 : 1));
    b.Tick();
    if (b.accepted_last)
      ++next_tag;
    if (b.retired >= 32)
      Require(b.retire_count[15] == 1, "high slot starved under continuous low-slot refill");
  }
  Require(next_tag == 96, "sustained refill stopped making progress");
  b.Drain();
  Require(b.retired == 96 && b.retire_count.size() == 96, "refill lost/duplicated tags");
  Require(b.physical_reads == (valid_reads ? 96 : 0), "refill physical-read count mismatch");
}
}  // namespace

int main(int argc, char **argv) {
  Verilated::commandArgs(argc, argv);
  unsigned passed = 0;
  try {
    const auto run = [&](const char *name, auto function) {
      function();
      ++passed;
      std::cout << "PASS " << name << '\n';
    };
    run("seal_hash_masks_write_drain_and_stop", SealControls);
    run("backend_loss_loading_requires_reload", [] { VolatileImageLoss(1); });
    run("backend_loss_verifying_requires_reload", [] { VolatileImageLoss(2); });
    run("backend_loss_sealed_requires_reload", [] { VolatileImageLoss(3); });
    run("physical_capacity_and_logical_aperture", ImageBounds);
    run("sixteen_credits_and_backpressure", CreditsAndBackpressure);
    run("invalid_request_errors", InvalidRequests);
    run("held_response_fatal_first_fault_and_late_drain", HeldResponseFatalAndLateDrain);
    run("fault_with_held_physical_request", FaultWithHeldPhysicalRequest);
    run("read_issue_fairness", [] { SustainedRefillFairness(true); });
    run("response_fairness", [] { SustainedRefillFairness(false); });
    std::cout << "{\"status\":\"PASS\",\"scenarios\":" << passed
              << ",\"scoreboard\":\"public_ports_exact_tag_epoch_status_data\"}\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "FAIL after " << passed << " scenarios: " << error.what() << '\n';
    return 1;
  }
}
