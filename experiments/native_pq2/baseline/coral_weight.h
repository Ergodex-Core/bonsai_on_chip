#ifndef CORAL_WEIGHT_H
#define CORAL_WEIGHT_H
#include <stdint.h>
/* All addresses are byte offsets into the identical read-only model image.
 * No implicit bank transpose or code remapping. Compiler must use RV32F and
 * -ffp-contract=off -fno-fast-math for the firmware FP32 scale epilogue. */
#ifndef CORAL_WEIGHT_ROM_BASE
#define CORAL_WEIGHT_ROM_BASE UINT32_C(0x40000000)
#endif
#define CORAL_WEIGHT_CONTROL_BASE UINT32_C(0x60000000)
#define CORAL_WEIGHT_IMAGE_BYTES  UINT32_C(463290464)
static inline volatile uint32_t *coral_weight_reg(unsigned offset) {
  return (volatile uint32_t *)(uintptr_t)(CORAL_WEIGHT_CONTROL_BASE + offset);
}
static inline void coral_weight_fence(void) {
#if defined(__riscv)
  __asm__ volatile("fence iorw, iorw" ::: "memory");
#else
  __asm__ volatile("" ::: "memory");
#endif
}
/* Returns 0 when completed, -1 on rejected config or bounded wait timeout.
 * weight_offsets hold one original 34-byte PQ2 block per output unit.
 * Native Q8_K FP32 activation scale is carried unchanged; hardware does integer work only.
 * Native Q8_K combines subgroup INT32 sums, then firmware applies
 * (d_pq2 * d_q8_k) * integer_dot in pinned Prism order. One Q8_K scale
 * belongs to 256 activation lanes, so two successive DOT128 calls share it. */
static inline int coral_weight_dot128(unsigned units, const uint32_t *weight_offsets,
                                      const int8_t activation[128], uint32_t activation_scale_bits,
                                      int32_t output[32][4], uint16_t weight_scale[32],
                                      unsigned poll_limit) {
  if (units == 0 || units > 32 || (*coral_weight_reg(0) & 1))
    return -1;
  *coral_weight_reg(8) = units;
  for (unsigned u = 0; u < units; u++) {
    if (weight_offsets[u] > CORAL_WEIGHT_IMAGE_BYTES - 34)
      return -1;
    *coral_weight_reg(0x100 + 4 * u) = weight_offsets[u];
  }
  for (unsigned i = 0; i < 128; i += 4) {
    uint32_t v = 0;
    for (unsigned j = 0; j < 4; j++)
      v |= (uint32_t)(uint8_t)activation[i + j] << (8 * j);
    *coral_weight_reg(0x200 + i) = v;
  }
  *coral_weight_reg(0x280) = activation_scale_bits;
  coral_weight_fence();
  *coral_weight_reg(4) = 1;
  coral_weight_fence();
  unsigned status = 0;
  while (poll_limit--) {
    status = *coral_weight_reg(0);
    if (status & 6)
      break;
  }
  if ((status & 6) != 2)
    return -1;
  coral_weight_fence();
  for (unsigned u = 0; u < units; u++) {
    for (unsigned g = 0; g < 4; g++)
      output[u][g] = (int32_t)*coral_weight_reg(0x400 + 16 * u + 4 * g);
    uint32_t s      = *coral_weight_reg(0x600 + 4 * (u / 2));
    weight_scale[u] = (uint16_t)(s >> (16 * (u % 2)));
  }
  return 0;
}
#endif
