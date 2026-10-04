# Native Coral F2 correction plan

This is a correction plan and regression-checker setup. The approved first correction is a
50 MHz Core island with complete interface CDC. Its hardware implementation
and component evidence are tracked in a separate source candidate. This PR
contains the plan and checker setup, not that RTL. No routed timing pass, AFI,
physical full-model token, or live FPGA demo is established by this PR.

## Measured problem and comparison

The full Coral/HBM hard-locality placement completed on 2026-10-04, with
zero placement errors and zero escapes from the requested child region.
Nevertheless, setup WNS was **-12.575 ns**, hold WHS **-1.883 ns**, and peak
Global/Long congestion levels **6/7**. The worst same-Core-clock path was:

```text
score/regfile/readDataBits_6_reg[8]/C
  -> score/retirement_buffer/storeComplete_pipe_b_reg[11]/CE
```

Its 4 ns requirement contrasts with a 16.182 ns estimated placed data path:
2.047 ns logic, 14.135 ns estimated wire delay (87.350%), 33 logic levels.
These are placement estimates, not routed timing. The candidate was rejected
for automatic routing and its instance stopped after preserving evidence.

| Diagnostic | Source | Status |
| --- | --- | --- |
| Hard locality | `6f12d0173120da46eb70b419b727b50c1d1187ee` | Placed; timing/congestion fail |
| Same-clock, no-child baseline | `53c6f8ba5d0241b037306a92b51e3bbb0ba98440` | Placement started 17:03 UTC; result pending |

All 182 staged source hashes were compared: 181 match exactly. Only
`build/scripts/native_lsu_locality.tcl` differs. RTL, clock XDC, generated
Core (`0901d091592b03c43d933357907f30087abb2bc4d90bf55ec4f1889e1603c937`),
HBM inputs, vendor Shell and placement recipe match. Both use
`SSI_SpreadLogic_high` and the same 250 MHz MMCM configuration.

Fresh baseline qualification passed three vendor commands: Core clock,
native-wrapper CDC, and legal linked-shell CL resource budgeting. All 66,568
Core FFs use `generated_clk` with master `clk_main_a0`; eight reset FFs have
the intended synchronizer attributes. Four native reset/fault paths each
report zero Critical/Warning CDC findings. Legal CL LUT use is 436,294 of
1,137,720 (38.35%). Capacity is not physical fit or timing closure.

Global synthesis audit still retains 16 Critical/1,282 Warning CDC findings,
13 unconstrained internal endpoints, 918 missing input delays, 811 missing
output delays, OOC HDOOC-3 and unrouted RTSTAT-12. They remain unwaived and
must be reviewed in the appropriate linked/routed context before promotion.

## Source and path attribution

The immutable generated Core inventory records source-tree commit
`81036e734e80bf14862de3d4259a65b5427832a4` and an exact previously emitted
stock-no-L0 snapshot. That tree contained untracked generated files: the Git
commit alone cannot reproduce this inventory. The exact official upstream
revision and original emission command remain unproven. Preserve the emitted
Core SHA above for the initial clock-wrapper correction; do not silently
replace it with current upstream RTL.

The ordered placed STA cone includes registered Regfile read port 6,
branch/fault control, fetch/reorder/instruction-buffer control, LSU slot and
instruction-bus valid, instruction/external AXI ready/response logic, slot
completion detection, and the existing pipelined retirement-PC enable.
It is not evidence of an enqueue-to-output reservation-station data bypass.
The retirement completion valid and PC are already pipelined. Current Chisel
alone cannot establish the ancestry of these exact synthesized cells.

