# ERG-103 HBM ROM-contract candidate

Status: **CPU RTL validation and independent automated review passed; physical
HBM acceptance remains blocked pending coordinated execution.** This is the separate
HBM backend candidate, based on PR #2 source
`fc8ff1b5b2e67c459fe8f61bb192c8d010b1216e`. The historical DDR evidence in the
parent report does not validate this candidate. No merge or issue completion
is requested.

Published implementation: `495751c2720a8865a73b0bfbb5a5d687b1ef7d20`.
The locally validated commit `8d8600f48ae4dac0bb3411a4c77d1f6a2aedd53b`
has the identical Git tree `9f105470532e327373ff41ed67c62f39f7881c8c`;
publication used the authorized GitHub connector because CLI write credentials
were not configured. All source bytes are unchanged.
All 57 files in the HBM and fresh DDR validation source maps were independently
checked against that commit; see the [source receipt](evidence/committed-source-verification.json).
The runs retain their original base-plus-working-tree provenance and exact
tested bytes. Subsequent report commits do not change the tested implementation.

Implementation owner: Codex; accountable issue owner: Rachit Tibrewal.
Independent automated reviewer: Codex `source_audit`, separate from the RTL,
attachment, runtime and simulator authors. Automated review is not human
approval. Parent coordination owns hardware allocation and issue publication.

## Implemented scope

The candidate preserves the v1 DOT128 mailbox, canonical Q1_0 bytes and logical
64-byte tagged/epoch reads. A fixed build-time mapping sends a complete line as
two 32-byte native HBM beats on pseudochannel 15. The native HBM physical address
is `(15 << 29) | local_offset`; full-width local bounds are checked before
narrowing. The 512 MiB channel allocation does not enlarge the 256 MiB v1 image
limit. There is one physical read and one write in flight, with sixteen logical
read credits in the existing frontend; this is a correctness implementation.

Only the guarded PCIS loader can write weight memory. The production attachment
omits example test engines, scrubbers, performance muxes and alternate masters;
unused native HBM ports have no write requests. There is no writable clock,
channel-map, controller-reset or HBM configuration CSR. The trusted host loads,
closes admission, drains accepted writes, reads back the complete logical image,
checks SHA-256, supplies all eight digest words and seals. This is a host digest
handshake, not a hardware SHA engine. Accepted writes drain on ordinary closure;
malformed beats and backend errors suppress the rest of a burst.

HBM is volatile. Loss of backend readiness during loading, verification or use
invalidates the image permanently until coordinated reset, complete reload,
verification and reseal. Existing source-bound responses stay stable under
backpressure. There is no core-only reset control that can reopen the loader.
The host/testbench is the CPU bus master; mutable activations and results stay
in the mailbox, outside the sealed allocation. KV-cache storage and CoralNPU
firmware remain separate integration work; no mutable KV alias exists here.

## Numerical and source provenance

Pinned source model: `prism-ml/Bonsai-1.7B-gguf` revision
`210a9e99f79cb184909d49595906526eb2b3dd9a`, `Bonsai-1.7B-Q1_0.gguf`.
Source SHA-256: `3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3`.
The image has 310 tensors, 242,357,152 payload bytes and 32 padding bytes:
242,357,184 bytes in total, SHA-256
`ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99`.
The fixture SHA-256 is
`1fa4d0b6a9a8ea1baf43e9aad5fb60cbba407dcb6f33adc69d8a613d41f9b066`.
Model payloads and generated images are outside Git.

The independent reference is the pinned GGUF payload interpreted by
`utils/first_slice/prepare_fixture.py` and rechecked against canonical bytes by
the shared runtime. Acceptance is exact int32 dot product and raw FP16 scale
bits for each unchanged 18-byte Q1_0 group, followed by one disclosed host FP32
multiply. Native cases have only ±1 weights. Separate synthetic two-bit ternary
cases cover zeros and illegal code rejection. No Q1_0 case is labeled native
PQ2; these tests do not prove a complete layer, model, firmware or generated
text.

Published PRs #1–#10 and all fetched branches were inspected before reuse.
No complete native HBM attachment was present. PR #7's native-PQ2 read queue is
provenance/reference only and is not imported into this Q1_0 backend. The
October 4 automated review of PR #2 identified burst continuation after an
error; the candidate repairs it with a regression that fails on the published
original bridge. The HBM proposal `aa69172d` remains design provenance, not
execution evidence. Unpublished local retiming or native HBM candidates have
not been assumed present or tested.

## Verification and reproduction

