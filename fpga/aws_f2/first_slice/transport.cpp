// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// Public AWS SDK bus transport. No AFI lifecycle, reset, or dot arithmetic here.
// Build against aws/aws-fpga b603a81f65666e0cf7a67ee5cf18b148eb6b08c3.
#include <fcntl.h>
#include <fpga_mgmt.h>
#include <fpga_pci.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>

#if !defined(__linux__) || !defined(__x86_64__)
#error "The AWS F2 transport requires Linux x86_64; no simulator fallback is provided."
#endif

#ifndef MEMORY_BACKEND_HBM
#define MEMORY_BACKEND_HBM 0
#endif
#if MEMORY_BACKEND_HBM != 0 && MEMORY_BACKEND_HBM != 1
#error "MEMORY_BACKEND_HBM must be 0 (DDR) or 1 (HBM)."
#endif

namespace {
constexpr uint64_t kMaxImageBytes    = 0x10000000;
constexpr uint64_t kOclBytes         = 0x2000;
constexpr auto kBulkTimeout          = std::chrono::seconds(1800);
constexpr const char *kSdkRevision   = "b603a81f65666e0cf7a67ee5cf18b148eb6b08c3";
constexpr uint32_t kMemoryBackendId  = MEMORY_BACKEND_HBM ? 3 : 2;
constexpr const char *kMemoryBackend = MEMORY_BACKEND_HBM ? "hbm" : "ddr";

void require(bool condition, const std::string &message) {
  if (!condition)
    throw std::runtime_error(message);
}

void sdk_check(int status, const char *operation) {
  if (status != 0)
    throw std::runtime_error(std::string(operation) + " SDK status=" + std::to_string(status));
}

void system_check(bool condition, const char *operation) {
  if (!condition)
    throw std::runtime_error(std::string(operation) + ": " + std::strerror(errno));
}

uint64_t number(const std::string &token) {
  require(!token.empty() && token[0] >= '0' && token[0] <= '9', "invalid unsigned integer");
  size_t consumed  = 0;
  const int base   = token.size() > 2 && token[0] == '0' && token[1] == 'x' ? 16 : 10;
  const auto value = std::stoull(token, &consumed, base);
  require(consumed == token.size(), "trailing characters in integer");
  return value;
}

uint64_t argument(std::istringstream &input) {
  std::string token;
  require(bool(input >> token), "missing numeric argument");
  return number(token);
}

void end_of_request(std::istringstream &input) {
  input >> std::ws;
  require(input.eof(), "extra transport arguments");
}

std::string quoted_path(std::istringstream &input) {
  input >> std::ws;
  require(input.peek() == '"', "path must be quoted");
  std::string path;
  require(bool(input >> std::quoted(path)), "invalid quoted path");
  require(!path.empty() && path.size() < 4096, "invalid path length");
  for (unsigned char c : path)
    require(c >= 32 && c != 127, "control character in path");
  return path;
}

std::string hex32(uint32_t value) {
  std::ostringstream out;
  out << "0x" << std::hex << std::setfill('0') << std::setw(8) << value;
  return out.str();
}

class File {
 public:
  explicit File(int descriptor) : fd(descriptor) { system_check(fd >= 0, "open"); }
  ~File() {
    if (fd >= 0 && ::close(fd) != 0)
      std::cerr << "CLEANUP_FAIL file close errno=" << errno << '\n';
  }
  File(const File &)            = delete;
  File &operator=(const File &) = delete;
  int get() const { return fd; }
  void close() {
    const int descriptor = fd;
    fd                   = -1;
    system_check(::close(descriptor) == 0, "close file");
  }

