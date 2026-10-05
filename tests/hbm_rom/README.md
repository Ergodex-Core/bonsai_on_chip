# HBM ROM-equivalent CPU regression

The integrated path is `first_slice_top` → `f2_memory_bridge` →
`hbm_line_bridge` → a byte-addressed HBM256 AXI model. It uses the production
store, immutable loader gate, mailbox, DOT128 and CDC adapter. The endpoint
checks native pseudochannel 15, two 32-byte beats per 64-byte logical line,
IDs and burst attributes. It models independently stalled address/data channels,
6–24 HBM-cycle initial read handshake latency, 3–9 interbeat latency and 4–20 write-response
latency. These delays are test parameters, not measured device timings.

The default clock-period ratio 18:10 represents 250 MHz core / 450 MHz HBM.
The contract suite also runs with periods 4:10 and phase 1, reversing the clock
speeds. Digital simulation does not model analog metastability, HBM calibration,
refresh behavior, thermal effects, or physical timing closure.

## One-command canonical comparison

Use Python 3.11+ with `gguf==0.19.0`, Verilator 5.048, GNU Make and a C++17
compiler. Supply the authorized local GGUF pinned in
[the canonical image instructions](../../utils/weightstore/README.md): revision
`210a9e99f79cb184909d49595906526eb2b3dd9a`, SHA-256
`3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3`.
Reserve 2 GiB outside the checkout for generated images and readbacks.

```bash
python3 tests/hbm_rom/validate.py \
  --gguf /absolute/Bonsai-1.7B-Q1_0.gguf \
  --out /absolute/new-hbm-validation --jobs 4
```

The command refuses an existing output directory. It prepares the unchanged
242,357,184-byte native image and frozen 396-case fixture, builds RTL from the
current sources, runs the HBM store, native HBM adapter and PCIS bridge tests, runs the integrated
contract suite at both clock ratios plus transport-protocol tests, and executes
396 actual Q1_0 cases and six separate synthetic ternary cases. The shared
first-slice runner loads the full image, drains accepted writes, reads back and
hashes every byte, seals it, attempts rejected writes, runs DOT128, and hashes
the full readback again. The native expected result is derived independently
from the unchanged 18-byte groups; the FPGA arithmetic returns exact int32
accumulation and raw FP16 scale. One disclosed FP32-rounded scale multiplication
runs on the host. Synthetic zeros/illegal codes do not relabel Q1_0 as ternary
or establish full-model inference.

`validation.json` retains commands, exit codes, tool versions, source and log
hashes, timestamps and completed suite summaries. Each native/synthetic run has
per-case arithmetic, cycles, stalls and line-read counts. All outputs explicitly
set `fpga_executed: false`. A failed or interrupted step cannot produce a PASS
report. The binary manifest binds the HBM sources, mapping and clock settings.
Source changes during the run invalidate the final report.

## Tool provenance used for the checked run

The cloud workspace used the Debian amd64 `verilator_5.048-1_amd64.deb` package
from [Debian's Verilator archive](https://deb.debian.org/debian/pool/main/v/verilator/),
SHA-256 `e387099b34343ee76d3322e3cc86b3059d65c716e1d3fb2469fc366d52f9b596`.
It was extracted into a writable tools directory without system installation.
Its version string is `Verilator 5.048 2026-04-26 rev vUNKNOWN-built20260429`.
For an extracted package set `VERILATOR_ROOT` to its `usr/share/verilator`
and pass `--verilator /absolute/extracted/usr/bin/verilator`.
Compilation is limited to four jobs. No FPGA build, image registration, remote
host mutation or AWS allocation is performed by this command.

## Focused tests

`test_hbm.py` extends the existing mailbox tests with HBM identity, native beat
counts, both halves of sealed lines, controller readiness loss with running/stopped HBM clocks, volatile reset
and reload/reverify/reseal, and first/last RRESP, wrong RID and early RLAST faults.
The inherited cases cover unsealed reads, stale epochs, range errors, mutable
configuration denial and unacknowledged completions. The original simulator and
DDR tests remain separate.

Set `FIRST_SLICE_MODEL_SERVER` to the newly built `Vhbm_rom_sim_top` to run these
tests. `HBM_SIM_CORE_HALF_PERIOD`, `HBM_SIM_HBM_HALF_PERIOD` and `HBM_SIM_PHASE`
control small-suite clock stress; every transport INFO response records them.
The main validation command binds its selected defaults in the build manifest.
The `RESET`, `READY`, `PAUSE` and `FAULT` commands exist only in this simulation transport
and cannot provide an unguarded hardware writer or reset alias.

Physical FPGA evidence, vendor simulation, resource/timing reports and the
Week 3 firmware/full-layer helpers remain separate acceptance gates.