The [final HBM workflow](evidence/hbm/validation.json) passed **13/13 steps**.
The [fresh DDR regression](evidence/ddr/validation.json) passed **16/16 steps**.
The [strict comparison](evidence/backend-comparison.json) verified all **402 case
records**, shared source/reference identities and actual complete readback files.
Its ten corruption tests reject missing/duplicate cases, changed arithmetic,
identities, hashes, read counts and stale sources. The independent
[review](independent-review.md) binds the final files and results.

| Check | Actual result |
| --- | --- |
| Actual Q1_0 arithmetic | 396 passed, zero failures, exact int32/raw FP16/host FP32 matches |
| Separate synthetic ternary cases | 6 passed, including 2 expected illegal-code rejections |
| Whole canonical image | 242,357,184 bytes; expected, pre-seal and post-run hashes identical |
| Store lifecycle, epochs, credits, bounds, held responses | 11 scenarios passed |
| PCIS write guards, malformed bursts, draining, readiness loss | 218 checks passed; original published RTL fails new regression |
| Native HBM AXI, CDC, errors, aliases, stopped clock, late responses | 270 checks passed |
| Integrated HBM/mailbox tests | 9 methods at 250:450 ratio and 9 at reversed 2.5:1 ratio; all passed |
| Transport protocol | 4 tests passed |
| Shared fixture and runner failure tests | 13 fixture plus 35 runner tests passed |
| Mock SDK transport | 43 checks for DDR and 43 for HBM passed; no hardware access |
| Real pinned SDK | HBM transport compiled and linked; no hardware access |
| Repository changed-file formatters and macro signatures | Passed; Bazel inputs/lockfile unchanged |
| Arcilator HBM | Not run; this environment has no corresponding toolchain/two-clock harness |

The new FPGA-target memory model uses native 256-bit beats and independent
channel stalls. Effective initial-read, inter-beat and write-response delays are
6–24, 3–9 and 4–20 HBM cycles. Core/HBM periods are 18/10 abstract time units
(default 250:450 ratio); the stress configuration uses periods 4/10 and phase 1.
These are explicit test profiles, not measured vendor latency. The first
complete run passed but reported countdown ranges one cycle lower; its receipt
and differing source are retained in [initial evidence](evidence/hbm-initial/README.md).
The corrected description was rebuilt and the entire workflow rerun. An initial
parallel mock-SDK attempt collided on the mock's advisory slot lock; that failure
is retained, and both isolated mock suites pass. Neither involved an FPGA.

The model endpoint replaces native HBM PHY/calibration/thermal behavior in CPU
simulation. The testbench replaces a CoralNPU CPU. Neither is hidden as physical
execution. Source/pin/declaration checks of the concrete vendor attachment are
separate from its unexecuted functional vendor simulation. CDC simulation does
not establish metastability, implementation constraints or timing sign-off.

### Exact reproduction

Use Linux x86-64, Python 3.12 (3.11+ supported), `gguf==0.19.0`, GNU C++/Make,
and Verilator 5.048. The actual run used Python 3.12.14 and GCC 14.2.0 on an
Intel Xeon Platinum 8573C cloud CPU. Tools, download hashes and environment are
recorded in [environment](evidence/environment.json), [tool receipts](evidence/quality-tools.json)
and the validation manifest. Set `MODEL` to the pinned GGUF outside the checkout;
reserve 4 GiB for fresh images, readbacks and builds. No model download, AWS API
call, paid allocation or FPGA operation is performed by the validation command.

From a clean checkout of the validated implementation, with the tools on PATH:

```bash
python3 -m venv /tmp/hbm-venv
/tmp/hbm-venv/bin/pip install gguf==0.19.0
/tmp/hbm-venv/bin/python tests/hbm_rom/validate.py \
  --gguf "$MODEL" --out /tmp/fresh-hbm-validation --jobs 4
```

The last command performs image preparation, independent fixture generation,
RTL compilation, regressions, load/drain/full-readback/hash/seal, arithmetic
comparison, write rejection and complete final readback. It refuses an existing
output directory and caps compiler parallelism at four. Native and synthetic
suite processes each have a 1,800-second watchdog (bulk request limit 900 seconds).
Expected terminal result is `status: PASS`, `fpga_executed: false`, with exactly
the counts above; failures retain logs and cannot produce a completed report.

To reproduce the additional comparison with a freshly built DDR backend:

```bash
/tmp/hbm-venv/bin/python tests/first_slice/validate.py \
  --gguf "$MODEL" --out /tmp/fresh-ddr-validation --jobs 4
/tmp/hbm-venv/bin/python tests/hbm_rom/compare_backends.py \
  --ddr /tmp/fresh-ddr-validation --hbm /tmp/fresh-hbm-validation \
  --out /tmp/fresh-backend-comparison.json --self-test
```

