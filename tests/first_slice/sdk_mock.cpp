// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// Test-only SDK implementation: synthetic host memory, never physical FPGA I/O.
#include <array>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <string>

#include "fpga_mgmt.h"
static std::map<uint64_t, uint32_t> regs;
alignas(64) static std::array<unsigned char, 131072> ddr{};
static int describes = 0, pokes = 0;
static bool wc_pending     = false;
static uint64_t wc_address = 0;
static bool fail(const char *name) {
  const char *p = std::getenv("MOCK_FAILURE");
  return p && std::string(p) == name;
}
extern "C" {
int fpga_mgmt_init() {
  regs[0]                    = 0x10000;
  const char *memory_backend = std::getenv("MOCK_MEMORY_BACKEND");
  regs[8]                    = memory_backend && std::string(memory_backend) == "hbm" ? 3 : 2;
  if (fail("memory_backend"))
    regs[8] ^= 1;
  regs[0x1000] = 0x444f5431;
  regs[0x1004] = 0x10000;
  regs[0x1008] = 7;
  if (fail("abi"))
    regs[0] = 0;
  if (fail("not_cold"))
    regs[4] = 3;
  return fail("init") ? -1 : 0;
}
int fpga_mgmt_close() { return (fail("close") || wc_pending) ? -1 : 0; }
void fpga_mgmt_set_cmd_timeout(uint32_t) {}
void fpga_mgmt_set_cmd_delay_msec(uint32_t) {}
int fpga_pci_get_slot_spec(int, fpga_slot_spec *spec) {
  *spec                   = {};
  auto &m                 = spec->map[0];
  m.vendor_id             = 0x1d0f;
  m.device_id             = 0xf010;
  m.subsystem_vendor_id   = 0x1d0f;
  m.subsystem_device_id   = 0x103;
  m.resource_size[0]      = 0x10000;
  m.resource_size[4]      = 0x10000000;
  m.resource_burstable[4] = true;
  spec->map[1].device_id  = F2_MBOX_DEVICE_ID;
  if (fail("pci"))
    m.subsystem_device_id = 0xffff;
  return 0;
}
int fpga_mgmt_describe_local_image(int slot, fpga_mgmt_image_info *info, uint32_t flags) {
  ++describes;
  // GET_HW_METRICS is read-only; silently returning zero metrics is forbidden.
  if (flags != FPGA_CMD_GET_HW_METRICS)
    return -1;
  *info            = {};
  info->status     = FPGA_STATUS_LOADED;
  info->slot_id    = slot;
  info->sh_version = 0x10212415;
  std::strcpy(info->ids.afi_id, "agfi-00000000000000103");
  info->ids.afi_device_ids = {0x1d0f, 0xf010, 0x1d0f, 0x103};
  if (fail("agfi") || (fail("final_identity") && describes >= 3))
    std::strcpy(info->ids.afi_id, "agfi-00000000000000104");
  if (fail("final_memory_backend") && describes >= 3)
    regs[8] ^= 1;
  auto &metrics = info->metrics.f2_metrics;
  if (fail("metrics-int-status"))
    metrics.int_status = 1;
  if (fail("metrics-pcis-timeout") || (fail("metrics-late-pcis-timeout") && describes >= 3))
    metrics.dma_pcis_timeout_count = 1;
  if (fail("metrics-ocl-timeout") || (fail("metrics-late-ocl-timeout") && describes >= 3))
    metrics.ocl_slave_timeout_count = 1;
  if (fail("metrics-pcim-status"))
    metrics.pcim_axi_protocol_error_status = 1;
  if (fail("metrics-pcim-count"))
    metrics.pcim_axi_protocol_error_count = 1;
  if (fail("metrics-range-count"))
    metrics.pcim_range_error_count = 1;
  return fail("describe") ? -1 : 0;
}
int fpga_pci_attach(int, int, int bar, uint32_t flags, int *handle) {
  if (fail("attach"))
    return -1;
  *handle = bar == 0 ? 1 : (flags ? 2 : 3);
  return 0;
}
int fpga_pci_detach(int) { return fail("detach") ? -1 : 0; }
int fpga_pci_peek(int handle, uint64_t addr, uint32_t *value) {
  if (fail("peek"))
    return -1;
  if (handle == 1)
    *value = regs[addr];
  else {
    if (addr + 4 > ddr.size())
      return -1;
    std::memcpy(value, ddr.data() + addr, 4);
  }
  return 0;
}
int fpga_pci_peek64(int handle, uint64_t addr, uint64_t *value) {
  if (handle != 3 || addr + 8 > ddr.size() || fail("drain") || !wc_pending ||
      (addr & ~uint64_t(63)) != wc_address)
    return -1;
  std::memcpy(value, ddr.data() + addr, 8);
  wc_pending = false;
  return 0;
}
int fpga_pci_poke(int handle, uint64_t addr, uint32_t value) {
  ++pokes;
  if (fail("poke"))
    return -1;
  if (handle == 1) {
    regs[addr] = value;
    if (addr == 0x100) {
      if (value == 1) {
        regs[4] = 1;
        ++regs[0xc];
      } else if (value == 2) {
        if (fail("epoch-change"))
          ++regs[0xc];
        regs[4]     = 2;
        regs[0x104] = 1;
      } else if (value == 3)
        regs[4] = 0x303;
    }
  } else {
    if ((regs[4] & 7) == 3)
      ++regs[0x60];
    else if (addr + 4 <= ddr.size())
      std::memcpy(ddr.data() + addr, &value, 4);
    else
      return -1;
  }
  return 0;
}
int fpga_pci_write_burst(int handle, uint64_t addr, uint32_t *data, uint64_t words) {
  if (fail("burst") || (fail("burst_partial") && addr >= 4096) || words != 16 || addr % 64 != 0 ||
      wc_pending || handle != 2 || addr + 4 * words > ddr.size() || (regs[4] & 7) != 1)
    return -1;
  std::memcpy(ddr.data() + addr, data, 4 * words);
  wc_pending = true;
  wc_address = addr;
  return 0;
}
int fpga_pci_get_address(int handle, uint64_t addr, uint64_t bytes, void **pointer) {
  if (fail("map") || (fail("map_partial") && addr >= 4096) || handle != 3 ||
      addr + bytes > ddr.size())
    return -1;
  *pointer = ddr.data() + addr;
  return 0;
}
}
