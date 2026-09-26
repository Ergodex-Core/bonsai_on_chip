# Delivery plan review — ERG-102 through ERG-108

Reviewed 2026-09-26 against the Linear week 0–6 descriptions and the initial
repository/simulator findings. This is a delivery recommendation, not approval
of the schedule or evidence that the implementation is complete.

## Main changes

The weekly issues now require an integration PR, reproducible simulator results,
separately identified FPGA simulation and hardware results, and a committed and
attached report. The PR template carries the same evidence requirements. A
missing result keeps its gate pending or blocked; an installed tool or passing
AWS example cannot close a Bonsai integration gate.

Start with [ERG-102](https://linear.app/ergodex-ai/issue/ERG-102/week-0-finalize-interface-spec-and-fpga-setup).
Its environment and specification work is in [PR #1](https://github.com/Ergodex-Core/bonsai_on_chip/pull/1).
Freeze the interface and reference baseline before dependent integration work;
independent prototypes may proceed against explicitly versioned assumptions.

## Recommended sequence

| Issue | Concrete integration deliverable | Decision needed before acceptance |
| --- | --- | --- |
| ERG-102 / Week 0 | Interface specification, runnable smoke harness, official F2 shell/MMIO bring-up and evidence report | Model/fixture hashes, numerical format and tolerances, memory/register map, performance budget, owners/reviewers; define 2x/4x and eight-tile |
| ERG-103 / Week 1 | One actual-weight ternary operation through image tooling, driver and mailbox | Independent arithmetic comparison; identify every stub and the CPU/testbench execution boundary |
| ERG-104 / Week 2 | Verified engine plus resource and bandwidth budget | Supported shapes, partial tiles and stalls; shell-inclusive fit decision and fallback configuration |
| ERG-105 / Week 3 | Helper coverage matrix and integrated GDN/full-attention layer workloads | One owner and focused test per helper; multi-token state/KV checks; define attention work remaining for Week 5 |
| ERG-106 / Week 4 | Reproducible named FPGA configurations and routed implementation results | Actual meaning of 2x/4x, achieved clocks, constraints/exceptions, hardware correctness and fit |
| ERG-107 / Week 5 | Known-good full-model inference candidate and declared eight-tile mapping | Concurrent versus time-multiplexed tiles, chosen platform, measured bandwidth, no undisclosed host fallback |
| ERG-108 / Week 6 | Release/sign-off PR for the exact integrated candidate | Validate scheduling/clock changes before freeze; rerun affected tests; approve exact RTL, runtime, images and bitstream |

The dates in Linear are unchanged. Week 0 was due 2026-09-25. At the initial
review its interface and hardware gates were open; the final
[Week 0 specification](../../doc/spec/week0.md) and [report](README.md) supersede
that initial status and define the v0 performance assumptions. Only Weeks 0 and 1 currently have
an assignee. Treat later dates as targets until engineering owners, reviewer
capacity and the measured build/test cycle support them. No new dependencies or
reviewer assignments were imposed by this review.

## Reduce the schedule risks

1. **Separate the two infrastructure tracks.** The repository-default baseline
   runs Verilator/Chisel/host tests. Arcilator currently has a four-configuration
   parity pilot, not a full repository backend. Track backend integration as
   explicit work with preserved original test semantics, so changing simulators
   does not silently reduce regression coverage.
2. **Move numerical decisions before helper integration.** Pin the checkpoint,
   weight packing, accumulation/rounding rules, state/KV precision and reference
   tolerances in Week 0–2. Week 6 precision experiments should confirm the chosen
   baseline; a changed numerical format reopens affected validation.
3. **Start fit and timing feedback early.** Use Week 2 resource/bandwidth evidence
   to choose a credible configuration and platform. Carry a working baseline
   clock through integration. Clock increases and overlap scheduling need their
   own comparisons before the final release freeze.
4. **Break Week 3 into reviewable implementation PRs.** FWHT, epilogues, macro-op,
   top-k, gdn.step, decode-attention and Zvt fixes each need an owner, independent
   oracle and focused tests. Keep one integration PR with a coverage matrix and
   stateful layer tests as the weekly completion gate.
5. **Make performance comparisons reproducible.** Record model, shape, batch,
   context length, physical tile count/mapping, clocks and memory assumptions
   with every cycle/latency/throughput result. Freeze quantitative targets with
   the owner before evaluating them; none are invented in this review.
6. **Keep sign-off tied to an immutable candidate.** Reference commit, fixture,
   firmware and bitstream identifiers in the PR and report. Retain raw failures,
   skips and unsupported cases alongside passes. A later code, precision or
   clock change invalidates only the affected evidence, which must be rerun.

## First Arcilator integration candidate

After the default baseline is classified, a bounded next PR can support the
unchanged cocotb target
`//tests/cocotb/tlul:secded_encoder_32_cocotb_test_test_secded_encoder`.
Its existing test checks 1,000 random inputs against an independent SECDED model,
using public ports and clock/edge triggers. This is a proposed boundary, not an
implemented backend or a guarantee that the frontend accepts the generated RTL.

Acceptance should include the original Python test running through a real
cocotb/GPI adapter with correct time, edge and delta-cycle behavior; identical
seed/configuration under Verilator and Arcilator; all 1,000 comparisons actually
executed; and normal Bazel logs, exit status and XML results. Preserve the test
and oracle. Expanding to SRAM/DPI, internal hierarchy, ChiselSim and SystemC/UVM
requires separate demonstrated capabilities.

The [Arcilator readiness report](arcilator-suite-readiness.md) captures two
existing SystemVerilog benches that fail before simulation, including a dropped
randomization/coverage diagnostic. Do not suppress these diagnostics and call the
original regression equivalent.

## Current evidence boundary

The [environment report](README.md) is the authoritative current result summary.
The AWS example now has separate vendor simulation and actual loaded-AFI
MMIO evidence. Bonsai under the F2 shell and full-model execution remain
implementation gates for later milestones. The independent Week 0 review and
merge record determine ERG-102 acceptance; this planning review alone does not
approve or close any weekly issue.
