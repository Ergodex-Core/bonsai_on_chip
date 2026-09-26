# ERG-102 Week 0 interface and FPGA environment baseline

Run date: 2026-09-26. Issue owner: Rachit Tibrewal.
Issue: [ERG-102](https://linear.app/ergodex-ai/issue/ERG-102/week-0-finalize-interface-spec-and-fpga-setup).
Integration PR: [#1](https://github.com/Ergodex-Core/bonsai_on_chip/pull/1).
Development repository: [bonsai_on_chip](https://github.com/Ergodex-Core/bonsai_on_chip).

Week 0 delivers the initial interface/fixture contract, runnable simulator
baseline and actual AWS F2 shell/MMIO environment bring-up. Technical acceptance
and the reviewed source revision are recorded in the
[independent automated review](independent-review.md). PR merge and the final
report attachment are recorded in Linear; this report alone is not merge proof.

Architecture, implementation and FPGA execution were performed by Codex in this
task, with a separate Codex agent reviewing the source and evidence. This is
independent automated technical review, not a human or GitHub APPROVED review.
Permanent engineering staffing for subsequent milestones remains unassigned.

## Scope and results

| Evidence | Result | Execution boundary |
| --- | --- | --- |
| Week 0 specification | Versioned v0 contract | Existing instructions/control, memory map, fixture, numerics, cycle budgets and performance assumptions |
| Weight-store image | PASS: 310 tensors; 242,357,184 bytes | Native Q1_0/FP32 bytes, range hashes, padding and tied-head alias; no weight-store RTL claim |
| New packer Bazel target | PASS: 1 target, 6 unit cases | Separate addition to the earlier repository baseline |
| Repository-default baseline | PASS: 1,332 selected targets | 1,046 fresh and 286 cached; Verilator, Chisel and host backends |
| Instruction-cycle baseline | PASS: 45 workloads | RvvCoreMiniAxi RTL counters, 32 repetitions each; preserved cached result |
| Core cocotb smoke | PASS with explicit coverage limits | 26 exercised cases plus 3 RVV early returns |
| Arcilator / Verilator parity pilot | PASS: 4 configurations | One unchanged handshake-control module; 41,716 samples and 208,580 output checks per backend |
| Pilot evidence-integrity checks | PASS: 15 checks | Stale inputs, incomplete runs and altered traces rejected |
| Full Arcilator testbench frontend probes | UNSUPPORTED: 2/2 | Aligner and MultiFifo fail before simulation; no full-suite pass |
| AWS shell XSIM tests | PASS: 9/9, no failures/skips | Host CPU, vendor shell bus-functional model and AXI-Lite example |
| Physical AWS shell/MMIO replay | PASS: 2,264 additions; 2,144 independent assertions | Actual loaded official F2 FPGA example, slot 0 |
| Bonsai F2 wrapper / weight-store RTL / full-model FPGA inference | FOLLOW-ON, not implemented or validated here | No FPGA model tokens, DDR/HBM weight-store result or eight-engine fit claim |

The [v0 specification](../../doc/spec/week0.md) and
[weight-store architecture](../../doc/microarch/weightstore.md) reserve a
read-only, non-executable weight aperture and define a common tagged line-read
interface for ASIC ROM, simulation and FPGA DDR/HBM. On FPGA the future loader
must close and drain every write path, verify readback, seal the image and expose
READY before execution. These are specified interfaces awaiting RTL implementation.
The [image packer](../../utils/weightstore/README.md) already produces and verifies
the canonical packed image. Its [result](weightstore-image-prototype.json) and
[raw unit-test log](evidence/weightstore-test.log.txt) identify the tested sources.

The initial checkpoint is `prism-ml/Bonsai-1.7B-gguf` revision
`210a9e99f79cb184909d49595906526eb2b3dd9a`, file `Bonsai-1.7B-Q1_0.gguf`,
SHA-256 `3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3`.
The native image SHA-256 is
`ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99`.
This binary sign-quantized fixture is distinct from the later 27B ternary target.

The v0 engineering assumption defines 2x/4x as 100/200 MHz targets against the
existing 50 MHz Nexus baseline, with the same engine/workload assumptions.
These are proposed clock targets, not measured F2 speedups. They may be revised
explicitly before Week 4. Eight-tile means eight physical engines only if the
shell-inclusive resource, routing, timing and bandwidth budgets support them;
logical time-multiplexed partitions must disclose the actual engine count.
The complete [45 cycle measurements](measured-instruction-cycles.json),
[original table](measured-instruction-cycles-table.txt) and
[retrieval provenance](isa-cycle-baseline-provenance.json) establish the initial
unchanged-configuration non-regression budget.

## Physical FPGA evidence

The final run used Amazon's public `cl_axil_reg_access` image:
AGFI `agfi-06447dea0ca9b0a39`, regional AFI `afi-04454b01e29b26073`,
Small Shell `0x10212415`, PCI application IDs `1d0f:f006`, on an F2.6xlarge
in `us-east-1`, slot 0. Its mapping is published in the pinned AWS SDK notebook.
The source/example documents `clk_main_a0` at 250 MHz; this is the documented
clock contract, not a measured clock frequency or a Bonsai timing result.

The [physical result and provenance](f2-physical.json) bind the exact harness,
SDK, binaries and raw command logs to the final replay. The
[portable preparation and validation instructions](../../fpga/aws_f2/physical/README.md)
build the SDK and runtime examples from a pinned clean checkout, with no global
SDK installation. Preparation exports committed upstream files to a fresh work
directory; validation verifies the prepared hashes before touching the slot.

The final run passed 15 command phases, all exit code zero. Upstream sum, carry,
random and post-reset tests performed 2,002 additions. The independent probe
performed 262 additions and 2,128 assertions; initial and post-reload reset
probes added 8 assertions each. Checks cover register/operand readback, carry,
bounded completion and acknowledgement, read-only register write rejection,
invalid-address sentinel behavior and AFI clear/reload recovery.

Read-only writes are ignored and preserve the values. Invalid addresses return
`0xDEADBEEF`; this does not establish AXI SLVERR/DECERR handling. Reset is FPGA
reconfiguration, not a model soft-reset command. The final shell metrics report
zero for the reported timeout/protocol/range error flags and counters. The
instance was left running with this public image loaded and the advisory lock
released; instance power state is not a persistent report guarantee.

This is a prebuilt vendor AFI. Amazon's available metadata does not expose its
exact bitstream build source commit, DCP hash, utilization or post-route timing
reports. Those reports are unavailable, not passing. The pinned host runtime
and source mapping establish what was exercised; no locally reproduced FPGA
bitstream build, Bonsai hardware integration or model execution is claimed.

## Reproduce from the checkout

Use the final PR revision and the prerequisites pinned in each linked guide.
The original repository revision was
`5eff3822250fc52b2a0f83315969238805ece7c5`. The filename-only rename from
`Sram.scala` to `SramBlock.scala` resolves the fresh macOS clone collision with
`SRAM.scala`; original source contents and class names remain unchanged.
The baseline runner retains its actual source overlay and hashes, so earlier
baseline results are not represented as tests of later added implementation.

For the existing regression suite, use Linux x86_64, Bazel 8.6.0 and the
repository CI build prerequisites:

```bash
SIM_BUILD_JOBS=16 SIM_TEST_JOBS=8 utils/run_simulation_baseline.sh all
bazel test //utils/weightstore:test_pack_image --test_output=errors
```

The runner also provides `inventory` and `smoke` modes. The smoke target is
`//tests/cocotb:core_mini_axi_sim_cocotb`; full mode runs parallel and exclusive
groups separately with `--build_tests_only --keep_going`. Preserve fresh output
directories: missing results or incomplete artifact collection fail evidence
validation. The full run took 39m26s on the recorded F2 host configuration;
that is host wall time, not simulated accelerator latency.

For image packing, use Python 3.11 or later and `gguf==0.19.0`, with the verified
checkpoint outside the repository and a new output directory:

```bash
python utils/weightstore/pack_image.py --gguf "$MODEL" --out "$IMAGE_OUT"
```

The [packer guide](../../utils/weightstore/README.md) documents fixture checks,
expected output and the immutable publication behavior. Two independent full
packs agreed byte-for-byte and in image/manifest hashes. Model payload files
are not committed to Git.

For the bounded Arcilator pilot, use the exact commands, reference threshold,
external toolchain/runtime hashes and expected counts in the
[pilot instructions](../../tests/arcilator/README.md). Its pass threshold is
exact binary output equality and byte-identical complete traces; it does not
establish four-state/X behavior or full-suite backend compatibility.

On an authorized AWS F2 host with Vivado 2025.2 and an available slot:

```bash
F2_WORK_ROOT=/absolute/new/f2-work
bash fpga/aws_f2/setup.sh "$F2_WORK_ROOT"
bash fpga/aws_f2/validate.sh "$F2_WORK_ROOT"
PHYSICAL_WORK=/absolute/new/physical-work
bash fpga/aws_f2/physical/prepare.sh "$F2_WORK_ROOT/aws-fpga" "$PHYSICAL_WORK"
sudo bash "$PHYSICAL_WORK/harness/validate.sh" "$PHYSICAL_WORK"
cat "$PHYSICAL_WORK/physical-result.txt"
```

Expected XSIM result: nine fresh test passes and zero failures/skips. Expected
physical result: `PHYSICAL_PASS`, all command exit codes zero and the declared
probe counts. Root privileges permit BAR access and load/clear operations.
Reserve the host/slot before running; the script's advisory lock coordinates
only cooperating callers. An unexpected loaded image aborts the run.

The [AWS guide](../../fpga/aws_f2/README.md) pins AWS source and IP submodules and
lists individual simulation tests. The [physical guide](../../fpga/aws_f2/physical/README.md)
describes the fresh build, wrappers, hashes, assertions and expected final slot
state. Successful physical execution demonstrates access and usable allocated
F2 capacity; it is not a promise of future regional quota or availability.

## Raw evidence and coverage limits

The [native report](native-baseline.md) and [JSON](native-baseline.json) preserve
all 1,332 selected target results, complete build-event streams and the hashes
of 6,652 copied artifact records. The original inventory contains 2,758 rules;
selection excludes the union of 1,426 manual/VCS targets, plus synthesis/power/
spyglass tags where present. Thirty-five manual Scala definitions are exercised
through their selected shell wrappers. This inventory predates the new packer
target, which was tested separately.

DDR pin checks are disabled in the baseline. Some CSR-only scenarios skip
backdoor segment-loading or host writes; the [core smoke report](smoke-baseline.md)
identifies the three RVV early returns. Unsupported or skipped checks are never
counted as exercised passes. The [Arcilator pilot](arcilator-pilot.md) and
[readiness findings](arcilator-suite-readiness.md) retain the frontend failures
and dropped constrained-random/coverage limitation. Full Arcilator support is
follow-on work preserving the original test semantics.

The [XSIM evidence](f2-xsim.json) records the successful final source-matched
simulation run. Full native and XSIM archives are retained on persistent EBS
with hashes and locations in those reports. Public physical raw command logs,
ISA cycle JSON/table and packer unit-test log/XML are committed with this report.
Private cloud account/instance inventory, process listings and transfer URLs
are excluded from public files. No private archive S3 upload is claimed.
Earlier interrupted attempts are retained separately and are not passing runs.

Required repository formatting, macro-signature and lockfile checks are run for
the final change. The [independent review](independent-review.md) records their
result, the exact candidate and source/evidence checks; GitHub records final CI
and merge status. Seven synthetic runner-evidence checks and the 15 pilot
integrity checks cover incomplete/stale evidence rather than broadening RTL
coverage.

## Follow-on development

The next implementation must add the weight-store RTL and bus adapter, all-path
loader/write protection, reset/fault handling, and DDR/HBM backend integration,
then prove equivalence against the same packed bytes and common read contract.
Bonsai shell integration, arithmetic kernels, full-model tokens and performance
measurements require their own simulator/hardware evidence and reviewed PRs.
The [CPU model result and deferred diagnostic](model-inference-readiness.md)
remain separate; no FPGA model token has been generated. The
[delivery-plan review](delivery-plan-review.md) records the recommended sequence.

GitHub autolinks map numeric `ERG-` references to Linear. The project and weekly
issues link the repository; automatic GitHub-App PR/status synchronization
remains unverified. Issue closure is updated explicitly after merge and final
report attachment.
