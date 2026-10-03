# Full Coral inference integration gate

The variants preserve the E1-compatible-v1 leaf ABI in `contract.md`. All model
operations, including ordered FP32 scaling, remain on the FPGA-resident Coral
core and engine. Host work is limited to loading, control and independent checks.

## Two different integration snapshots

The historical simulator is based on `81036e734e80bf14862de3d4259a65b5427832a4`
with an uncommitted ROM/engine overlay. This experiment's publication base is
`26bded5bf809267506058df6bf0b296dddeeaf25`. Neither is silently substituted for
the other. The baseline leaf hash and noL0 emitted core pins are in
`../source-manifest.json`.

Historical E1 uses model ROM 0x40000000 and engine MMIO 0x60000000. The native F2
integration owner's stock-core path instead maps immutable model storage at
0x80000000, external code at 0xa0000000 and mutable state at
0x9c000000..0xa0000000. These are distinct maps. A leaf replacement alone does not
complete native F2 integration, and old Q1/DOT1 mailbox firmware cannot substitute
for native PQ2_0/Q8_K firmware.

## Integration order

1. In a fresh isolated snapshot, bind the selected leaf to the existing
   `CoralWeightBlackBox` and `CoralWeightPeripheral`. Preserve the noL0 emitter and
   full Core/RVV/fpnew source inventory. Do not regenerate from an unproven emitter.
2. For E1-fetch, bind the queued single-ID read adapter to the immutable model
   window. All AXI responses must stay ordered and obey backpressure; retain
   appropriate arbitration with instruction/data DDR. The one-outstanding adapter
   is a valid functional fallback but cannot hide memory latency.
3. Compile the existing `coral_matmul_engine_q8k` firmware without FMA or fast-math.
   Its explicit row-stride/file offsets and final active-unit count must remain
   unchanged; the output-head tail has 21 outputs.
4. First run the same original-weight row32 Q/down and four-row regression,
   comparing ordered FP32 outputs bit-for-bit. Preserve fixture, source, compiler,
   emitter, ELF and model hashes, plus the exact noL0 parameter record.
5. Run first-layer and complete-model firmware for the same prompts and token
   counts as S0/R1/E1, then integrate with the native F2 memory map and boot path.
   The host must not replace any model operation. The independent full-token
   stock/ROM runs retain priority.
6. Obtain target synthesis, post-route timing and board evidence from the FPGA
   integration owner. Report LUT/FF/BRAM/URAM/DSP plus shell reservations, achieved
   clock, HBM stalls and complete firmware cycles separately from leaf estimates.

This branch provides leaf implementations and a matched operator benchmark.
Full-core re-emission/build, compiled firmware overhead, complete-model token
comparison, native F2 resource/timing and physical execution are pending gates.
The allocated short operator slot does not authorize long full-model or physical
F2 jobs. No earlier partial-layer result is presented as a completed token.

## Approved FPGA model storage contract

The user approved HBM-backed read-only model storage as the FPGA equivalent of ROM
on 2026-10-03. Physical ASIC ROM remains a separate implementation target.

Before inference, the host loads the complete native GGUF into the selected model
aperture, verifies its byte count and SHA256 against the pinned manifest, and seals
it. The shell must reject writes to that model aperture while inference is active;
a software promise alone is insufficient. Activations, KV state, mailbox and
outputs occupy separately bounded writable intervals. The queued read adapter
exposes no write channel and does not itself implement the shell's seal or loader.
These controls belong to the FPGA integration owner, with no duplicate shell here.

All storage addresses below the leaf are native file-relative byte offsets. Bind
`HBM_BASE` to the shell's model aperture, enforce bounds there, and preserve
single-ID ordered reads, error propagation and backpressure. Reset requires a
coordinated flush of outstanding AXI responses. The current latency/II/capacity
sweep exercises realistic constraints as explicit scenarios, not calibrated HBM
measurements; board measurements remain required before a hardware speed claim.
