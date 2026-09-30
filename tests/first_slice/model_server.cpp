// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// Bus transport for the same custom logic used by the physical FPGA build.
#include <fcntl.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#ifdef USE_ARC
#include "arcilator_adapter.h"
using FirstSliceModel                 = ArcFirstSlice;
static constexpr const char *kBackend = "arcilator";
#else
#include "Vfirst_slice_sim_top.h"
#include "verilated.h"
using FirstSliceModel                 = Vfirst_slice_sim_top;
static constexpr const char *kBackend = "verilator";
#endif

struct Sample {
  bool oa, ow, ob, oar, orr, pa, pw, pb, par, pr;
  uint32_t ov;
  unsigned obr, orresp, pbr, prresp, pbid, prid;
  bool prlast;
  std::array<uint32_t, 16> pv;
};

class Simulation {
 public:
  FirstSliceModel d;
  uint64_t cycles = 0, reads = 0, writes = 0, requests = 0;
  uint32_t random_state = 103;
  std::vector<uint8_t> memory;
  bool load_started = false;
  bool have_aw = false, have_w = false, b_pending = false, r_pending = false;
  uint64_t aw_addr = 0, wstrb = 0;
  std::array<uint32_t, 16> wdata{}, rdata{};
  unsigned bdelay = 0, rdelay = 0, bresponse = 0, rresponse = 0;
  uint16_t bid = 0, rid = 0;
  bool held_r = false, held_b = false;
  uint32_t held_rdata = 0;
  unsigned held_rresp = 0, held_bresp = 0;