 private:
  int fd;
};

void read_exact(int fd, void *destination, size_t count) {
  auto *bytes = static_cast<uint8_t *>(destination);
  while (count) {
    const ssize_t got = ::read(fd, bytes, count);
    if (got < 0 && errno == EINTR)
      continue;
    system_check(got >= 0, "read image");
    require(got > 0, "short image read");
    bytes += got;
    count -= size_t(got);
  }
}

void write_exact(int fd, const void *source, size_t count) {
  const auto *bytes = static_cast<const uint8_t *>(source);
  while (count) {
    const ssize_t sent = ::write(fd, bytes, count);
    if (sent < 0 && errno == EINTR)
      continue;
    system_check(sent >= 0, "write readback");
    require(sent > 0, "zero-length readback write");
    bytes += sent;
    count -= size_t(sent);
  }
}

struct Options {
  std::string agfi;
  int slot = 0;
};

Options options(int argc, char **argv) {
  Options result;
  if (const char *value = std::getenv("FIRST_SLICE_EXPECTED_AGFI"))
    result.agfi = value;
  auto slot = [](const std::string &value) {
    const auto parsed = number(value);
    require(parsed < FPGA_SLOT_MAX, "slot outside SDK range");
    return int(parsed);
  };
  if (const char *value = std::getenv("FIRST_SLICE_SLOT"))
    result.slot = slot(value);
  for (int i = 1; i < argc; ++i) {
    const std::string option = argv[i];
    require(i + 1 < argc, "usage: transport [--agfi agfi-...] [--slot N]");
    if (option == "--agfi")
      result.agfi = argv[++i];
    else if (option == "--slot")
      result.slot = slot(argv[++i]);
    else
      throw std::runtime_error("unknown command-line argument");
  }
  require(result.agfi.size() == 22 && result.agfi.compare(0, 5, "agfi-") == 0,
          "FIRST_SLICE_EXPECTED_AGFI or --agfi must specify the expected AGFI");
  for (size_t i = 5; i < result.agfi.size(); ++i)
    require((result.agfi[i] >= '0' && result.agfi[i] <= '9') ||
                (result.agfi[i] >= 'a' && result.agfi[i] <= 'f'),
            "invalid AGFI identifier");
  return result;
}

class Transport {
 public:
  explicit Transport(Options settings) : settings(std::move(settings)) {}
  ~Transport() { cleanup(false); }

