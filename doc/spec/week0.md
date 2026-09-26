# ERG-102 Week 0 interface and evidence baseline — v0

Status: v0 candidate for final ERG-102 review. Issue owner: Rachit Tibrewal
(the existing Linear assignee). Architecture and FPGA execution: Codex in this
task. Technical review: a separate Codex agent, recorded as automated review of
the final source revision; this is not a human or GitHub APPROVED review.
Permanent engineering staffing for later milestones is not assigned here.

## Scope and source of truth

Week 0 delivers a versioned interface/fixture contract, a runnable CoralNPU
simulator baseline, AWS F2 vendor-shell simulation and physical MMIO bring-up,
and the accompanying reproducible report. The physical target is the pinned
official AWS shell example named in the hardware report. Its successful
execution establishes environment access and MMIO operation; it does not
establish a Bonsai accelerator or full-model implementation.

The first weight-store implementation, loader-lock RTL, DDR/HBM integration,
CoralNPU weight kernels and model inference are follow-on deliverables. The
product's 27B ternary model remains a separate target and format. Neither the
1.7B fixture nor an official AWS example validates that target.

The baseline RTL inspected for this specification is commit
`613540ca38c7194aa079721459a66ba78c82a854`. The final report binds this
specification, its companion [weight-store contract](../microarch/weightstore.md), tests and evidence to the final PR commit and file hashes.
Current RTL and the selected configuration take precedence over historical
overview prose. Changes to an interface, packing format or numerical contract
require an explicit version change and affected-test revalidation.

## Existing execution and instruction contract

Retain CoralNPU's current compute pipeline. The first integration uses existing
loads, stores and control/status operations; it adds no RISC-V opcode. The
scalar smoke target is `CoreMiniAxi`; the existing FPGA SoC configuration is
RV32 with RVV enabled, 128-bit load/store and fetch buses, scalar floating point
and BF16 support, as defined in `hdl/chisel/src/soc/SoCChiselConfig.scala`.
These are named configurations, not the raw `Parameters` defaults, whose bus
widths are 256 bits. VME/Zvt remains an optional experimental configuration.

Freeze implemented instruction encodings by the versioned sources
`hdl/chisel/src/coralnpu/scalar/Decode.scala`, the floating-point instruction
table, `hdl/chisel/src/coralnpu/rvv/RvvDecode.scala`, and
`hdl/verilog/rvv/inc/rvv_backend_opcode.svh`. The existing
`//hdl/chisel/src/coralnpu:decode_table_dump` exports instruction masks/matches
and CSR numbers for named scalar/RVV/VME configurations;
`//hdl/chisel/src/coralnpu:alphabet_core_mini_axi` is its scalar JSON target.
The decoder sources remain authoritative for backend-specific instructions.

`CoreAxi` routes external data transactions through `DBus2Axi` with AXI ID 0;
external instruction fetch uses ID 1 when enabled. The SoC wraps this route in
TL-UL. Preserve IDs, access sizes, byte lanes, backpressure and error propagation.
Do not infer instruction/data permissions from ARPROT alone. The historical
integration guide's assertion that every master ID is zero is not the current
contract. Do not add variable-latency memory to the internal fixed-latency
ITCM/DTCM fabric.

The external core control registers remain offsets `0x0` RESET_CONTROL,
`0x4` PC_START and `0x8` STATUS within the selected CSR page.
RESET_CONTROL bits 0/1 hold reset/clock-gate, initially asserted; software writes
zero to other bits. PC_START initially captures `boot_addr` and is programmed
before release. STATUS bits 0/1 report halted/fault. Initialize program/data,
program PC_START, write RESET_CONTROL=1 to ungate while holding reset, then 0
to start, and poll completion/fault. Debug and CSR-output registers retain their
existing `CoreAxiCSR.scala` offsets. The reset, readback and invalid-access
behavior is covered by `CoreAxiCSRTest.scala`; the report identifies the actual
run evidence. A new accelerator mailbox ABI is not silently implied by these
existing registers.

## Memory map

All addresses below are byte addresses. Existing maps are frozen by
`Parameters.scala` and `soc/CrossbarConfig.scala`; the new reservations are
specified interfaces awaiting RTL implementation.