  Simulation() {
    d.rst_ni      = 0;
    d.ddr_ready_i = 1;
    for (unsigned i = 0; i < 5; i++)
      step();
    d.rst_ni = 1;
    for (unsigned i = 0; i < 3; i++)
      step();
  }
  uint32_t random() {
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state;
  }
  static void require(bool condition, const char *message) {
    if (!condition)
      throw std::runtime_error(message);
  }
  Sample step() {
    d.clk_i       = 0;
    d.ddr_awready = !have_aw && !b_pending && (random() % 5 != 0);
    d.ddr_wready  = !have_w && !b_pending && (random() % 7 != 0);
    d.ddr_bvalid  = b_pending && bdelay == 0;
    d.ddr_bresp   = bresponse;
    d.ddr_bid     = bid;
    d.ddr_arready = !r_pending && (random() % 4 != 0);
    d.ddr_rvalid  = r_pending && rdelay == 0;
    d.ddr_rresp   = rresponse;
    d.ddr_rid     = rid;
    d.ddr_rlast   = 1;
    for (unsigned i = 0; i < 16; i++)
      d.ddr_rdata[i] = rdata[i];
    d.eval();
    Sample s{bool(d.ocl_awvalid && d.ocl_awready),
             bool(d.ocl_wvalid && d.ocl_wready),
             bool(d.ocl_bvalid && d.ocl_bready),
             bool(d.ocl_arvalid && d.ocl_arready),
             bool(d.ocl_rvalid && d.ocl_rready),
             bool(d.pcis_awvalid && d.pcis_awready),
             bool(d.pcis_wvalid && d.pcis_wready),
             bool(d.pcis_bvalid && d.pcis_bready),
             bool(d.pcis_arvalid && d.pcis_arready),
             bool(d.pcis_rvalid && d.pcis_rready),
             d.ocl_rdata,
             unsigned(d.ocl_bresp),
             unsigned(d.ocl_rresp),
             unsigned(d.pcis_bresp),
             unsigned(d.pcis_rresp),
             unsigned(d.pcis_bid),
             unsigned(d.pcis_rid),
             bool(d.pcis_rlast),
             {}};
    for (unsigned i = 0; i < 16; i++)
      s.pv[i] = d.pcis_rdata[i];
    if (d.rst_ni) {
      if (held_r)
        require(d.ocl_rvalid && d.ocl_rdata == held_rdata && d.ocl_rresp == held_rresp,
                "OCL read response changed under stall");
      if (held_b)
        require(d.ocl_bvalid && d.ocl_bresp == held_bresp,
                "OCL write response changed under stall");
      held_r     = d.ocl_rvalid && !d.ocl_rready;
      held_rdata = d.ocl_rdata;
      held_rresp = d.ocl_rresp;
      held_b     = d.ocl_bvalid && !d.ocl_bready;
      held_bresp = d.ocl_bresp;
      bool ba = d.ddr_awvalid && d.ddr_awready, bw = d.ddr_wvalid && d.ddr_wready;
      bool bb = d.ddr_bvalid && d.ddr_bready, ra = d.ddr_arvalid && d.ddr_arready,
           rr = d.ddr_rvalid && d.ddr_rready;
      if (ba) {
        require(d.ddr_awlen == 0 && d.ddr_awsize == 6 && d.ddr_awburst == 1,
                "unexpected DDR write burst");
        have_aw = true;
        aw_addr = d.ddr_awaddr;
        bid     = d.ddr_awid;
      }
      if (bw) {
        require(d.ddr_wlast, "missing single-beat WLAST");
        have_w = true;
        wstrb  = d.ddr_wstrb;
        for (unsigned i = 0; i < 16; i++)
          wdata[i] = d.ddr_wdata[i];
      }
      if (bb)
        b_pending = false;
      if (rr)
        r_pending = false;
      if (bdelay)
        bdelay--;
      if (rdelay)
        rdelay--;
      if (have_aw && have_w && !b_pending) {
        bresponse = 0;
        if ((aw_addr & 63) || aw_addr > memory.size() || memory.size() - aw_addr < 64)
          bresponse = 2;
        else
          for (unsigned i = 0; i < 64; i++)
            if (wstrb & (uint64_t(1) << i))
              memory[aw_addr + i] = uint8_t(wdata[i / 4] >> (8 * (i % 4)));
        have_aw   = false;
        have_w    = false;
        b_pending = true;
        bdelay    = 1 + random() % 7;
        writes++;
      }
      if (ra) {
        require(d.ddr_arlen == 0 && d.ddr_arsize == 6 && d.ddr_arburst == 1,
                "unexpected DDR read burst");
        uint64_t addr = d.ddr_araddr;
        rid           = d.ddr_arid;
        rresponse     = 0;
        rdata.fill(0);
        if ((addr & 63) || addr > memory.size() || memory.size() - addr < 64)
          rresponse = 2;
        else
          for (unsigned i = 0; i < 64; i++)
            rdata[i / 4] |= uint32_t(memory[addr + i]) << (8 * (i % 4));
        r_pending = true;
        rdelay    = 1 + random() % 11;
        reads++;
      }
    }
    d.clk_i = 1;
    d.eval();
    cycles++;
    return s;
  }
  void idle(unsigned n) {
    while (n--)
      step();
  }
  std::pair<uint32_t, unsigned> ocl_read(uint32_t addr) {
    requests++;
    d.ocl_araddr  = addr;
    d.ocl_arvalid = 1;
    d.ocl_rready  = 0;
    bool accepted = false;
    for (unsigned timeout = 0; timeout < 1000000; timeout++) {
      auto s = step();
      if (s.oar) {
        accepted      = true;
        d.ocl_arvalid = 0;
        idle(2);
        d.ocl_rready = 1;
      }
      if (s.orr) {
        d.ocl_rready = 0;
        return {s.ov, s.orresp};
      }
    }
    throw std::runtime_error(accepted ? "OCL read response timeout" : "OCL read address timeout");
  }
  unsigned ocl_write(uint32_t addr, uint32_t value) {
    requests++;
    d.ocl_awaddr = addr;
    d.ocl_wdata  = value;
    d.ocl_wstrb  = 15;
    bool aw = false, ww = false;
    // Alternate address-first and data-first to exercise independent channels.
    bool data_first = (requests % 2) == 0;
    d.ocl_awvalid   = !data_first;
    d.ocl_wvalid    = data_first;
    d.ocl_bready    = 0;
    for (unsigned timeout = 0; timeout < 1000000; timeout++) {
      auto s = step();
      if (s.oa) {
        aw            = true;
        d.ocl_awvalid = 0;
      }
      if (s.ow) {
        ww           = true;
        d.ocl_wvalid = 0;
      }
      if (timeout == 2) {
        if (!aw)
          d.ocl_awvalid = 1;
        if (!ww)
          d.ocl_wvalid = 1;
      }
      if (aw && ww && timeout > 5)
        d.ocl_bready = 1;
      if (s.ob) {
        d.ocl_bready = 0;
        return s.obr;
      }
    }
    throw std::runtime_error("OCL write timeout");
  }
  unsigned pcis_write(uint64_t addr, const uint8_t *data, unsigned beats, unsigned size = 6) {
    require(beats > 0 && beats <= 64 && size <= 6 && (addr & ((uint64_t(1) << size) - 1)) == 0,
            "invalid simulator PCIS write dimensions");
    d.pcis_awaddr  = addr;
    d.pcis_awlen   = beats - 1;
    d.pcis_awsize  = size;
    d.pcis_awburst = 1;
    d.pcis_awid    = 0x103;
    d.pcis_awvalid = 1;
    d.pcis_bready  = 0;
    bool accepted  = false;
    for (unsigned timeout = 0; timeout < 1000000; timeout++)
      if (step().pa) {
        accepted       = true;
        d.pcis_awvalid = 0;
        break;
      }
    require(accepted, "PCIS AW timeout");
    for (unsigned beat = 0; beat < beats; beat++) {
      for (unsigned i = 0; i < 16; i++)
        d.pcis_wdata[i] = 0;
      unsigned lane  = unsigned((addr + (uint64_t(beat) << size)) & 63);
      unsigned bytes = 1u << size;
      d.pcis_wstrb   = size == 6 ? ~uint64_t(0) : ((uint64_t(1) << bytes) - 1) << lane;
      for (unsigned i = 0; i < bytes; i++)
        d.pcis_wdata[(lane + i) / 4] |= uint32_t(data[beat * bytes + i]) << (8 * ((lane + i) % 4));
      d.pcis_wlast  = beat + 1 == beats;
      d.pcis_wvalid = 1;
      accepted      = false;
      for (unsigned timeout = 0; timeout < 1000000; timeout++)
        if (step().pw) {
          accepted      = true;
          d.pcis_wvalid = 0;
          break;
        }
      require(accepted, "PCIS W timeout");
    }
    idle(2);
    d.pcis_bready = 1;
    for (unsigned timeout = 0; timeout < 1000000; timeout++) {
      auto s = step();
      if (s.pb) {
        require(s.pbid == 0x103, "PCIS BID mismatch");
        d.pcis_bready = 0;
        return s.pbr;
      }
    }
    throw std::runtime_error("PCIS B timeout");
  }
  std::vector<uint8_t> pcis_read(uint64_t addr, unsigned beats, unsigned size = 6) {
    require(beats > 0 && beats <= 64 && size <= 6 && (addr & ((uint64_t(1) << size) - 1)) == 0,
            "invalid simulator PCIS read dimensions");
    d.pcis_araddr  = addr;
    d.pcis_arlen   = beats - 1;
    d.pcis_arsize  = size;
    d.pcis_arburst = 1;
    d.pcis_arid    = 0x104;
    d.pcis_arvalid = 1;
    d.pcis_rready  = 0;
    bool accepted  = false;
    for (unsigned timeout = 0; timeout < 1000000; timeout++)
      if (step().par) {
        accepted       = true;
        d.pcis_arvalid = 0;
        break;
      }
    require(accepted, "PCIS AR timeout");
    std::vector<uint8_t> result;
    result.reserve(static_cast<std::size_t>(beats) * (std::size_t{1} << size));
    for (unsigned beat = 0; beat < beats; beat++) {
      idle(beat % 3);
      d.pcis_rready = 1;
      accepted      = false;
      for (unsigned timeout = 0; timeout < 1000000; timeout++) {
        auto s = step();
        if (s.pr) {
          require(s.prresp == 0, "PCIS RRESP error");
          require(s.prid == 0x104, "PCIS RID mismatch");
          require(s.prlast == (beat + 1 == beats), "PCIS RLAST mismatch");
          unsigned lane = unsigned((addr + (uint64_t(beat) << size)) & 63);
          for (unsigned i = 0; i < (1u << size); i++)
            result.push_back(uint8_t(s.pv[(lane + i) / 4] >> (8 * ((lane + i) % 4))));
          accepted      = true;
          d.pcis_rready = 0;
          break;
        }
      }
      require(accepted, "PCIS R timeout");
    }
    return result;
  }
  void load(const std::string &path) {
    require(!load_started, "only one LOAD is permitted per cold simulation");
    load_started = true;
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    require(bool(file), "cannot open image");
    auto size = file.tellg();
    require(size > 0 && uint64_t(size) <= 0x10000000 && (uint64_t(size) % 64) == 0,
            "invalid image size");
    memory.resize(size, 0);
    file.seekg(0);
    std::array<uint8_t, 4096> block{};
    for (uint64_t offset = 0; offset < uint64_t(size); offset += block.size()) {
      unsigned count = unsigned(std::min<uint64_t>(block.size(), uint64_t(size) - offset));
      file.read(reinterpret_cast<char *>(block.data()), count);
      require(bool(file), "short image read");
      require(pcis_write(offset, block.data(), count / 64) == 0, "image write rejected");
      if ((offset % (32 * 1024 * 1024)) == 0)
        std::cerr << "LOAD_BYTES " << offset << "\n";
    }
  }
  void dump(const std::string &path, uint64_t size) {
    require(size > 0 && size <= memory.size() && size % 64 == 0, "invalid dump size");
    int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
    require(fd >= 0, "cannot exclusively create readback file");
    try {
      for (uint64_t offset = 0; offset < size; offset += 4096) {
        unsigned count = unsigned(std::min<uint64_t>(4096, size - offset));
        auto bytes     = pcis_read(offset, count / 64);
        size_t written = 0;
        while (written < bytes.size()) {
          auto n = ::write(fd, bytes.data() + written, bytes.size() - written);
          if (n < 0 && errno == EINTR)
            continue;
          require(n > 0, "readback write failed");
          written += size_t(n);
        }
        if ((offset % (32 * 1024 * 1024)) == 0)
          std::cerr << "DUMP_BYTES " << offset << "\n";
      }
      require(::fsync(fd) == 0, "readback fsync failed");
      int status = ::close(fd);
      fd         = -1;
      require(status == 0, "readback close failed");
    } catch (...) {
      if (fd >= 0)
        ::close(fd);
      throw;
    }
  }
};