Upstream [Nexus FPGA configuration](https://github.com/google-coral/coralnpu/blob/main/fpga/chip_nexus.core)
uses a 50 MHz starting point on a different FPGA; its DDR clock does not
establish a 250 MHz NPU guarantee. This reference motivates a function-first
experiment, but establishes neither AWS F2 clock legality nor routed closure.

## Correction sequence

1. **Preserve and complete the matched placement.** Compare setup/hold endpoint
   counts, ordered worst-path cone, estimated wire share, local congestion,
   SLR crossings and clock distribution. Keep the vendor parent floorplan
   unchanged. Do not restart the rejected hard candidate unchanged.
2. **Build an isolated 50 MHz Core wrapper first.** Keep Shell
   `clk_main_a0` at 250 MHz and existing DDR/HBM clocks and IP recipes unchanged.
   Preserve the exact generated Core. Evaluate MMCM input 250 MHz, multiplier
   4, VCO 1,000 MHz, output divider 20, phase 0 and duty cycle 0.5, with an actual
   20 ns generated-Core constraint. This is a proposed configuration requiring
   vendor structural and physical qualification, not a clock-only patch.
   Add **two complete AXI clock crossings**, both 128-bit data/32-bit address/
   6-bit ID: host TCM/CSR MAIN-to-CORE slave, and merged instruction/data
   CORE-to-MAIN master. Preserve independent AW/W/B/AR/R handshakes, ordering,
   IDs, strobes, burst metadata and LAST. Use qualified vendor FIFO/converter
   implementation and its supported CDC constraints.
   Synchronize Core halt/fault/WFI status. Latch unsupported-lock protocol
   faults in the Core domain before synchronizing to MAIN. Boot PC is stable
   through reset and release; audit all remaining control crossings.
   Paired reset must flush both bridge ends. Qualify local reset release and
   FIFO reset-busy completion before asserting readiness. MMCM lock loss
   blocks the old lifecycle and requires paired reset; a later re-lock cannot
   reuse queued responses. No broad false-path or multicycle exception may
   hide ordinary logic paths. No LSU retiming is part of this first candidate.
3. **Run interface and Core regressions on EC2.** Exercise independent AW/W/B
   and AR/R stalls, full/empty/wrapped queues, repeated addresses/IDs, bursts,
   exactly-once writes and completions, RRESP/BRESP errors, reset with work
   outstanding, stopped/restarted Core clock, lock loss and paired recovery.
   Qualify the vendor FIFO model, not only a behavioral substitute. Then use
   existing actual Core/DBus-to-AXI scalar/vector, fault and flush tests with
   the pinned native configuration, independently checking results and
   retirement order. Ordered FP32 layer and complete corrected token reference
   checks remain mandatory before inference promotion.
4. **Freeze and qualify the fresh candidate.** Pin source, Core inventory,
   parameters, CDC implementation, Shell/HDK, actual MMCM and checkpoint
   identities. Repeat synthesis, clock/reset/CDC, legal capacity and placement.
   The existing 250 MHz linked/post-synthesis DCP is not automatically valid
   after clock/bridge RTL changes. Reuse only technically proven matching
   checkpoints. Start optimization/routing after evidence review and a budget
   owner allocation with enough runtime; never silently extend current guards.
5. **Require physical closure before frequency increases.** Complete route
   status; zero failing setup, hold and pulse-width endpoints; actual clock
   coverage; legal DFX clock placement; routed DRC; documented per-finding
   global CDC dispositions. Keep strict findings visible. A placement pass or
   resource percentage is insufficient. Increase frequency only after measured
   closure and correctness at the preceding target. If the control cone still
   fails, review memory mapping, placement/fanout/SLR and equivalent mux
   factoring first. Structural LSU/completion changes need exact source
   provenance and architecture regression; they are conditional follow-ups.
6. **Qualify full-model execution and the demo.** After reviewed image-upload/
   AFI approval, bind actual image/transport/firmware identities, prove HBM
   geometry, full model hash and hardware read-only seal, and compare actual
   multi-token FPGA output with the corrected full-model reference. Preserve
   native -1/0/+1/+2 codes, raw scales and ordered Coral FP32 operations.
   The private demo already has EC2 software tests, but stays unavailable
   until physical proof. Current legacy firmware exposes neither per-token
   streaming nor device TTFT/rate counters; leave those measurements absent.

## Store-completion regression setup

`tests/fpga/native_timing/check_store_trace.py` checks traces emitted by a
future independently scored RTL testbench. Its synthetic unit tests check
that the checker accepts valid schedules and rejects lost, duplicated,
premature, late, canceled or wrong-PC events. They do not validate the DUT.

Each trace names the exact sample point (`lsu.storeComplete` or
`retirement.storeComplete`) and provenance (`synthetic` or `rtl_simulation`).
Cycles increase. `dispatch` records unique testbench transaction IDs and
32-bit halfword-aligned PCs. An **independent reference scoreboard** emits
`eligible` when a transaction may complete and `cancel` when the transaction
is flushed/faulted. The DUT observer supplies only sampled valid-event PCs
in `observed` (at most one per cycle). The order at each edge is dispatch,
cancel, eligibility, observation. The scoreboard must express the intended
fault/flush priority explicitly; it must not derive eligibility from the DUT
completion signal. Different transactions may share a PC, as in a loop.

```json
{
  "schema": "coralnpu.lsu_store_completion_trace.v1",
  "signal": "retirement.storeComplete",
  "provenance": "synthetic",
  "cycles": [
    {"cycle": 0, "dispatch": [{"id": 0, "pc": 4096}]},
    {"cycle": 8, "eligible": [0]},
    {"cycle": 9, "observed": [{"pc": 4096}]}
  ]
}
```

The latency bound is explicit and must match the candidate's intended sample
point and pipeline latency. End-of-trace requires every dispatched transaction
to complete or cancel, all eligible events to be consumed, and at least one
successful store. A checker pass always leaves RTL execution verification,
physical FPGA and routed timing acceptance false. Review the actual simulator,
Core inventory, configuration, stimulus/seed and log identities separately.

On existing allocated **EC2**, using a fresh evidence directory:

```sh
python3 -m unittest discover -s tests/fpga/native_timing -p 'test_*.py' -v
python3 tests/fpga/native_timing/check_store_trace.py \
  --trace /absolute/trace.json --trace-sha256 EXACT_TRACE_SHA256 --max-latency 2
```

Reuse the existing `RVV_LOAD_STORE_TESTCASES` in `tests/cocotb/BUILD`, especially
`store_unit_masked`, `store_unit_all_vtypes_test`, `store_strided_all_vtypes_test`,
the indexed/segment stores, and all ten `lsu_fault_*` cases. The exact native
no-L0 testbench binding and new trace observer remain to be implemented after
the emitter/parameter provenance is recovered. Do not substitute a mini-Core
test or synthetic trace pass for that full target regression.

## Time, resources and dependencies

The matched placement is bounded on the existing F2 host. Its stop deadlines
remain **2026-10-04 20:58:42 UTC** (guest) and **21:08:42 UTC** (independent
backup). The user subsequently withdrew the $500 project cap and prioritized a
correct working demo. Existing concrete allocations and guards remain binding
until replacement controls and the next allocation are verified. Do not create
duplicate jobs, extend timers or allocate resources from this test setup.

Previous observed vendor durations: synthesis approximately 23 minutes,
fresh clock/CDC/budget approximately 12 minutes, placement approximately
49–54 minutes, physical optimization plus routing approximately 5 hours
40 minutes in one failed attempt. Those are planning anchors, not a promise
of closure. A full fresh vendor iteration is about seven hours before RTL
debug, numerical checks, report review or board work, and does not fit this
remaining window. Image generation and first full-model board execution do
not have project-specific successful duration measurements.

The firmware owner must provide the complete corrected source/toolchain/ELF
binding and raw full-model reference. Corrected first-layer results were
relayed (stock and ROM 2,048 exact outputs), but those do not qualify complete
inference. The existing loader's historical source hashes deliberately reject
changed firmware; prepare a coherent new runtime profile and EC2 validation
once those inputs arrive. No duplicate CPU full-model job is started here.

## Validation recorded for this PR

Final EC2 checker attempt002 passed **21/21 synthetic cases** on 2026-10-04
at 17:17:42 UTC (0.136 seconds in unittest; 193 ms bounded service runtime).
The unit used one CPU and 512 MiB limits alongside the independently running
placement. No RTL simulation or model inference ran in that unit.

Test log SHA256:
`2084c57c8575e0e6fe34bc2dea0d1d9d282f89913ef49d84d7303edec9c6ae47`.
Checker source SHA256:
`b229680d30885095071be03cc0ea9124129ecebeb38f68c20dc8b8dfb0909315`.
Test source SHA256:
`45652ae71bbc80aecbb0e3b028f68f116fe40fb5ae37486de65230199903f4ad`.

Python formatting, Markdown lint and repository macro checks run on EC2;
see the PR validation record for their results. Matched placement, the new
actual-DUT trace adapter, full-Core correctness, routed signoff, complete
corrected firmware binding and physical tokens remain pending.

Separate continuation evidence on EC2: both vendor-XPM bridge orientations
passed 3,200 exact channel-payload comparisons, with full/wrapped queues,
independent stalls, error response bits and stopped-Core-clock paired reset.
The actual installed vendor MMCM model passed 60 consecutive 20 ns periods,
paired reset and injected digital lock-loss recovery. These component checks
do not validate full AXI memory behavior, Coral firmware, routed DFX legality
or physical tokens. Their RTL is not included in this plan/checker PR.
