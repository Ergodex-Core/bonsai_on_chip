/* ERG-102 physical smoke probe. Uses the public AWS FPGA SDK API and the
 * documented cl_axil_reg_access register ABI. This is host test code. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "fpga_mgmt.h"
#include "fpga_pci.h"

static pci_bar_handle_t bar = PCI_BAR_HANDLE_INIT;
static unsigned checks, additions;

static void require(int yes, const char *name) {
  ++checks;
  if (!yes) {
    fprintf(stderr, "FAIL check=%u name=%s\n", checks, name);
    exit(1);
  }
}
static uint32_t read32(uint32_t address) {
  uint32_t value = 0;
  int rc         = fpga_pci_peek(bar, address, &value);
  if (rc) {
    fprintf(stderr, "FAIL peek address=0x%x rc=%d\n", address, rc);
    exit(1);
  }
  return value;
}
static void write32(uint32_t address, uint32_t value) {
  int rc = fpga_pci_poke(bar, address, value);
  if (rc) {
    fprintf(stderr, "FAIL poke address=0x%x rc=%d\n", address, rc);
    exit(1);
  }
}
static void add(uint32_t a, uint32_t b) {
  uint64_t expected = (uint64_t)a + b;
  write32(0, a);
  write32(4, b);
  require(read32(0) == a, "operand A readback");
  require(read32(4) == b, "operand B readback");
  write32(0x10, 1);
  uint32_t status = 0;
  unsigned poll;
  for (poll = 0; poll < 1000; ++poll) {
    status = read32(0x10);
    if (status & 2)
      break;
    usleep(1000);
  }
  require(poll < 1000, "bounded completion poll");
  require(status == 2, "ready set, start cleared, reserved bits zero");
  uint32_t sum = read32(8);
  require(read32(0x10) == 2, "ready remains until both result registers read");
  uint32_t carry = read32(0xc);
  require(sum == (uint32_t)expected, "sum result");
  require(carry == (uint32_t)(expected >> 32), "carry result");
  require(read32(0x10) == 0, "completion clears after result acknowledgement");
  ++additions;
  printf("addition=%u a=0x%08x b=0x%08x sum=0x%08x carry=%u expected=0x%09llx PASS\n", additions, a,
         b, sum, carry, (unsigned long long)expected);
}
static uint32_t random32(uint32_t *state) {
  *state ^= *state << 13;
  *state ^= *state >> 17;
  *state ^= *state << 5;
  return *state;
}
int main(int argc, char **argv) {
  if (argc != 2 || (strcmp(argv[1], "reset") && strcmp(argv[1], "full"))) {
    fprintf(stderr, "usage: %s reset|full\n", argv[0]);
    return 2;
  }
  require(fpga_mgmt_init() == 0, "management init");
  require(fpga_pci_attach(0, 0, 0, 0, &bar) == 0, "attach application PF0 BAR0");
  if (!strcmp(argv[1], "reset")) {
    for (uint32_t address = 0; address <= 0x10; address += 4) {
      uint32_t value = read32(address);
      printf("reset address=0x%02x value=0x%08x\n", address, value);
      require(value == 0, "register reset value");
    }
  } else {
    const uint32_t patterns[] = {0, 1, 0xffffffffu, 0xaaaaaaaau, 0x55555555u, 0x12345678u};
    for (unsigned i = 0; i < sizeof(patterns) / sizeof(patterns[0]); ++i) {
      write32(0, patterns[i]);
      write32(4, ~patterns[i]);
      require(read32(0) == patterns[i], "pattern loopback A");
      require(read32(4) == ~patterns[i], "pattern loopback B");
      printf("loopback A=0x%08x B=0x%08x PASS\n", patterns[i], ~patterns[i]);
    }
    const uint32_t pairs[][2] = {{0, 0},
                                 {1, 2},
                                 {0xffffffffu, 1},
                                 {0xffffffffu, 0xffffffffu},
                                 {0x80000000u, 0x80000000u},
                                 {0x12345678u, 0x9abcdef0u}};
    for (unsigned i = 0; i < sizeof(pairs) / sizeof(pairs[0]); ++i)
      add(pairs[i][0], pairs[i][1]);
    uint32_t seed = 0x10212415u;
    for (unsigned i = 0; i < 256; ++i) {
      uint32_t a = random32(&seed), b = random32(&seed);
      add(a, b);
    }
    uint32_t prior_sum = read32(8), prior_carry = read32(0xc);
    write32(8, ~prior_sum);
    write32(0xc, ~prior_carry);
    require(read32(8) == prior_sum, "sum read-only write rejection");
    require(read32(0xc) == prior_carry, "carry read-only write rejection");
    uint32_t prior_a = read32(0), prior_b = read32(4);
    const uint32_t invalid[] = {0x14, 0xf0, 0x100};
    for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
      write32(invalid[i], 0xcafef00du);
      uint32_t got = read32(invalid[i]);
      printf("invalid address=0x%02x response=0x%08x\n", invalid[i], got);
      require(got == 0xdeadbeefu, "invalid address sentinel");
    }
    require(read32(0) == prior_a && read32(4) == prior_b, "invalid writes do not mutate operands");
    for (unsigned poll = 0; poll < 10; ++poll) {
      require(read32(0x10) == 0, "no spurious completion without start");
      usleep(1000);
    }
    write32(0, 0xdead1234u);
    write32(4, 0xbeef5678u);
    require(read32(0) == 0xdead1234u && read32(4) == 0xbeef5678u,
            "poison state present before reconfiguration reset");
  }
  require(fpga_pci_detach(bar) == 0, "detach");
  printf("ERG102_PROBE_PASS mode=%s checks=%u additions=%u seed=0x10212415\n", argv[1], checks,
         additions);
  return 0;
}
