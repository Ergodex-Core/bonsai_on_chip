# Native Coral F2 correction plan

This is a correction plan and regression-checker setup. The approved first correction is a
50 MHz Core island with complete interface CDC. Its hardware implementation
and component evidence are tracked in a separate source candidate. This PR
contains the plan and checker setup, not that RTL. No routed timing pass, AFI,
physical full-model token, or live FPGA demo is established by this PR.

## Problem and target

The correction targets a long same-Core-clock control path from the registered
Regfile output to the retirement store-completion enable. The cone includes
branch/fault control, fetch and reorder control, LSU instruction-bus valid,
AXI readiness/response handling and slot completion detection. Existing
retirement valid and PC are already pipelined.

Preserve the exact generated Core for the first wrapper correction. The
available inventory includes generated files that are not reproducible from
the recorded source-tree commit alone. Recover the original emitter command,
parameters and upstream ancestry before making generated-Core RTL changes.
Keep source/checkpoint identities and detailed physical reports in the private
execution evidence. Capacity alone does not establish physical fit or timing
closure, and placement estimates do not establish routed timing.

Upstream [Nexus FPGA configuration](https://github.com/google-coral/coralnpu/blob/main/fpga/chip_nexus.core)
uses a 50 MHz starting point on a different FPGA; its DDR clock does not
establish a 250 MHz NPU guarantee. This reference motivates a function-first
experiment, but establishes neither AWS F2 clock legality nor routed closure.

## Correction sequence

1. **Compare source-matched physical experiments.** Compare setup/hold endpoint
   counts, ordered worst-path cone, estimated wire share, local congestion,
   SLR crossings and clock distribution. Keep the vendor parent floorplan
   unchanged. Preserve terminal reports and reject incomplete diagnostics.
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

## Resources and dependencies

Use the existing allocated EC2 execution route for tests and vendor tools.
Keep allocation, deadlines, resource identity and detailed build history in
private execution records. Verify both guest and independent stop safeguards
before replacing prior controls. Do not create duplicate jobs or silently
extend runtime. A fresh physical iteration must fit its concrete allocation.

Corrected full-model firmware and transformer/helper sources were recovered
and hash-verified privately. Exact first-layer comparisons passed for stock
and ROM variants; complete corrected token/logit comparison remains pending.
The historical loader deliberately rejects changed firmware source hashes.
Prepare and test a coherent new runtime profile with complete build, toolchain,
source and numerical-reference binding. Verify compiler/runtime availability
on EC2 and coordinate with the existing firmware owner to avoid duplicate
full-model jobs.

## Diagnostic robustness

Vendor driver exit status alone is insufficient to accept a diagnostic.
Require the exact completion marker, placed checkpoint and complete report
set, and explicitly reject abnormal termination or segmentation-fault text.
Save the diagnostic checkpoint immediately after placement, before report
queries; a checkpoint alone is still insufficient for acceptance.

Preserve plain hierarchy names across placement and reacquire current cell
collections before site/utilization queries. Do not retain netlist object
handles through transformations. Test missing/changed hierarchy rejection,
checkpoint-before-report ordering and injected reporting failure. Control-flow
mocks do not prove vendor placement recovery; verify the actual fresh vendor
run and retain every physical finding.

## Validation recorded for this PR

EC2 checker validation passed **21/21 synthetic cases**.
No RTL simulation or model inference ran in that checker unit.

Test log SHA256:
`2084c57c8575e0e6fe34bc2dea0d1d9d282f89913ef49d84d7303edec9c6ae47`.
Checker source SHA256:
`b229680d30885095071be03cc0ea9124129ecebeb38f68c20dc8b8dfb0909315`.
Test source SHA256:
`45652ae71bbc80aecbb0e3b028f68f116fe40fb5ae37486de65230199903f4ad`.

Python formatting, Markdown lint and repository macro checks run on EC2;
see the PR validation record for their results. Incomplete physical diagnostics
remain rejected. The actual-DUT store trace adapter, broad full-Core ISA regression,
routed signoff, complete corrected model comparison and physical tokens remain
pending.

Separate continuation evidence on EC2: both vendor-XPM bridge orientations
passed 3,200 exact channel-payload comparisons, with full/wrapped queues,
independent stalls, error response bits and stopped-Core-clock paired reset.
The actual installed vendor MMCM model passed 60 consecutive 20 ns periods,
paired reset and injected digital lock-loss recovery. These component checks
do not validate full AXI memory behavior, Coral firmware, routed DFX legality
or physical tokens. Their RTL is not included in this plan/checker PR.

A separate continuation test executed the unchanged complete Coral Core at
50 MHz with actual vendor clock/FIFO models: two scalar fixture epochs passed
2,500 checks, four data reads, 17 instruction fetches, two stores and one queued
response discard across paired reset. The taken branch suppressed the wrong
path store. This establishes that bounded scalar fixture, not RVV/FPU numerical
coverage, complete model inference or physical FPGA execution.

## Performance after correctness

First qualify correct full-model FPGA inference and the working prompt demo.
Then measure device and end-to-end tokens per second and time to first token
under a pinned model, prompt/context, generation length, sampling policy and
precision. Count generated tokens separately from prompt tokens and padding.
Separate prefill, decode, model loading and host delivery time.

The current one-shot firmware ABI has no per-token streaming or device token
timestamps. Keep those device metrics unavailable until real events/counters
exist; simulator throughput cannot stand in for hardware throughput. Preserve
request/source hashes and measured host durations now. Add token timestamps and
memory/compute/stall counters after the correctness baseline without delaying
initial closure.

Optimize measured memory latency/concurrency and compute idle cycles before
raising frequency through fresh timing/CDC/DRC gates. Preserve the model and
exact numerical outputs. Queued-fetch operator gains are hypotheses for
integration, not measured full-model speedups. Paid expansion still requires a
concrete allocation; this plan does not change safeguards.
