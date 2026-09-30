# ERG-103 first-slice implementation and validation report

Status: **In progress; Verilator and integrated Arcilator simulation passed; custom FPGA execution remains pending.**
As of 2026-09-30 17:55 UTC, EC2 reports the designated F2 instance stopped with
`Client.InstanceInitiatedShutdown`. The last retrieved routing status was active
at 17:40 UTC. Final routed artifacts must be inspected after coordinated recovery;
a stale SSM InProgress status is not proof of completion. Custom FPGA validation
remains incomplete. This report covers the first executable weight-store/DOT128/mailbox
slice and does not close ERG-103.

Issue: [Week 1: Start core RTL and tooling work](https://linear.app/ergodex-ai/issue/ERG-103/week-1-start-core-rtl-and-tooling-work).
Draft PR: [#2 — sealed DDR weight-store and DOT128](https://github.com/Ergodex-Core/bonsai_on_chip/pull/2).
Locally validated source revision: `3ee0f99eb7216fe41ba55e719584c662313d3b10`.
Validated F2-target XSIM source: `839e5fc1729c36903843ccc03ea6742bd4a265f9`.
Each run is bound to its recorded source hashes. Later commits include harness
fixes as well as reports; the local results below are not validation of those
new harness changes or physical FPGA execution.
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
2026-09-27, 20:20:36–20:21:51 UTC (75-second timestamp window; 74.610 seconds
summed across subprocesses). See the [workflow record](evidence/simulator/validation.json),
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
| Source-commit GitHub CI | CodeQL summary and C/C++, Python, Ruby analyses pass; optional [code]smith skipped |

Native job latency was **138–157 simulated core cycles** per 128-lane dot,
56,695 cycles summed across the 396 jobs. This excludes image loading, host
register setup and host scaling. The complete native bus simulation used
99,098,552 cycles, 3,786,831 accepted DDR writes and 7,574,061 DDR reads, including
two complete image readbacks. These are simulated counts, not on-board timing.
No measured FPGA resource use, achieved clock, bandwidth or tokens/s exists yet.

The frozen fixture JSON SHA256 is
`1fa4d0b6a9a8ea1baf43e9aad5fb60cbba407dcb6f33adc69d8a613d41f9b066`.
The built local transport SHA256 is
`2d6f01b0ef3a81322f6475f4271d306138fac1d795dae602f77f9d75263d1ed1`.
Build manifests include every compiled RTL/C++ input hash. Generated fixture,
image and readback binaries are reproduced by the command and not published.

The [SDK mock report](evidence/sdk-mock/report.json), [real SDK link log](evidence/sdk-static-link.log),
[required checks](evidence/required-checks.log), [Bazel results](evidence/bazel-tests.log)
and [automated review](evidence/review.json) are separate evidence classes.
The workflow used the existing Python environment containing `gguf==0.19.0`;
an initial attempt with system Python failed preparation because GGUF was
missing, before RTL execution. That failure is retained in
[initial environment evidence](evidence/initial-environment-attempt/validation.json).

CodeQL alert 12 identified unsigned-width arithmetic in the simulator's read
buffer reservation. Commit `3ee0f99e` adds `<cstddef>` and evaluates the reserve
expression in `std::size_t`; the existing bounds still limit it to 1–4,096 bytes.
Independent review found no numerical or interface change. The entire workflow
was then rebuilt and rerun: native execution took 29.017 seconds and synthetic
execution 0.095 seconds. All 44 validation source hashes match that run's
`3ee0f99e` source snapshot; the later F2 harness changes are outside that run.
The [CodeQL snapshot](evidence/ci-source3ee.json) records successful checks for
this exact source commit as observed on 2026-09-27 at 20:26:33 UTC.

The [previous successful workflow](evidence/prior-simulator-run2/validation.json),
its command reports and build manifests remain in a separate evidence directory.
They describe the earlier `edaba6235c06e6c3ae447b7479b2123e69369321` source;
the original [published report](https://github.com/Ergodex-Core/bonsai_on_chip/blob/f5425b4d70d4a2b01796f52b88d01db2753fb235/reports/ERG-103/README.md)
also remains available. Earlier successes and failures are not relabeled as the
new run.

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

F2 harness revision `c05d541599fc568f1b03b48b7abe582c5c57f1d7` adds the test
Makefile and required Small Shell place-and-route XDC, and fixes Vivado version
banner casing checks. The locally validated compute/store/bridge RTL is unchanged
from `3ee0f99e`. All four transferred inputs passed SHA256 checks and preparation passed on F2.
The [first attempt](evidence/f2/xsim-attempt-01/result.json) ran at
15:44:54-15:44:58 UTC and failed before custom RTL compilation: the pinned
AWS IP helper could not discover its Git root from the external CL directory.
The [retained console log](evidence/f2/xsim-attempt-01/console.log) records exit 2.
A separate management-channel failure obscured command status until recovery;
rebooting the dedicated instance restored access without losing EBS evidence.

Commit `c491c4e4` binds Git discovery to the pinned HDK for the simulation
invocation, without changing vendor files. A local reproduction against the
actual pinned helper confirms that correction; it is not an RTL test. The fresh
remote rerun began at 15:55:20 UTC and completed vendor IP compilation, then
failed at 15:56:47 UTC because the custom Makefile omitted the shell C/DPI
support inputs. Its [result and source binding](evidence/f2/xsim-attempt-02/result.json)
and [full log](evidence/f2/xsim-attempt-02/console.log) retain that failed attempt.

Commit `3d849975` adds the pinned shell DPI inputs and stages a minimal XSIM
run script. The [third attempt](evidence/f2/xsim-attempt-03/result.json) compiled
and elaborated, then failed the DDR-ready check before any DOT job. Commit
`839e5fc1` adds the pinned shell's DDR statistics initialization pulse and the
required calibration wait to the testbench. Compute/store/bridge RTL is unchanged.

The [fourth fresh attempt passed](evidence/f2/xsim-attempt-04/result.json) at
16:09:47–16:12:26 UTC on 2026-09-30, with **87 assertions**, three actual Q1_0
cases and one separate synthetic ternary case. All 4,160 compact-fixture bytes
matched readback. Signed dot results were **899, 938, 713 and -999**; raw FP16
scale bits were **0x26f0, 0x2710, 0x28a0 and 0x3c00** and matched exactly.
The [complete test log](evidence/f2/xsim-attempt-04/test_first_slice.log),
[source manifest](evidence/f2/xsim-attempt-04/source-manifest.json), runner hash,
exit code and unchanged HDK pin are retained. The test ended at 64.224 simulated
microseconds; this is not Arcilator speed, board throughput or model latency.
XSIM warns that two vendor-IP assertions are ignored and FIFO models omit
synchronization delays. The 87 explicit custom checks passed; this functional
smoke is not CDC verification or proof that every vendor assertion was evaluated.
Coverage is three real blocks from two tensors plus one valid synthetic case.
This smoke does not test post-seal write rejection, a second full readback,
reset/fault recovery, or exact line-read counts; those remain separate local
regressions and physical-suite gates. The synthetic scale 0x3c00 is its defined
unit-scale convention; the three native scales are read from the payload.

The [physical host transport build](evidence/f2/transport-build-01/result.json)
also passed on the F2 Linux host against the pinned SDK, without opening the
FPGA. Its binary SHA256 is
`2164ca15edd84b8f7ce059ea805a9d69b35650d9d05500ad67a59e4b2f16038f`.

Synthesis/routed build started at 16:14:48 UTC on the same prepared source.
Final timing, utilization and DRC are pending. The
[AWS shell checkpoint](evidence/f2/shell-checkpoint.json) was independently
downloaded and checksum-verified; it is not our custom DCP.

The prepared XSIM fixture contains three actual native cases from the same
fixture inventory, explicitly relocated to exercise aligned, line-crossing
and page-crossing reads, plus a separate synthetic ternary case. Its compact
DDR image is distinct from the full canonical image. XSIM must check every
compact image byte and compare dot/scale results; it cannot establish that all
242 MB were loaded. The physical native suite must use the full canonical image
and all 396 actual-weight commands through the same host runtime.

| Gate | Status |
| --- | --- |
| F2 execution preparation | PASS: authorized instance running; source/model hashes and fresh preparation verified |
| AWS shell/DDR XSIM | PASS: 87 assertions, three actual Q1_0 cases, one synthetic case, complete 4,160-byte readback; three prior failures retained |
| Custom DCP synthesis, routed timing/resources/DRC | In progress; started 2026-09-30 16:14:48 UTC; final reports pending |
| Linux physical host transport build | PASS against pinned SDK; compile/link evidence only |
| Custom AFI/AGFI creation and load | Pending; slot 0 cleared, no AFI loaded |
| Physical full-image load, seal, DOT128 and counters | Pending custom build/image; not run |
| Arcilator execution of this new slice | Not run; Verilator is the current local backend |
| Reviewed and merged integration PR | Pending |

The existing Week 0 vendor adder AGFI cannot execute this design. No vendor
smoke result, mocked SDK test or structural wrapper elaboration counts as this
slice's FPGA result. Physical `resp:0` means SDK call completion because PCIe
mmap does not expose AXI response codes; persistent RTL errors and independent
readback provide the hardware evidence.

The earlier authorization/authentication block is cleared: the user explicitly
authorized F2 execution and AWS login succeeded on 2026-09-30. The designated
F2.6xlarge is running with healthy instance checks and SSM online; slot 0 has been
cleared and has no AFI loaded. This is host/slot readiness, not accelerator
validation. Remote source and fixture staging passed. Three failed attempts and the
subsequent custom XSIM pass are recorded above.

Accountable owner: Rachit Tibrewal; execution owner: Codex. Next: complete the
DCP build, review timing and DRC, create/load the
new AFI, run the canonical and synthetic suites after separate coordinated
reloads, and attach the actual results. No hardware fallback has been substituted.
Keep the issue In Progress until these gates and review/merge are complete.

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

## HBM ROM-equivalent and ternary-array design

The [weight-store architecture](../../doc/microarch/weightstore.md) now prefers
HBM for the later FPGA backend. Host loading, full-image verification, draining
admitted writes, and sealing every writer must precede inference. Backend reset
invalidates the loaded image and outstanding epochs; HBM is volatile and requires
reload/verify/reseal. The current implementation still uses DDR.

The [ternary-array integration design](../../doc/microarch/ternary-systolic-array.md)
and [independent HBM review](../../doc/microarch/hbm-rom-equivalent-review.md)
cover the common logical interface, candidate HBM channel mapping, reset and
write-gate coverage, and outstanding-read requirements. These are design-only
deliverables, not HBM or array implementation/simulation claims. Full CoralNPU
Arcilator execution and its cycles/second remain unmeasured; the earlier
[Arcilator pilot](../ERG-102/arcilator-pilot.md) covers a small controller only.


## Arcilator first-slice execution and EC2 replay — 2026-09-30

The [Arcilator evidence index](evidence/arcilator/local-emulated/index.json)
records an actual integrated first-slice run: **396 native cases, six synthetic
cases (two expected rejections), three mailbox tests and four protocol tests
passed**. The full 242,357,184-byte image was loaded and both full readback hashes
matched the canonical image. Every native and synthetic case object, image hash
and cycle/DDR counter matches the historical Verilator report, with the backend
label identified separately. The initial frontend declaration-order failure is
retained; moving the declaration before its assignments fixes portability without
changing logic.

The native run took 456.572 seconds for 99,098,552 simulated cycles, about
0.217 million cycles per workflow second, **inside emulated Linux/amd64 on an
arm64 Mac**. This includes host load/readback, hashing and commands. It is not a
native EC2 measurement or a speed comparison against the earlier native-Mac
Verilator result (29.017 seconds, about 3.42 million workflow cycles/second).
Neither simulator rate is the modeled 250 MHz XSIM clock or the 250 MHz FPGA
implementation target. FPGA timing closure remains unverified.

The full run is bound to its included exact tested-source snapshots and initial
build manifest. The final reusable [one-command workflow](../../tests/first_slice/README.md#arcilator-integrated-first-slice-workflow)
is committed in `00dd6b41b488aabcf580b3bb53acd37829e09b3f`; later helper/provenance
changes passed short compile checks, and the shared runner's 22 failure tests
passed. Do not relabel historical runs as a complete final-source replay.

This follows Wispr-on-chip's explicit compiler stages and retained failure logs,
with public-port metadata and settled runtime evaluation. The final runner records
source/tool/binary hashes and requires complete suite reports. No internal state
or clock-history patching is used. Standalone DOT/store/mailbox-fault/bridge unit
benches, full CoralNPU and full-model inference were not run under this Arcilator
workflow.

Per the requested direction, native EC2 Arcilator and Verilator comparison is
prepared. The external toolchain was transferred privately, verified on F2's
native x86 CPU, and its prerequisites passed. The instance then shut down from
inside the guest before the simulator replay started. No EC2 simulator result,
custom AFI, physical operation, HBM execution or generated token is claimed.
Resume choice and guest shutdown/build evidence inspection remain open.

The [five-slide meeting deck](../project/decks/Bonsai-on-chip-progress-2026-09-30.pptx)
and [blank weekly template](../project/decks/Bonsai-on-chip-weekly-template.pptx)
are editable and preserved. The deck is a **17:40 UTC snapshot**, preceding the
shutdown; it is also attached to ERG-103. Newer status is recorded in this report.
The [27B candidate design note](../../doc/microarch/bonsai-27b-candidate.md)
identifies native formats, the Hadamard transform, hybrid-attention helpers and
conditional memory limits without selecting or claiming to run that model.