int main(int argc, char **argv) {
#ifndef USE_ARC
  Verilated::commandArgs(argc, argv);
#else
  (void)argc;
  (void)argv;
#endif
  try {
    Simulation sim;
    std::string line;
    while (std::getline(std::cin, line)) {
      std::istringstream input(line);
      std::string op;
      input >> op;
      if (op == "QUIT")
        break;
      if (op == "INFO")
        std::cout << "{\"ok\":true,\"backend\":\"" << kBackend
                  << "\",\"seed\":103,\"cycles\":" << sim.cycles << ",\"ddr_reads\":" << sim.reads
                  << ",\"ddr_writes\":" << sim.writes
                  << ",\"memory_model\":\"AXI512 variable latency 1-11 cycles with "
                     "independent channel stalls\"}";
      else if (op == "READ") {
        uint64_t addr;
        input >> addr;
        Simulation::require(bool(input) && addr <= 0xffffffff, "invalid READ");
        auto r = sim.ocl_read(uint32_t(addr));
        std::cout << "{\"ok\":true,\"value\":" << r.first << ",\"resp\":" << r.second << "}";
      } else if (op == "WRITE") {
        uint64_t addr, value;
        input >> addr >> value;
        Simulation::require(bool(input) && addr <= 0xffffffff && value <= 0xffffffff,
                            "invalid WRITE");
        auto r = sim.ocl_write(uint32_t(addr), uint32_t(value));
        std::cout << "{\"ok\":true,\"resp\":" << r << "}";
      } else if (op == "LOAD") {
        std::string path;
        input >> std::quoted(path);
        Simulation::require(bool(input), "invalid LOAD");
        sim.load(path);
        std::cout << "{\"ok\":true,\"bytes\":" << sim.memory.size()
                  << ",\"ddr_writes\":" << sim.writes << "}";
      } else if (op == "DUMP") {
        std::string path;
        uint64_t size;
        input >> std::quoted(path) >> size;
        Simulation::require(bool(input), "invalid DUMP");
        sim.dump(path, size);
        std::cout << "{\"ok\":true,\"bytes\":" << size << ",\"ddr_reads\":" << sim.reads << "}";
      } else if (op == "POKE") {
        uint64_t addr, value;
        input >> addr >> value;
        Simulation::require(bool(input) && value <= 0xffffffff && addr % 4 == 0 &&
                                sim.memory.size() >= 4 && addr <= sim.memory.size() - 4,
                            "invalid POKE dimensions");
        std::array<uint8_t, 4> bytes{};
        for (unsigned i = 0; i < 4; i++)
          bytes[i] = uint8_t(value >> (8 * i));
        auto r = sim.pcis_write(addr, bytes.data(), 1, 2);
        std::cout << "{\"ok\":true,\"resp\":" << r << "}";
      } else if (op == "PEEK") {
        uint64_t addr;
        input >> addr;
        Simulation::require(
            bool(input) && addr % 4 == 0 && sim.memory.size() >= 4 && addr <= sim.memory.size() - 4,
            "invalid PEEK dimensions");
        auto bytes     = sim.pcis_read(addr, 1, 2);
        uint32_t value = 0;
        for (unsigned i = 0; i < 4; i++)
          value |= uint32_t(bytes[i]) << (8 * i);
        std::cout << "{\"ok\":true,\"resp\":0,\"value\":" << value << "}";
      } else
        throw std::runtime_error("unknown transport request");
      std::cout << std::endl;
    }
    std::cerr << "TRANSPORT_COMPLETE cycles=" << sim.cycles << " ddr_reads=" << sim.reads
              << " ddr_writes=" << sim.writes << "\n";
  } catch (const std::exception &error) {
    std::cerr << "TRANSPORT_FAIL " << error.what() << "\n";
    std::cout << "{\"ok\":false,\"error\":\"transport failed; inspect stderr log\"}" << std::endl;
    return 1;
  }
  return 0;
}