  void start() {
    // Advisory reservation protects cooperating transport processes. It cannot
    // stop an unrelated privileged AFI loader; startup/bulk/final identity
    // checks detect changes, and the caller must reserve this slot exclusively.
    const auto lock_path =
        "/tmp/coralnpu-first-slice-slot-" + std::to_string(settings.slot) + ".lock";
    lock_fd = ::open(lock_path.c_str(), O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
    system_check(lock_fd >= 0, "open slot reservation");
    struct stat lock_stat{};
    system_check(::fstat(lock_fd, &lock_stat) == 0, "stat slot reservation");
    require(S_ISREG(lock_stat.st_mode) && lock_stat.st_uid == geteuid() && lock_stat.st_nlink == 1,
            "unsafe slot reservation file");
    system_check(::flock(lock_fd, LOCK_EX | LOCK_NB) == 0, "reserve FPGA slot");
    sdk_check(fpga_mgmt_init(), "fpga_mgmt_init");
    management_open = true;
    fpga_mgmt_set_cmd_timeout(100);
    fpga_mgmt_set_cmd_delay_msec(10);
    identity(false);
    sdk_check(fpga_pci_attach(settings.slot, FPGA_APP_PF, APP_PF_BAR0, 0, &ocl), "attach BAR0");
    sdk_check(fpga_pci_attach(settings.slot, FPGA_APP_PF, APP_PF_BAR4, BURST_CAPABLE, &bulk_write),
              "attach write-combining BAR4");
    sdk_check(fpga_pci_attach(settings.slot, FPGA_APP_PF, APP_PF_BAR4, 0, &bulk_read),
              "attach uncached BAR4");
    check_abi();
    require((read32(4) & 7) == 0 && read32(0xc) == 0 && (read32(0x100c) & 3) == 0,
            "cold EMPTY store/mailbox required; transport never resets or loads an AFI");
    identity(true);
  }

  void info() {
    identity(true);
    std::cout << "{\"ok\":true,\"backend\":\"aws_f2\",\"agfi\":\"" << settings.agfi
              << "\",\"memory_backend\":\"" << kMemoryBackend << "\",\"slot\":" << settings.slot
              << ",\"shell_version\":\"" << hex32(shell) << "\",\"sdk_revision\":\"" << kSdkRevision
              << "\",\"pci_vendor\":\"1d0f\",\"pci_device\":\"f010\","
                 "\"pci_subsystem_vendor\":\"1d0f\",\"pci_subsystem_device\":\"0103\","
                 "\"clock_mhz\":250,\"clock_measured\":false,"
                 "\"clock_source\":\"documented F2 shell clk_main_a0; not a timing measurement\","
                 "\"axi_response_observable\":false,\"posted_write_drain\":true,"
                 "\"whole_image_readback_required\":true,\"exclusive_advisory_lock\":true,"
                 "\"load_chunk_bytes\":64,\"drain_each_load_chunk\":true,"
                 "\"host_dot_computation\":false,\"loaded_bytes\":"
              << loaded_bytes << ",\"readback_bytes\":" << readback_bytes
              << ",\"completed_dumps\":" << completed_dumps << ",\"image_epoch\":" << image_epoch
              << ",\"shell_health_checked\":true,\"metrics_cleared\":false,\"shell_health\":{"
                 "\"int_status\":"
              << health[0] << ",\"dma_pcis_timeout_count\":" << health[1]
              << ",\"ocl_slave_timeout_count\":" << health[2]
              << ",\"pcim_axi_protocol_error_status\":" << health[3]
              << ",\"pcim_axi_protocol_error_count\":" << health[4]
              << ",\"pcim_range_error_count\":" << health[5] << "}}";
  }

  uint32_t read32(uint64_t address) {
    require(address % 4 == 0 && address <= kOclBytes - 4 && address <= ocl_bytes - 4,
            "OCL read outside aligned register pages");
    uint32_t value = 0;
    sdk_check(fpga_pci_peek(ocl, address, &value), "OCL peek");
    return value;
  }

  void write32(uint64_t address, uint64_t value) {
    require(address % 4 == 0 && address <= kOclBytes - 4 && address <= ocl_bytes - 4 &&
                value <= std::numeric_limits<uint32_t>::max(),
            "invalid OCL write");
    check_abi();
    sdk_check(fpga_pci_poke(ocl, address, uint32_t(value)), "OCL poke");
    fence();
    // Read a defined read-only register, never a write-only command register.
    require(read32(address < 0x1000 ? 0 : 0x1004) == 0x10000, "OCL write drain ABI changed");
  }

  void load(const std::string &path) {
    require(!load_started, "only one image LOAD is allowed per cold reset/process");
    identity(true);
    require((read32(4) & 7) == 1, "LOAD requires the LOADING store state");
    File input(::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
    struct stat before{};
    system_check(::fstat(input.get(), &before) == 0, "stat image");
    require(S_ISREG(before.st_mode) && before.st_size > 0, "image must be a nonempty regular file");
    const auto bytes = uint64_t(before.st_size);
    image_range(bytes);
    require(configured_base() == 0 && configured_size() == bytes,
            "image differs from configured range");
    const auto rejected = read32(0x60);
    require(read32(0x40) == 0, "store fault before LOAD");
    load_started = true;
    image_bytes  = bytes;
    image_epoch  = read32(0xc);
    require(image_epoch != 0, "LOAD epoch was not advanced from cold reset");
    // The shell's host-PCIS deadline includes time queued under backpressure.
    // Bound this serial correctness bridge to one cache line before each
    // same-device read drain; a 4KiB WC batch can queue many PCIe requests.
    alignas(64) std::array<uint32_t, 16> block{};
    const auto deadline = std::chrono::steady_clock::now() + kBulkTimeout;
    for (uint64_t offset = 0; offset < bytes; offset += sizeof(block)) {
      require(std::chrono::steady_clock::now() < deadline, "LOAD time budget exceeded");
      const auto count = size_t(std::min<uint64_t>(sizeof(block), bytes - offset));
      read_exact(input.get(), block.data(), count);
      // Each line is page-bounded. AWS PCIS can present partial WSTRB with
      // AxSIZE=6, so the bridge must implement full-width unaligned semantics.
      sdk_check(fpga_pci_write_burst(bulk_write, offset, block.data(), count / 4),
                "BAR4 burst write");
      fence();
      uint64_t drain = 0;
      sdk_check(fpga_pci_peek64(bulk_read, offset + count - 8, &drain), "BAR4 posted-write drain");
      // This read drains traffic; it is not the whole-image verification gate.
      loaded_bytes += count;
      if ((offset % (32 * 1024 * 1024)) == 0)
        std::cerr << "LOAD_BYTES " << loaded_bytes << '\n';
    }
    struct stat after{};
    system_check(::fstat(input.get(), &after) == 0, "restat image");
    require(before.st_size == after.st_size && before.st_mtim.tv_sec == after.st_mtim.tv_sec &&
                before.st_mtim.tv_nsec == after.st_mtim.tv_nsec &&
                before.st_ctim.tv_sec == after.st_ctim.tv_sec &&
                before.st_ctim.tv_nsec == after.st_ctim.tv_nsec,
            "image changed while loading");
    input.close();
    identity(true);
    require((read32(4) & 7) == 1 && configured_base() == 0 && configured_size() == bytes &&
                read32(0xc) == image_epoch && read32(0x60) == rejected && read32(0x40) == 0,
            "LOAD state changed or write was rejected");
    std::cout << "{\"ok\":true,\"bytes\":" << loaded_bytes
              << ",\"axi_response_observable\":false,\"whole_image_readback_required\":true}";
  }

  void dump(const std::string &path, uint64_t bytes) {
    identity(true);
    require(
        load_started && loaded_bytes == image_bytes && bytes == image_bytes && completed_dumps < 2,
        "DUMP requires one complete LOAD, whole-image byte count, and at most two readbacks");
    image_range(bytes);
    const bool initial = completed_dumps == 0;
    auto check_phase   = [&]() {
      const auto status = read32(4);
      require(configured_base() == 0 && configured_size() == bytes && read32(0xc) == image_epoch &&
                    read32(0x40) == 0,
                "DUMP image identity changed or store faulted");
      if (initial)
        require((status & 7) == 2 && (read32(0x104) & 1),
                  "initial DUMP requires drained VERIFYING state");
      else
        require((status & 0x707) == 0x303,
                  "final DUMP requires SEALED, READY, LOCKED, and drained state");
    };
    check_phase();
    File output(::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600));
    uint64_t dump_bytes = 0;
    alignas(64) std::array<uint64_t, 512> block{};
    const auto deadline = std::chrono::steady_clock::now() + kBulkTimeout;
    for (uint64_t offset = 0; offset < bytes; offset += sizeof(block)) {
      require(std::chrono::steady_clock::now() < deadline, "DUMP time budget exceeded");
      const auto count = size_t(std::min<uint64_t>(sizeof(block), bytes - offset));
      void *mapped     = nullptr;
      // At the pinned SDK revision fpga_pci.c passes this length to the bounds
      // checker as BYTES, despite fpga_pci.h describing it as dwords.
      sdk_check(fpga_pci_get_address(bulk_read, offset, count, &mapped), "BAR4 read mapping");
      require(mapped != nullptr && reinterpret_cast<uintptr_t>(mapped) % 8 == 0,
              "invalid BAR4 mapping");
      const auto *words = static_cast<volatile const uint64_t *>(mapped);
      for (size_t i = 0; i < count / 8; ++i)
        block[i] = words[i];
      write_exact(output.get(), block.data(), count);
      readback_bytes += count;
      dump_bytes += count;
      if ((offset % (32 * 1024 * 1024)) == 0)
        std::cerr << "DUMP_BYTES " << readback_bytes << '\n';
    }
    system_check(::fsync(output.get()) == 0, "fsync readback");
    output.close();
    identity(true);
    check_phase();
    ++completed_dumps;
    std::cout << "{\"ok\":true,\"bytes\":" << dump_bytes << ",\"readback_bytes\":" << readback_bytes
              << ",\"completed_dumps\":" << completed_dumps
              << ",\"axi_response_observable\":false,\"source\":\"uncached volatile BAR4 reads\"}";
  }

  void poke(uint64_t address, uint64_t value) {
    image_word(address);
    require(value <= std::numeric_limits<uint32_t>::max(), "POKE value exceeds 32 bits");
    identity(true);
    const auto state = read32(4);
    require(completed_dumps == 1 && readback_bytes == image_bytes && (state & 7) == 3 &&
                (state & (1u << 9)),
            "POKE is only a sealed-image write-rejection probe");
    sdk_check(fpga_pci_poke(bulk_write, address, uint32_t(value)), "BAR4 rejected-write probe");
    fence();
    uint32_t drain = 0;
    sdk_check(fpga_pci_peek(bulk_read, address, &drain), "BAR4 probe write drain");
    std::cout << "{\"ok\":true,\"posted\":true,\"axi_response_observable\":false,"
                 "\"rejection_counter_and_readback_required\":true}";
  }

  uint32_t peek(uint64_t address) {
    image_word(address);
    uint32_t value = 0;
    sdk_check(fpga_pci_peek(bulk_read, address, &value), "BAR4 peek");
    return value;
  }

  void finish() {
    identity(true);
    cleanup(true);
    std::cerr << "TRANSPORT_COMPLETE loaded_bytes=" << loaded_bytes
              << " readback_bytes=" << readback_bytes << '\n';
  }

 private:
  Options settings;
  pci_bar_handle_t ocl        = PCI_BAR_HANDLE_INIT;
  pci_bar_handle_t bulk_write = PCI_BAR_HANDLE_INIT;
  pci_bar_handle_t bulk_read  = PCI_BAR_HANDLE_INIT;
  int lock_fd                 = -1;
  bool management_open = false, have_identity = false, load_started = false;
  uint32_t shell = 0, image_epoch = 0;
  unsigned completed_dumps = 0;
  fpga_pci_resource_map initial_map{};
  uint64_t ocl_bytes = 0, bar4_bytes = 0, image_bytes = 0, loaded_bytes = 0, readback_bytes = 0;
  std::array<uint32_t, 6> health{};

  static void fence() { asm volatile("sfence" ::: "memory"); }

  void check_abi() {
    require(read32(0) == 0x10000 && read32(0x1000) == 0x444f5431 && read32(0x1004) == 0x10000 &&
                (read32(0x1008) & 7) == 7,
            "weight-store/DOT128 ABI mismatch");
    require(read32(8) == kMemoryBackendId, "hardware memory backend differs from transport build");
  }

  uint64_t configured_base() { return read32(0x110) | (uint64_t(read32(0x114)) << 32); }
  uint64_t configured_size() { return read32(0x118) | (uint64_t(read32(0x11c)) << 32); }

  void image_range(uint64_t bytes) {
    require(bytes > 0 && bytes <= kMaxImageBytes && bytes <= bar4_bytes && bytes % 64 == 0,
            "image outside complete-line bounded BAR4 allocation");
  }

  void image_word(uint64_t address) {
    require(load_started && loaded_bytes == image_bytes && address % 4 == 0 && image_bytes >= 4 &&
                address <= image_bytes - 4 && address <= bar4_bytes - 4,
            "BAR4 word outside loaded image");
  }

  void identity(bool attached) {
    fpga_mgmt_image_info image{};
    sdk_check(fpga_mgmt_describe_local_image(settings.slot, &image, FPGA_CMD_GET_HW_METRICS),
              "describe loaded image and read-only metrics");
    require(
        image.status == FPGA_STATUS_LOADED && image.status_q == 0 && image.slot_id == settings.slot,
        "FPGA slot is not successfully loaded");
    // This process requires a clean, freshly loaded test image. Do not clear
    // historical counters or baseline away errors: PCIe can return successful
    // completions with placeholder data after an AXI error or shell timeout.
    const auto &metrics                     = image.metrics.f2_metrics;
    health                                  = {metrics.int_status,
                                               metrics.dma_pcis_timeout_count,
                                               metrics.ocl_slave_timeout_count,
                                               metrics.pcim_axi_protocol_error_status,
                                               metrics.pcim_axi_protocol_error_count,
                                               metrics.pcim_range_error_count};
    const std::array<const char *, 6> names = {"int_status",
                                               "dma_pcis_timeout_count",
                                               "ocl_slave_timeout_count",
                                               "pcim_axi_protocol_error_status",
                                               "pcim_axi_protocol_error_count",
                                               "pcim_range_error_count"};
    for (size_t i = 0; i < health.size(); ++i)
      require(health[i] == 0,
              std::string("shell hardware error ") + names[i] + "=" + std::to_string(health[i]));
    require(strnlen(image.ids.afi_id, AFI_ID_STR_MAX) == settings.agfi.size() &&
                settings.agfi == image.ids.afi_id,
            "loaded AGFI differs from required identity");
    const auto ids = image.ids.afi_device_ids;
    require(ids.vendor_id == 0x1d0f && ids.device_id == 0xf010 && ids.svid == 0x1d0f &&
                ids.ssid == 0x0103,
            "AGFI metadata PCI identity mismatch");
    fpga_slot_spec slot{};
    sdk_check(fpga_pci_get_slot_spec(settings.slot, &slot), "read current PCI slot specification");
    const auto map = slot.map[FPGA_APP_PF];
    require(map.vendor_id == 0x1d0f && map.device_id == 0xf010 &&
                map.subsystem_vendor_id == 0x1d0f && map.subsystem_device_id == 0x0103 &&
                slot.map[FPGA_MGMT_PF].device_id == F2_MBOX_DEVICE_ID,
            "physical PCI identity is not the expected F2 first slice");
    require(map.resource_size[APP_PF_BAR0] >= kOclBytes && map.resource_size[APP_PF_BAR4] >= 64 &&
                map.resource_burstable[APP_PF_BAR4],
            "PCI BAR ranges/capabilities unavailable");
    if (have_identity) {
      require(image.sh_version == shell && map.domain == initial_map.domain &&
                  map.bus == initial_map.bus && map.dev == initial_map.dev &&
                  map.func == initial_map.func && map.resource_size[APP_PF_BAR0] == ocl_bytes &&
                  map.resource_size[APP_PF_BAR4] == bar4_bytes,
              "FPGA shell, PCI location, or BAR mapping changed");
    } else {
      shell         = image.sh_version;
      initial_map   = map;
      ocl_bytes     = map.resource_size[APP_PF_BAR0];
      bar4_bytes    = map.resource_size[APP_PF_BAR4];
      have_identity = true;
    }
    if (attached)
      check_abi();
  }

  void cleanup(bool throw_errors) {
    unsigned errors = 0;
    for (auto *handle : {&bulk_read, &bulk_write, &ocl}) {
      if (*handle != PCI_BAR_HANDLE_INIT) {
        const int status = fpga_pci_detach(*handle);
        *handle          = PCI_BAR_HANDLE_INIT;
        if (status) {
          ++errors;
          std::cerr << "CLEANUP_FAIL detach SDK status=" << status << '\n';
        }
      }
    }
    if (management_open) {
      const int status = fpga_mgmt_close();
      management_open  = false;
      if (status) {
        ++errors;
        std::cerr << "CLEANUP_FAIL management close SDK status=" << status << '\n';
      }
    }
    if (lock_fd >= 0) {
      if (::close(lock_fd) != 0) {
        ++errors;
        std::cerr << "CLEANUP_FAIL lock close errno=" << errno << '\n';
      }
      lock_fd = -1;
    }
    if (throw_errors)
      require(errors == 0, "transport cleanup failed");
  }
};
}  // namespace

int main(int argc, char **argv) {
  try {
    Transport bus(options(argc, argv));
    bus.start();
    std::string line;
    bool quit = false;
    while (std::getline(std::cin, line)) {
      require(line.size() < 16384, "transport request too large");
      std::istringstream input(line);
      std::string operation;
      input >> operation;
      if (operation == "INFO") {
        end_of_request(input);
        bus.info();
      } else if (operation == "READ" || operation == "PEEK") {
        const auto address = argument(input);
        end_of_request(input);
        const auto value = operation == "READ" ? bus.read32(address) : bus.peek(address);
        std::cout << "{\"ok\":true,\"value\":" << value
                  << ",\"resp\":0,\"resp_source\":\"sdk_completion_not_axi_response\","
                     "\"axi_response_observable\":false}";
      } else if (operation == "WRITE" || operation == "POKE") {
        const auto address = argument(input), value = argument(input);
        end_of_request(input);
        if (operation == "WRITE") {
          bus.write32(address, value);
          std::cout
              << "{\"ok\":true,\"resp\":0,\"resp_source\":\"sdk_completion_not_axi_response\","
                 "\"axi_response_observable\":false,\"posted_write_drained\":true}";
        } else
          bus.poke(address, value);
      } else if (operation == "LOAD") {
        const auto path = quoted_path(input);
        end_of_request(input);
        bus.load(path);
      } else if (operation == "DUMP") {
        const auto path  = quoted_path(input);
        const auto bytes = argument(input);
        end_of_request(input);
        bus.dump(path, bytes);
      } else if (operation == "QUIT") {
        end_of_request(input);
        bus.finish();
        std::cout << "{\"ok\":true}";
        quit = true;
      } else
        throw std::runtime_error("unknown transport request");
      std::cout << std::endl;
      if (quit)
        break;
    }
    if (!quit)
      bus.finish();
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "TRANSPORT_FAIL " << error.what() << '\n';
    std::cout << "{\"ok\":false,\"error\":\"transport failed; inspect stderr log\"}" << std::endl;
    return 1;
  }
}