| Region | Base / size | Interface or status |
| --- | --- | --- |
| Default ITCM | `0x00000000` / 8 KiB | Existing core-local program memory |
| Default DTCM | `0x00010000` / 32 KiB | Existing core-local writable data |
| Default core CSR | `0x00030000` / 4 KiB | Existing control/status |
| High-memory option | ITCM `0x0`, DTCM `0x00100000`, CSR `0x00200000` | Selected TCM sizes up to 1 MiB each; no map mixing |
| CLINT / PLIC | `0x02000000` / 64 KiB; `0x0c000000` / 64 MiB | Existing 32-bit devices |
| Boot ROM | `0x10000000` / 32 KiB | Existing 32-bit device |
| SRAM | `0x20000000` / 4 MiB | Existing 128-bit device |
| Weight aperture | `0x30000000` / 256 MiB | New reserved read-only, non-executable 128-bit TL slave |
| Peripheral pages | `0x40000000`, `0x40001000`, `0x40010000`, `0x40020000`, `0x40030000`, `0x40040000`, `0x40050000`, `0x40070000` | Existing UART0, clock table, UART1, SPI, GPIO, I2C, DMA, flash SPI; 4 KiB each |
| Weight control | `0x40080000` / 4 KiB | New reserved 32-bit control page; ABI in companion contract |
| ISP control | `0x50000000` / 1 MiB | Existing separate clock domain |
| DDR control | `0x70000000` / 4 KiB | Existing 32-bit device |
| DDR memory | `0x80000000` / 2 GiB | Existing 128-bit TL route; backend bridge/controller is platform-specific |

The weight aperture uses logical offsets, with offset zero at `0x30000000`.
FPGA physical base/channel mapping is private to the backend. No executable or
writable alias is allowed; all CPU, DMA, debug and host write paths must be
covered by the eventual guard. Core/DMA accesses before readiness complete with
an error rather than hanging. Existing boot has no weight-ready handshake;
weight-dependent jobs must add that dependency in their implementation PR.

## Initial fixture, read protocol and numerics

Use `prism-ml/Bonsai-1.7B-gguf` revision
`210a9e99f79cb184909d49595906526eb2b3dd9a`, file
`Bonsai-1.7B-Q1_0.gguf`, SHA-256
`3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3`.
The initial native image preserves all 197 Q1_0 matrix payloads and 113 FP32
vectors byte-for-byte, in GGUF tensor order, with zero padding only between
64-byte-aligned tensor starts and after the final tensor. Expected padded size:
242,357,184 bytes. The packer report, not this specification, proves the actual
image hash, tensor checks and padding checks. Store the tied embedding/head once
and identify its alias in the manifest.

Q1_0 is binary sign quantization, not a three-valued ternary format. Each group
has 128 weights in 18 bytes: a little-endian FP16 scale followed by 16 sign
bytes, LSB first, with `weight[j] = (2*bit[j]-1)*scale`. Rows are unpadded.
FP32 vectors retain their little-endian payloads. No requantization or whole
model FP32 expansion is allowed in the canonical packed image.

Freeze the companion contract's common read interface: aligned 64-byte lines,
48-bit logical offsets, 512-bit responses, 8-bit per-client tags, 32-bit epochs,
16 outstanding requests per streaming client and ready/valid handshakes.
Exactly one tagged response follows each accepted request unless all clients
and the store undergo coordinated reset. Reordering is permitted across tags;
tag reuse waits for consumption. Invalid range/alignment/epoch returns explicit
error and zero data. The first CPU bus adapter permits one outstanding access,
extracts correct narrow-load lanes and rejects stores/instruction fetches.
Groups crossing a line or 4 KiB boundary are assembled explicitly. AXI bursts
split at 4 KiB; errors drain transactions before IDs can be reused.

ASIC ROM, behavioral ROM and FPGA DDR/HBM must expose identical bytes and
responses to unchanged kernels. FPGA loading closes and drains every write
path, reads back and verifies the image, then seals its descriptor before READY.
Warm core reset cannot unlock the store. A store/controller reset invalidates
readiness, epoch and caches and requires coordinated recovery. Detailed control
register encodings, seal states and fault handling are fixed by the companion
contract; they are proposed RTL interfaces, not implemented hardware claims.

Packed bytes, integer values, protocol status and deterministic decode results
require exact equality. The initial future arithmetic oracle decodes the same
Q1_0 weights to FP32 and performs the defined operation in FP32. For finite
arithmetic tensors use `abs(actual-reference) <= 1e-3 + 1e-3*abs(reference)`
elementwise, with shapes, scale placement, accumulation/reduction ordering and
operation boundaries fixed before the implementation run. NaN/Inf is a failure
unless an explicitly separate ISA corner-case test defines its expected result.
Report maximum absolute/relative error and failing counts, not only allclose.
Compare model logits only at equal token histories and require exact generated
token IDs for an end-to-end parity claim. This tolerance does not validate a
later integer-accumulator, BF16 or 27B ternary implementation; those changes need
their own frozen reference, rounding/overflow rules and affected tests.

## Cycle budget and performance protocol

Use a transparent model rather than converting an environment smoke result into
model throughput. For a declared workload, record operation count `O`, useful
and physically transferred bytes `B`, physical engine count `N`, sustained
operations/engine-cycle `P`, core clock `f`, measured memory bandwidth `BW`,
non-overlapped overhead `T_overhead`, and separately measured load/initialization
time. Compute an optimistic overlap bound and a conservative serialized budget:

```text
C_compute = ceil(O / (N * P))
T_compute = C_compute / f
T_memory = B / BW
T_overlap_bound = max(T_compute, T_memory) + T_overhead
T_serial_budget = T_compute + T_memory + T_overhead
```

