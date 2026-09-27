# ERG-103 first-slice implementation and validation report

Status: **In progress; physical FPGA gate blocked pending authorization to
restart the designated F2 instance.** This report covers the first executable
weight-store/DOT128/mailbox slice. It does not close ERG-103.

Issue: [Week 1: Start core RTL and tooling work](https://linear.app/ergodex-ai/issue/ERG-103/week-1-start-core-rtl-and-tooling-work).
Draft PR: [#2 — sealed DDR weight-store and DOT128](https://github.com/Ergodex-Core/bonsai_on_chip/pull/2).
Validated implementation revision: `edaba6235c06e6c3ae447b7479b2123e69369321`.
Subsequent report commits change evidence only; source hashes bind each run.
Development repository: [bonsai_on_chip](https://github.com/Ergodex-Core/bonsai_on_chip).
Implementation owner: Rachit Tibrewal (Linear assignee); implementation and
execution: Codex. Separate Codex agents reviewed RTL, host runtime and F2
integration. This is automated technical review, not human/GitHub approval.

## Scope and result boundary

The implemented path loads DDR through a guarded AXI bridge, verifies the
whole image, seals writes, reads native weights through a tagged/epoch store,
and completes a mailbox-issued DOT128. The engine multiplies 128 signed int8
inputs by binary native Q1_0 signs or separate synthetic {-1,0,+1} operations,
accumulating exactly in int32. It returns unchanged FP16 scale bits. A single
FP32-rounded scale multiply runs on the host and is labeled accordingly.

Local Verilator simulation uses the same four RTL modules as the custom AWS
wrapper, with a byte-addressed AXI DDR model, independent channel stalls and
1–11-cycle response delay (seed 103). The CPU boundary is a testbench/host bus
master. No CoralNPU CPU/firmware, RVV execution, HBM, full layer/model inference
or generated tokens are claimed. Sealed FPGA DDR is the ROM-equivalent weight
source; these model bytes are not synthesized as on-chip ROM.

## Acceptance and reproducibility

The fixed numeric oracle independently decodes native image bytes and requires
exact int32 dots and exact raw FP16 scales; the disclosed host FP32 product is
compared bit-for-bit. Each successful job must report its submitted cookie,
stable completion and exactly one or two line reads according to its address.
Both invalid ternary encodings must reject with zero outputs. The full native
image digest must match after load/readback and again after command execution.
Sealed writes must increment the hardware rejection counter without changing
memory. Backend errors or incomplete evidence fail the run.

See the [one-command simulator instructions](../../tests/first_slice/README.md)
and [mailbox ABI](../../doc/spec/first_slice.md). From a clean checkout:

```bash
python3 tests/first_slice/validate.py \
  --gguf /absolute/Bonsai-1.7B-Q1_0.gguf \
  --out /absolute/fresh/erg103-validation --jobs 4
```

Prerequisites are Python 3.11+, `gguf==0.19.0`, Verilator, a C++17 compiler and
Make. The measured local tools are Python 3.14.5, Verilator 5.048 and Apple
clang 17.0.0 on arm64 macOS. Reserve at least 2 GiB for generated images,
readbacks and build outputs. No cloud resource is required for this command.
The expected final result is PASS with 396 native and six directed synthetic
cases; this is a simulator result, never physical-FPGA evidence.

## Model and fixture identity

The model is Bonsai-1.7B-Q1_0 from the pinned Prism release. Q1_0 remains binary;
the separate two-bit synthetic test format does not change the native model.
The canonical image includes all 310 tensor records, with 197 Q1_0 matrices.
The native suite selects the first/last block from every Q1_0 matrix plus a
64-byte-line and a 4-KiB-page crossing case. Inputs are seeded signed int8 with
explicit -128, 127, -1 and zero lanes. Model files and generated derived vectors
retain the fixture generator's Apache/Prism/Qwen3 attribution and are not
committed into this report.

| Artifact | SHA256 or revision |
| --- | --- |
| Hugging Face revision | `210a9e99f79cb184909d49595906526eb2b3dd9a` |
| GGUF checkpoint | `3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3` |
| Canonical image, 242,357,184 bytes | `ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99` |
| Canonical tensor manifest | `21c664d0c029dc7f12440ad93a7fa6688c2b9e78b2143f6e9c179956793a7391` |
| Separate 192-byte ternary image | `966088f726e00ad969b7c7bc5ec64e9a2eee1e6734660f47fb2e222fb121ae36` |

## Final local evidence

The complete workflow **passed** from the frozen implementation revision on
2026-09-27, 20:07:32–20:08:46 UTC. See the [workflow record](evidence/simulator/validation.json),
[native per-command results](evidence/simulator/native/report.json),
[synthetic results](evidence/simulator/synthetic/report.json), and
[durable evidence index](evidence/index.json). All 16 workflow steps passed.

| Check | Actual result |
| --- | --- |
| Canonical native DOT128 suite | 396/396 pass; 398 engine line reads; exact dot and raw scale |
| Synthetic ternary suite | 6/6 pass, including two expected invalid-code rejections |
| Full image before/after execution | Both 242,357,184-byte readbacks match the canonical SHA256 |
| DOT unit regression | 431 completed commands; four additional reset cases |
| Store protocol regression | Seven scenarios, including 16 credits, fairness, first-fault retention and late drain |
| DDR/PCIS bridge regression | 189 assertions, including unaligned host access, bounds and sticky DDR errors |
| Integrated mailbox | Three public-bus lifecycle tests |
| In-flight backend failure | Four fault scenarios, four successful baselines and four reset recoveries |
| Simulator transport error handling | Four test methods / six fresh-process negative scenarios |
| Fixture / host runner unit tests | 13 / 22 pass; both new Bazel targets also pass |
| Physical transport software tests | 41/41 mock-SDK checks pass under Python `-O`; no hardware access |
| Real public SDK static link | Pass against the pinned SDK with `-Wall -Wextra -Werror` |
| Required repository checks | Linters, macro signatures and unchanged Bzlmod lockfile pass |

Native job latency was **138–157 simulated core cycles** per 128-lane dot,
56,695 cycles summed across the 396 jobs. This excludes image loading, host
register setup and host scaling. The complete native bus simulation used
99,098,552 cycles, 3,786,831 accepted DDR writes and 7,574,061 DDR reads, including
two complete image readbacks. These are simulated counts, not on-board timing.
No measured FPGA resource use, achieved clock, bandwidth or tokens/s exists yet.

The frozen fixture JSON SHA256 is
`1fa4d0b6a9a8ea1baf43e9aad5fb60cbba407dcb6f33adc69d8a613d41f9b066`.
The built local transport SHA256 is
`ca96477b333a433508c6c95a1f91461dd2d0f87d6ecc9dce5930ed6416a54bc1`.
Build manifests include every compiled RTL/C++ input hash. Generated fixture,
image and readback binaries are reproduced by the command and not published.

The [SDK mock report](evidence/sdk-mock/report.json), [real SDK link log](evidence/sdk-static-link.log),
[required checks](evidence/required-checks.log), [Bazel results](evidence/bazel-tests.log)
and [automated review](evidence/review.json) are separate evidence classes.
The workflow used the existing Python environment containing `gguf==0.19.0`;
an initial attempt with system Python failed preparation because GGUF was
missing, before RTL execution. That failure is retained in
[initial environment evidence](evidence/initial-environment-attempt/validation.json).

Published logs/JSON replace local path prefixes with placeholders. Numeric
results, source/model/binary digests and exit codes are unchanged. The evidence
index records both original and published file digests; digest references inside
original records refer to the unsanitized originals. No credentials, private AWS
inventory, or model payload binaries are included.

## FPGA-target simulation and physical gate

The [custom F2 workflow](../../fpga/aws_f2/first_slice/README.md) pins AWS HDK
`b603a81f65666e0cf7a67ee5cf18b148eb6b08c3`, Vivado 2025.2 and the Small Shell
interface. OCL/BAR0 exposes controls; PCIS/BAR4 is the sole DDR loader/readback
path. The wrapper uses `sh_ddr`; HBM and other write masters are disabled. PCI
identity is `1d0f:f010`, subsystem `1d0f:0103`. The shell clock target is 250 MHz;
this is a target, not measured timing closure or throughput.

The prepared XSIM fixture contains three actual native cases from the same
fixture inventory, explicitly relocated to exercise aligned, line-crossing
and page-crossing reads, plus a separate synthetic ternary case. Its compact
DDR image is distinct from the full canonical image. XSIM must check every
compact image byte and compare dot/scale results; it cannot establish that all
242 MB were loaded. The physical native suite must use the full canonical image
and all 396 actual-weight commands through the same host runtime.

| Gate | Status |
| --- | --- |
| AWS shell/DDR XSIM | Not run |
| Custom DCP synthesis, routed timing/resources/DRC | Not run |
| Custom AFI/AGFI creation and load | Not run |
| Physical full-image load, seal, DOT128 and counters | Blocked; F2 stopped |
| Arcilator execution of this new slice | Not run; Verilator is the current local backend |
| Reviewed and merged integration PR | Pending |

The existing Week 0 vendor adder AGFI cannot execute this design. No vendor
smoke result, mocked SDK test or structural wrapper elaboration counts as this
slice's FPGA result. Physical `resp:0` means SDK call completion because PCIe
mmap does not expose AXI response codes; persistent RTL errors and independent
readback provide the hardware evidence.

Blocker owner: Rachit Tibrewal for billable instance restart authorization;
Codex for the subsequent execution. Automatic approval review rejected starting
the stopped F2 without explicit confirmation, and the instance remains stopped.
After approval: run fresh preflight, pinned XSIM, custom DCP build, review timing
and DRC, create/load the new AFI, run the canonical and synthetic suites after
separate coordinated reloads, and attach actual results. No hardware fallback
has been substituted. Keep the issue In Progress until these gates and review/
merge are complete.

## Review and remaining scope

Review identified and corrected persistent DDR error reporting, mailbox
completion after an in-flight backend failure, fair store scheduling, strict
source/fixture binding, partial-failure evidence retention and simulator file
finalization. Regression evidence and the independent review record accompany
the final local results.

The [deferred helper acceptance table](../../doc/spec/first_slice.md#remaining-integration-work)
lists CoralNPU/RVV wiring, native operator numerics, scale/dequantization,
attention/KV-cache and scheduling cases still required in Week 3. Current cycle
counts describe serial DOT128 and simulated memory; they are not model tokens/s,
FPGA bandwidth or ASIC power/area estimates.
