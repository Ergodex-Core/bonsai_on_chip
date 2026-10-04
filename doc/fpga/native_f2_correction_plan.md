# Native Coral F2 correction plan

This is a correction plan and regression-checker setup. The proposed RTL
correction has not been selected or implemented. No routed timing pass, AFI,
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

## Correction sequence

1. **Complete the matched placement.** Compare setup/hold endpoint counts,
   worst path cone and logic depth, estimated wire share, local congestion,
   SLR crossings, actual clock distribution, and routed-design DRC constraints.
   Do not compare the older different-clock build as a causal locality test.
   Keep the vendor parent floorplan unchanged and do not restart the rejected
   hard candidate unchanged.
2. **Choose the smallest justified change.** If the hard region increases
   congestion, retain the no-child profile as the next baseline. If the long
   completion cone persists, first evaluate unconditional registration of the
   PC payload with the existing one-cycle valid pipeline. `Pipe(io.storeComplete)`
   currently makes the completion cone drive payload-register clock enables;
   capturing PC every cycle may remove that fanout without adding latency.
   Invalid-cycle payload bits may differ and must remain architecturally
   ignored. This is an untested hypothesis: the valid-register path and LSU
   congestion may still dominate. Then evaluate a registered completion event
   carrying its original PC together, or an equivalent shallower completion
   detector if needed. Replacing only valid or adding unrelated delay is
   insufficient.
   LSU `stateFromAction` completion, next-slot acceptance, store writebacks,
   faults and flushes must retain their architectural behavior. Recover the
   exact no-L0 emitter/parameters and source-to-generated-Core provenance
   before regenerating RTL; the immutable emitted inventory alone does not
   prove that a new Chisel emission is equivalent.
3. **Run focused and full-Core regression on EC2.** Use an independent store
   scoreboard and the trace contract below, then existing scalar/vector
   store, exception and LSU flush tests. Exercise TCM and external AXI paths
   with backpressure, repeated PCs, adjacent completions and next-slot reuse.
   Run the corrected native PQ2/Q8_K operator, ordered FP32 layer and complete
   token reference checks before treating the revised Core as inference-ready.
4. **Build one fresh candidate.** Pin source, parameters, generated file
   inventory, Shell/HDK, clock recipe and checkpoint identities. Repeat native
   clock/reset/CDC, legal capacity and placement diagnostics. Start physical
   optimization/routing only after the results justify it and sufficient
   allocated time remains. Changing the Core clock alone is not a valid fix:
   direct Core/main AXI connections require interface/CDC redesign or a
   qualified matching system-clock change.
5. **Require actual physical closure.** Complete route status; zero failing
   setup, hold and pulse-width endpoints; source-bound clock/related-clock
   coverage; legal DFX clock placement; routed DRC and documented per-finding
   global CDC dispositions. Keep strict findings visible. A placement pass,
   small LUT percentage or absence of HDPR-59 in one report is insufficient.
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
backup). The combined project cap remains $500. Do not create duplicate jobs,
extend timers or allocate resources from this test setup.

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