Keep the generated images and readbacks for the comparator; they are deliberately
absent from public Git. [Native output records](evidence/hbm/native-report.json)
and [synthetic records](evidence/hbm/synthetic-report.json) preserve every returned
result. Model payloads, credentials and private infrastructure are not evidence
artifacts in this PR. The [evidence index](evidence/index.json) hashes the published
reports/logs, while the source receipt binds them to the implementation.

### Arithmetic and simulated cycles

| Actual case | Logical offset | int32 dot | Raw FP16 scale | Host FP32 bytes, LE | HBM command cycles |
| --- | ---: | ---: | --- | --- | ---: |
| q1_000 | 8192 | 899 | 0x26f0 | 80e6c241 | 157 |
| q1_394, crosses 64-byte line | 44582390 | 938 | 0x2710 | 0005cf41 | 187 |
| q1_395, crosses 4 KiB | 44584946 | 713 | 0x28a0 | 001ace41 | 183 |

| Native-suite counter | Fresh DDR model | HBM model |
| --- | ---: | ---: |
| Sum of 396 DOT command cycles | 56,695 | 61,273 |
| Per-command cycles, min–max | 138–157 | 148–187 |
| DOT memory-stall cycles | 4,815 | 9,393 |
| DOT logical line reads | 398 | 398 |
| Whole-workflow core cycles at final INFO | 99,098,552 | 224,475,962 |

HBM reports 3,786,831 native line writes and 7,574,061 line reads: two whole-image
readbacks, 398 DOT reads and one sealed-write probe read. Each uses exactly two
native beats: 7,573,662 write beats and 15,148,122 read beats. Final readiness is
one and fault is zero. Transport shutdown logs add 24 CSR cycles after final
INFO; those are not included in the table. These are simulated core/HBM-domain
counters, not board latency or bandwidth. The different latency models prevent
interpreting the ratio as a physical HBM/DDR speed comparison.

The final native HBM suite took 186.995 seconds of host wall time.
Other cloud CPU work could run concurrently; this is an execution receipt,
not a controlled simulator-speed benchmark. Synthesis LUT/FF/BRAM/URAM/DSP,
post-route timing, physical latency and power remain unmeasured.

## FPGA gates and bounded next action

AWS HDK pin: `b603a81f65666e0cf7a67ee5cf18b148eb6b08c3`.
IP pin: `6d32be972e6da854e61a8d3d6ec0466ab491c1b3`.
Small Shell HLX pin: `2383c2b64572c75163b1b60fbd0abea482c637e6`.
Vendor tool baseline: Vivado 2025.2 on Ubuntu 24.04 x86-64.
Core target: 250 MHz; HBM target: 450 MHz. These are configuration targets,
not timing measurements. The pinned IP disables parity and ECC correction;
no ECC protection is claimed. See the attachment README for exact configuration
and controller error/thermal observations.

| Gate | Status |
| --- | --- |
| CPU RTL HBM simulation and independent arithmetic | PASS; exact final source and full image |
| Automated source/evidence review | PASS for CPU/source scope; physical acceptance pending |
| Full LFS-backed staging / AMD HBM vendor-model XSIM | Pending; complete vendor resources/tool/license unavailable here |
| Synthesis, utilization, routed timing and CDC | Pending; no FPGA build started |
| Custom HBM DCP/AFI/AGFI and physical execution | BLOCKED; none produced |
| Review, merge and ERG-103 completion | Open |

Hardware owner: Rachit Tibrewal with the parent CI/lifecycle owner. Next action:
review this source-bound candidate and staging manifest, then allocate a bounded
vendor-XSim session before authorizing an HBM synthesis/build or AFI operation.
Any build needs a separately approved host, budget, end time and stop owner.
After implementation and timing/CDC pass, execute the same complete native and
synthetic workflows on an explicitly identified HBM image, retaining returned
outputs, hashes, counters and raw logs. Existing physical DDR work has priority.
No shared F2 instance, active HBM job, allocation, image, start/stop schedule or
Linear content was modified by this task.

## Week 3 helpers still pending

| Helper | Acceptance case |
| --- | --- |
| CoralNPU/RVV driver and map | Real simulated CPU submits, handles stale epochs/errors and matches the same oracle |
| Scale/dequantization | Native scale bits, signed extremes, zeros and rounding boundaries against a frozen reference |
| Matrix/layer operators | Full operator outputs including tails and row/group boundaries |
| Norm/activation/attention | Fixed precision and tolerances; adversarial and actual activation tensors |
| KV-cache and token scheduling | Separate mutable allocation; cache/token-step comparisons across contexts and reuse |
| Multi-client arbitration | Unique tags/epochs and progress under saturation, held responses, failures and reset |

No helper above, ASIC ROM fit, bandwidth, full-model token result or physical
performance is established by this backend change.
