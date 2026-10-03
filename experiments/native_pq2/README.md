# Native PQ2 engine variants

This is a bounded comparison of E1 serial32, E1 with queued weight fetches, and
spatial-L parallel reductions. It preserves native `{-1,0,+1,+2}` codes, raw scale
bits, four INT32 subgroup results, and the existing FPGA-resident Coral FP32
scaling order. It does not replace the model, quantization or full-core firmware.

See [measured results](../../reports/ERG-104/ternary-variants/README.md),
[the numerical/interface contract](docs/contract.md),
[original-source and license review](docs/source-review.txt), and
[full-core/HBM integration gates](docs/full_core_integration.md).

The common benchmark lives on `codex/ternary-e1-benchmark`. Implementations are
separate branches: `codex/ternary-e1-fetch` and `codex/ternary-spatial-l`.
Fetch depth defaults to 4 and supports 1..32. Spatial lanes default to 8 and support
1/2/4/8/16/32. Spatial-L is a parallel reduction tile, not a systolic array. The
architecture deck's E2 name remains reserved for its proposed full 32×DOT128 design.

On a coordinated EC2 Linux worker, use Python 3.12.3, GCC 13.3 and Verilator 5.020
for the exact recorded toolchain. Run from a clean checkout and separate worktrees:

```bash
git fetch origin
git checkout codex/ternary-e1-benchmark
git worktree add ../fetch origin/codex/ternary-e1-fetch
git worktree add ../spatial origin/codex/ternary-spatial-l
export BENCHMARK_EC2_AUTHORIZED=1
experiments/native_pq2/tools/reproduce_matrix.sh \
  "$MODEL_GGUF" \
  ../fetch/experiments/native_pq2/fetch/coral_weight_axi.sv \
  ../spatial/experiments/native_pq2/spatial/coral_weight_axi.sv \
  "$EVIDENCE_OUTPUT"
```

Run inside the budget owner's CPU/memory/time limits. The script does not launch
instances or enforce the enclosing cgroup. The model must match the report's
463,290,464-byte SHA256 pin; the runner validates tensor types and boundaries and
records the hash. No checkpoint is distributed with this branch. The complete
model and synthetic fixtures stay private; published CSV contains counters only.

Expected results are PASS for every selected variant/profile, nonzero integer
comparison counts and a JSON/CSV source manifest. A missing tool, skipped CPU
read-overlap check on historical E1, or unavailable board gate is not a pass.
`run_benchmark.py --help` exposes memory latency, response II, queue capacity and
request stalls. These are explicit service scenarios, not measured F2 HBM values.

The historical `tests/test_baseline.cpp` is retained for diagnostic
reproduction only. Current measurements use `native_reference.py` and
`tests/test_engine.cpp`, which sample the same K block across native tensor rows.
`inspect_model.py` independently checks all native PQ2 tensors and their code
histogram. The optional `tools/synth_leaf.tcl` is an unexecuted out-of-context
recipe; specify the actual device part and clock constraint before use.