Count one multiply-accumulate consistently as either one MAC or two operations,
and label which. Use the bottleneck across all pipeline stages and memory
channels. Scheduling, dependencies, padding and stalls add cost; the bound is
not a predicted achieved latency. Never substitute host simulator runtime for
RTL cycles or multiply throughput by logical tile count.

The existing Nexus target sets a 50 MHz default in `fpga/chip_nexus.core`.
Propose this as the modeling baseline only; actual F2 shell/core clocks belong
to the hardware report. At an explicitly idealized one 64-byte line per cycle,
a single sweep of the padded fixture takes 3,786,831 line cycles, or 75.73662 ms
at 50 MHz (3.2 GB/s). This is arithmetic from image size and an assumed transport
rate, not a benchmark, token latency or demonstrated ROM/DDR bandwidth.

**Engineering assumption for v0: 2×/4× means clock targets of 100/200 MHz**
against the existing 50 MHz Nexus baseline, with unchanged engine count,
workload and memory assumptions. These names are configuration targets, not
achieved F2 timing or measured end-to-end speedups. The owner may revise this
assumption before the Week 4 configuration freeze; record the change and update
dependent budgets. Every measured comparison uses the same workload/model and
states actual clocks and clock-normalized cycles separately.

For the later eight-tile target, propose eight physical compute engines within
one AFI only when shell-inclusive resources, routing, clocking and bandwidth
support that fit. Eight logical work partitions time-multiplexed over fewer
engines must be reported with the actual physical engine count and cannot be
presented as eight-engine throughput. Week 0 specifies this distinction and
does not demonstrate eight-engine fit.

For instruction baselines, retain `tests/cocotb/isa_cycle_bench_test.py` and its
45 named workloads, 32 repetitions, cycle-counter delta minus two read-overhead
cycles, seed 42 and named `RvvCoreMiniAxi` configuration. Preserve
`measured_instruction_cycles.json` and its Markdown companion from test outputs.
Its 100,000-cycle halt timeout is a correctness bound, not a throughput target.

The [complete cycle measurements](../../reports/ERG-102/measured-instruction-cycles.json)
and [retrieval provenance](../../reports/ERG-102/isa-cycle-baseline-provenance.json)
preserve all 45 actual measurements from
`//tests/cocotb:isa_cycle_bench_test_isa_cycle_bench_test`: PASSED, one run, reused
from cache in the final full-suite attempt. The preserved test log identifies
Verilator 5.052 and cocotb 2.0.0. The measurement JSON's SHA-256 is
`57e734f54529f369a510ceef9f564da26dc4359287888d2fef23e23a8c87c480`.
These are workload-specific RTL cycle costs, not physical clock measurements
or general per-instruction pipeline latencies.

| Benchmark | Recorded cycles/instruction |
| --- | ---: |
| `add` | 1.0 |
| `lw` / `sw` | 4.0 / 2.0625 |
| `mul` | 2.0 |
| `fdiv.s` / `fsqrt.s` | 12.96875 / 16.5625 |
| `vadd.vv` / `vfadd.vv` | 4.15625 / 7.15625 |

The initial non-regression budget for the unchanged configuration and workloads
is no increase over each recorded normalized cycle count. A change that alters
this budget must report the regression and adopt a reviewed new budget before
being claimed as a performance pass. This is a future comparison rule, not an
additional benchmark run.

For future hardware workloads, declare warm-up/reset state, shapes, batch and
context, then capture at least 30 steady-state repetitions, median/p95 latency,
cycle counts, bytes, bandwidth and stalls. Separate cold load time, simulator
cycles, host wall time, synthesis estimates, routed timing and physical results.
Freeze each implementation's quantitative pass threshold before that run.

## Acceptance and ownership record

ERG-102 closes only after the final integration PR is reviewed, non-draft and
merged, with the committed report and a readable Linear attachment. The report
must identify source/fixture/tool hashes, clean-checkout commands, expected
results, failures/skips/unsupported cases, immutable raw evidence locations and
the independent technical review decision against the final source revision.

The simulation gate consists of the supported repository baseline and the
declared core reset/control/readback/completion/error smoke evidence. Preserve
the full 1,332-target baseline's backend and cache distinctions, the three RVV
early returns in the scalar smoke, and the limited four-configuration Arcilator
pilot. Unsupported full-suite Arcilator frontends are disclosed follow-on work.
The FPGA gate consists of the selected vendor example's simulation plus an
actual loaded physical shell/MMIO run, with instance/slot, shell/AFI, clocks,
tools, command/readback logs and any available build reports. A prebuilt vendor
AFI must be labeled as such; it is not a locally synthesized Bonsai bitstream.

Root/owner records physical results and adopts the stated v0 assumptions in the
final approval record.
No passing baseline predating new RTL is counted as validation of that RTL.
The generic FP32 projection diagnostic and 27B product inference remain separate
from this Week 0 acceptance boundary.
