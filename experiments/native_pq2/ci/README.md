# Native engine simulator CI

This separate workflow compares the exact published baseline (PR #6), queued
fetch variant (PR #7), and spatial lane variant (PR #8) with both simulators.
It does not edit the simulator smoke workflow owned by PR #5.

Each backend executes 10 configurations, five identical memory profiles and
five trials using the unchanged public C++ protocol harness and 192 synthetic
native PQ2 cases. Every trial checks 14,172 integer subgroups, including +2.
The comparison rejects missing cases, source drift, changed fixture hashes,
reduced checks, nondeterministic cycle counters, and differences between
backends. The baseline's documented CPU overlap limitation remains explicit.

Tool pins: Verilator 5.052; CIRCT firtool 1.161.0; runtime generator commit
0d63c41c9121106b01372aca2b60eb44ed4a6e87; Clang 18. Source archives and runtime
files are checksum verified. Both jobs use standard public ubuntu-24.04 hosted
runners, two compile processes and identical compiler optimization settings.
Verilator make variables `OPT_FAST`, `OPT_SLOW` and `OPT_GLOBAL` are explicitly
set to `-O2`, including the unchanged C++ driver. Environment records include
CPU model, CPU affinity, memory size and compiler version for wall-time review.
No EC2 instances or paid runner service are provisioned.

`comparison.json` separates operator cycle changes from five-trial simulator
wall-time statistics. Hosted runner variability means wall-time results are
observations rather than hardware speed claims. These are operator/protocol
measurements, not firmware, full-token, synthesis, timing, or board results.
Arcilator compiles the full engine; unsupported frontend behavior fails rather
than replacing the workload with an arithmetic smoke test.

Before/after refers to the pinned E1 baseline and the candidate engines in the
same CI run. Run artifacts include source manifests, fixtures, logs and partial
failure evidence. No model checkpoint or private cloud data is included.
Each command reports its start, completion and elapsed time in the job log;
individual commands have a 15-minute timeout and retain failure diagnostics.
The complete repeated matrix has a 330-minute job limit, below the six-hour
hosted maximum. In run 37159561136 each Arcilator variant spent about 10.5
minutes building (`opt -O2` about 4.7 minutes, `llc -O2` about 5.7 minutes)
plus 8 to 16 minutes on its 25 trials; the first eight variants took 177
minutes, so all ten need roughly 225 minutes. This accommodates all trials
without weakening coverage.

The stock CIRCT 1.161.0 frontend leaves a synchronous LLHD process containing
static loops. The default Arc path fails at coroutine switch legalization;
keeping arrays as aggregates reaches an unsupported delta-time operation.
Failed artifacts remain in runs 37154630724, 37155220000 and 37156040181.

The current compatibility pass adapts the pinned upstream LLHD loop unroller
to visit processes. It retains all original loop-bound matching and rejects
loops containing `llhd.wait` or `llhd.halt`, preserving the outer event loop.
It runs before the official Deseq and structural LLHD passes. Allocated
Arc state then uses the official LLVM pipeline's array-bufferization pass and the bundled
`mlir-translate --mlir-to-llvmir` exporter; early-stop Arc output is MLIR. Remaining
processes or signal/event operations fail before simulation. The original RTL,
clock, reset, fixtures and C++ oracle remain unchanged; full cycle parity is
required. This compiler adaptation is source-built on Linux CI against the
checksum-verified official native shared development package, not a private
binary. Compiler source and installer hashes are part of the evidence.

Aggregate-array lowering first passed all 25 E1 trials and 18 depth-one trials
in run 37157905401, before that redundant run was cancelled for the current
revision. The original coroutine switch is eliminated by structural lowering,
so the current pipeline restores upstream `arc-lower-arrays` instead of keeping
the slower aggregate workaround. It must pass the same complete gate.

Upstream [UnrollLoops.cpp](https://github.com/llvm/circt/blob/0d63c41c9121106b01372aca2b60eb44ed4a6e87/lib/Dialect/LLHD/Transforms/UnrollLoops.cpp)
is Apache 2.0 with the LLVM exception, preserved in `LLVM-LICENSE.txt` and the
source notice. [The stock LLHD pipeline](https://github.com/llvm/circt/blob/0d63c41c9121106b01372aca2b60eb44ed4a6e87/lib/Conversion/ImportVerilog/ImportVerilog.cpp)
already runs Deseq before its combinational-only loop unroller, motivating
this bounded process-loop adaptation. Successful lowering is still subject
to the complete CI gate; no timing operation is discarded to bypass a failure.
