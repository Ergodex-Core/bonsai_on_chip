# Native PQ2 engine comparison — 2026-10-03

Queued fetch is the first integration candidate. Under the modeled 80-cycle read
latency, depth 8 reduced one native DOT128 tile's engine busy time from 7,904 to
1,139 cycles (6.94×). Including synthetic AXI configuration, polling and readback,
the same test went from 8,880 to 2,117 cycles (4.19×). These are operator simulation
results, not measured firmware or model-token speedups.

## Published draft PRs

| Branch | Draft PR | Scope |
| --- | --- | --- |
| `codex/ternary-e1-benchmark` | [#6](https://github.com/Ergodex-Core/bonsai_on_chip/pull/6) | Common oracle, harness and evidence; base main |
| `codex/ternary-e1-fetch` | [#7](https://github.com/Ergodex-Core/bonsai_on_chip/pull/7) | Queued fetch and read-only AXI adapter; base benchmark |
| `codex/ternary-spatial-l` | [#8](https://github.com/Ergodex-Core/bonsai_on_chip/pull/8) | Parameterized spatial reductions; base benchmark |

## Scope and correctness

The matrix compares E1 serial32, E1-fetch depths 1/4/8, and spatial-L widths
1/2/4/8/16/32. The latter is an activation-sharing parallel reduction tile, not a
systolic array. E2 remains reserved for the architecture deck's full 32×DOT128
proposal. No combined fetch/spatial implementation or sparse variant is claimed.
The baseline retains E1's numerical implementation and public interface. Its
source was reformatted for the final run. Provenance records the original imported
RTL SHA256 separately from the formatted RTL SHA256 used by Verilator.

The two fixture manifests use the same seed 7193, identical
synthetic cases and different native weight tensors. Across 100 variant/profile
runs, all 22,400 cases passed 1,826,800 exact INT32 subgroup comparisons:

| Native source | Native coverage per run | Cases/run | Runs | Subgroup checks |
| --- | --- | ---: | ---: | ---: |
| `blk.0.attn_q.weight` | 32 rows × 16 K blocks, width 2048 | 208 | 50 | 811,000 |
| `blk.0.ffn_down.weight` | 32 rows × 48 K blocks, width 6144 | 240 | 50 | 1,015,800 |

Native bytes retain their original row stride and scales. Each native case evaluates one
K block across the same 32 rows. Activations and FP32 scale bits are seeded test
operands; these cases do not assemble FP32 row results or execute model layers.
Synthetic coverage includes every two-bit code, INT8 extremes, all 16 byte
alignments, units 1/7/21/31/32, memory faults, reset and request/response stalls.
The independent oracle preserves `code - 1`, including +2, four INT32 subgroups,
and raw FP16/FP32 scale bits. Ordered FPGA-resident Coral FP32 epilogue execution
is still a separate integration gate.

Optimized variants pass a CPU-ROM/MMIO read-overlap regression. Historical E1 skips
that optional check because of its inherited response-ID arbitration hazard. The
current single-outstanding Coral wrapper masks it; baseline PASS is not a claim
that the skipped scenario passed. The optimized engines also retain stalled
requests through faults and drain accepted requests before clearing busy.

The read-only storage adapter passed depths 1/4/7: 3,000 accepted requests,
2,991 checked responses and nine explicitly canceled by paired reset. Tests cover
injected RRESP and missing-RLAST errors, backpressure, concurrent handshakes and
64-bit HBM address carry. Reset must flush both adapter and AXI peer.

## Matched cycle results

Representative Q `model_000`: batch 1, 32 outputs, one native 128-element block.
Every entry uses the same weights, operands, precision and simulator toolchain.

| Variant | Ideal busy | Lat80 busy | Lat80 AXI command | Capacity-2 busy |
| --- | ---: | ---: | ---: | ---: |
| E1 | 320 | 7,904 | 8,880 | 7,904 |
| E1-fetch-D1 | 415 | 7,999 | 8,981 | 7,999 |
| E1-fetch-D4 | 225 | 2,107 | 3,082 | 4,024 |
| E1-fetch-D8 | 225 | 1,139 | 2,117 | 4,024 |
| spatial-L1 | 320 | 7,904 | 8,880 | 7,904 |
| spatial-L2 | 256 | 7,840 | 8,822 | 7,840 |
| spatial-L4 | 224 | 7,808 | 8,788 | 7,808 |
| spatial-L8 | 208 | 7,792 | 8,777 | 7,792 |
| spatial-L16 | 200 | 7,784 | 8,773 | 7,784 |
| spatial-L32 | 196 | 7,780 | 8,739 | 7,780 |

All times above are simulated target cycles. AXI command includes the testbench's
configuration, dispatch/poll and readback sequence; it is not compiled firmware
overhead. Steady-state command initiation interval was not measured. Spatial
compute body takes 128/L cycles, but serialized memory dominates modeled high-latency
scenarios. At capacity 2, D4 and D8 both peak at two requests and perform equally;
more descriptors cannot exceed the backend's capacity. D1 adds control overhead
and is not the recommended deployment setting. Default D4 is conservative; D8
is a candidate to validate against the actual shell and resource budget.

| Profile | Read latency | Response beat II | Capacity | Request stall probability |
| --- | ---: | ---: | ---: | ---: |
| ideal | 1 | 1 | 8 | 0% |
| lat20 | 20 | 1 | 8 | 0% |
| lat80 | 80 | 4 | 8 | 0% |
| limited | 80 | 8 | 2 | 0% |
| stress | 20 | 4 | 2 | 25% |

Latency jitter is zero and seeded MMIO stalls range up to three cycles. These
are controlled memory service scenarios, not calibrated F2 HBM measurements.
No clock-frequency scaling is applied. The optional synthesis recipe defaults
to a 4 ns target constraint; it has not run and is not achieved timing.

Each representative tile performs 4,096 scalar products and fetches 96 128-bit
beats (1,536 bytes) for 1,088 useful native bytes: 70.83% payload efficiency.
Queued fetch hides latency; it does not reduce this traffic. Batch-1 arithmetic
slot occupancy over engine busy time, derived as 4,096 / (32 × lanes × busy
cycles), is 1.62% for E1, 11.24% for fetch-D8 and 0.0514% for spatial-L32 in
lat80. Spatial-L32 reaches only 2.04% in ideal memory. This is a derived
operator utilization metric, excluding CPU overhead, not measured FPGA
utilization. The 68-beat bound
requires an aligned, contiguous tile and is not achieved for these row-strided
native cases. The 1,088-byte staging array remains the original register-backed
storage; no BRAM inference or area saving is established.

In the final Q suite, measured simulator throughput spans 182,899–3,788,860
simulated cycles/s; summed simulation wall time is 41.139 seconds. The Q matrix
ran from 19:25:53 to 19:27:59 UTC, including builds. Held-out down ran from
19:30:24 to 19:32:39 UTC, with 186,179–3,850,510 simulated cycles/s and 50.121
seconds summed simulation wall time. Its first attempt was stopped by the
original 19:30 guard and excluded; the completed run used the owner-approved
extension through 19:45 within the same $1 reservation. Each benchmark JSON records
simulation wall time and simulated cycles/s separately
from target-cycle counters. These measure simulator execution speed on the
allocated CPU cores, not FPGA clock rate or tokens/s. Both suites use the same
two CPU cores and at most two build jobs on the existing worker. The final runs
were rebuilt after formatting; earlier run timings are not reused.

## Sources, licensing and compression decision

Original [TerEffic v2](https://arxiv.org/html/2502.16473v2) and
[ternaryLLM](https://github.com/fpgasystems/ternaryLLM/tree/68d384425261d60bfbde5ff079c33a474c1eb28e)
were read before design selection. Shared activation negation, reduction tiling
and bounded buffering informed independent implementations. No upstream source
code or paper throughput is copied. The paper is CC BY 4.0; the inspected root
repository license is MIT, with separately marked GPU files excluded. Detailed
scope and compatibility review is in
[the source review](../../../experiments/native_pq2/docs/source-review.txt).

The pinned full checkpoint has 1,719,904,256 PQ2 weights across 197 tensors, with
counts [-1, 0, +1, +2] = [531,316,607; 658,033,822; 530,553,827; 0]. Zero density is
38.2599%. A 128-bit bitmap plus two bits per retained native code needs greater
than 50% zeros before metadata merely to beat the original 256 code bits. This
whole-model statistic does not justify adding a uniform sparse format. Selective
compression needs a separate per-block study. Upstream ternary index/codebook
formats cannot silently remap native +2; synthetic +2 support stays mandatory.

## Reproduction and evidence

Use [the clean-checkout recipe](../../../experiments/native_pq2/README.md) on a
coordinated Linux worker. Toolchain: Verilator 5.020, GCC 13.3.0, Python 3.12.3;
strict Verilator warnings with explicit loop-unroll limits. No Mac simulation or
new instance was used. The owner reserved at most $1 from the single shared $300
cap for this existing-host CPU slot. Actual billing remains owner-reconciled.
Stock/ROM full-token jobs retained their priority and separate cores.

- [Q run, fixture and source metadata](q/benchmark.json), [counter CSV](q/cases.csv.gz)
  and [artifact integrity manifest](q/artifact-manifest.json).
- [Held-out down metadata](down/benchmark.json), [counter CSV](down/cases.csv.gz)
  and [artifact integrity manifest](down/artifact-manifest.json).
- [Full model histogram](q/model-histogram.json), freshly recomputed with the
  formatted inspector, with metadata and counts only.
- [Final validation receipt](final/final-result.json),
  [executable/build-log hashes](final/build-provenance.json), and
  [allocation/service receipt](final/service-receipt.json).
- [Publication source provenance](../../../experiments/native_pq2/source-manifest.json)
  includes the original imported and formatted E1 hashes, tested variant revisions, noL0 emitter/core
  pins and adapter source/test hashes and normalized build/run commands.
- [Adapter depth 1](q/adapter-result-1.json), [depth 4](q/adapter-result-4.json),
  [depth 7](q/adapter-result-7.json); [macro check](q/macro-check.log) passed.

Checkpoint: `Ternary-Bonsai-1.7B-PQ2_0.gguf`, 463,290,464 bytes, SHA256
`de68ba48a8dacb21979915991e7741b917869d71410a370df951c0c3a237ae50`.
No checkpoint, activation payload, credentials, private cloud IDs or raw remote
control logs are included. Q and down fixture SHA256 values are respectively
`3c1ab6c67ce99033203d2d9bf7594400a613621433962ba8a90dd1e454bdf0c9` and
`f81e327265ade9950207da61f137d7c66bcd2f96da314bb0d866b5d2f3a36737`.

## Remaining gates

Required repository formatter checks now pass on the formatted source snapshot:
clang-format, Verible, YAPF, Markdownlint and ShellCheck. Macro parity also passes.
The initial missing-tool and formatting failures were corrected before this final
rerun. [Formatter receipt](format/repository-linters.json),
[diagnostics](format/repository-linters.log), and
[before/after provenance](format/format-provenance.json) identify the changes.
All measured sources were rebuilt and both tensor matrices rerun after formatting.
All 22,400 per-case counter rows also match the original pre-format runs exactly.
The final publication Markdown is checked separately after report assembly;
its exact file hashes and result are in [the text check receipt](final/publication-text-check.json).

LUT/FF/BRAM/URAM/DSP utilization, synthesized and post-route timing, actual HBM
bandwidth, compiled firmware overhead, full ordered FP32 inference, complete model
tokens and board execution are unmeasured. Do not promote this result as FPGA fit
or full-model acceleration. The approved read-only HBM contract requires verified
load/hash, sealed model writes during inference and separate mutable state; ASIC
physical ROM remains a distinct implementation.

The next gate is integration with the FPGA owner's noL0 Coral full-core flow:
bind the read-only queued AXI adapter, run the native firmware operator oracle,
then preserve exact ordered FP32 layer/token results before synthesis/board
promotion. Host involvement stays model loading and checking only. The common
full-token baseline and stock/ROM runs must finish without interruption. The
[full-core integration checklist](../../../experiments/native_pq2/docs/full_core_integration.md)
records interfaces and the distinct publication base `26bded5b`, historical remote
simulator base `81036e73`, and historical local first-slice base `c04bdb4c`.

Tracking: [ERG-104](https://linear.app/ergodex-ai/issue/ERG-104/week-2-validate-ternary-engine-and-fpga-fit),
[ERG-108](https://linear.app/ergodex-ai/issue/ERG-108/week-6-complete-scheduling-sign-off-and-evaluation),
[ERG-109](https://linear.app/ergodex-ai/issue/ERG-109/define-coral-based-full-chip-architecture). These gates remain open.
