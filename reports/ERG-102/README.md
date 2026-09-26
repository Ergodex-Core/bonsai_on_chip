# ERG-102 simulation and AWS F2 environment baseline

Status: **in progress; environment PR, not Week 0 sign-off**.
Run date: 2026-09-26. Owner: Rachit Tibrewal. Technical acceptance: pending.
Issue: [ERG-102](https://linear.app/ergodex-ai/issue/ERG-102/week-0-finalize-interface-spec-and-fpga-setup).
Development repository: [bonsai_on_chip](https://github.com/Ergodex-Core/bonsai_on_chip).
Branch: `codex/erg-102-simulation-setup`. PR: pending publication.

## Scope and results

| Evidence | Result | Coverage |
| --- | --- | --- |
| Arcilator / Verilator parity pilot | PASS: 4 configurations, 0 failures, 0 skips | One unchanged handshake-control RTL module; 41,716 samples and 208,580 output checks per backend |
| Pilot evidence-integrity checks | PASS: 13 checks | Stale inputs, incomplete runs and altered traces rejected |
| Repository-default test baseline | Running | Verilator, Chisel and host backends; not an Arcilator suite result |
| AWS HDK preparation | PASS | Pinned source/IP with Vivado 2025.2; Git LFS validation passed |
| AWS AXI-Lite example XSIM tests | Initial run PASS: 9/9 | AWS shell bus-functional model and example; final script validation pending |
| Bonsai under an AWS F2 shell wrapper | NOT RUN | Wrapper and simulator integration remain to be implemented |
| Synthesis, routed timing and loaded AFI | NOT RUN | No area, timing or physical FPGA inference claim |

The pilot's [detailed report](arcilator-pilot.md) and
[machine-readable result](arcilator-pilot.json) contain per-configuration counts,
source/tool hashes, tool versions, independent oracle, seed and limitations.
Its reference threshold is exact equality for every binary public output and
byte-identical full traces. No performance or four-state/X result is claimed.

## Source and reproduction

The original repository revision is
`5eff3822250fc52b2a0f83315969238805ece7c5`. This change preserves both original
Scala source contents while renaming `Sram.scala` to `SramBlock.scala` and updating
its BUILD entry. The original `SRAM.scala` remains unchanged. This removes a
case-insensitive filesystem collision in a fresh macOS clone; it changes no RTL
or class names.

The repository runner records the tested revision, dirty-source patch, source
hashes, exact Bazel commands, raw build events, exit codes, logs and copied test
outputs. Results apply to those recorded bytes; report-only edits do not change
the tested RTL. The native baseline uses the original revision plus the filename
fix and runner, identified by the captured overlay/source hashes.

Run in Linux x86_64 with the repository's Bazel 8.6.0 and CI build prerequisites:

```bash
SIM_BUILD_JOBS=8 SIM_TEST_JOBS=2 utils/run_simulation_baseline.sh all
```

The runner has separate `inventory` and `smoke` modes. Its smoke target is
`//tests/cocotb:core_mini_axi_sim_cocotb`. The full mode runs parallel and exclusive
groups separately with `--build_tests_only --keep_going`. Preserve each fresh
output directory under `reports/ERG-102/raw/`; missing results and incomplete
artifact collection fail the evidence check.

For the separate Arcilator pilot, follow the exact commands and external
toolchain hashes in [the pilot instructions](../../tests/arcilator/README.md).
The compiler/runtime package is an external prerequisite, not bundled in this
public repository. The pilot does not replace the existing regression backends.

For AWS simulation, activate Vivado 2025.2 on the FPGA Developer AMI and run:

```bash
F2_WORK_ROOT=/absolute/dedicated/work-directory
bash fpga/aws_f2/setup.sh "$F2_WORK_ROOT"
bash fpga/aws_f2/validate.sh "$F2_WORK_ROOT"
```

The [AWS instructions](../../fpga/aws_f2/README.md) pin the HDK and both IP
submodules, explain prerequisites and list all nine individual tests. Pass
requires successful process/log capture plus fresh `TEST PASSED` and zero-error
markers. This uses XSIM on the instance CPU. No AFI was created or loaded.

## Declared test inventory

The initial Bazel query expands to **2,758 test targets**, not individual
cocotb/Scala testcases. CI-compatible selection includes **1,332 candidates**:
1,325 parallel and 7 exclusive. Candidate counts are not passing-test counts.

The unique excluded union is 1,426 targets: 1,307 tagged `vcs` and 191 tagged
`manual`, with 72 in both groups. The filter also excludes `synthesis`, `power`
and `spyglass` where present. Thirty-five manual Scala test definitions are
wrapped by 35 selected shell-test targets, so they are not simply omitted.
The selected rule types are 797 Verilator UVM, 476 cocotb, 36 shell, 10 Python,
8 C++ and 5 simulator tests. Raw inventory and build events determine actual
execution; unsupported, unbuilt, filtered and skipped targets do not pass.

A full Arcilator port needs real callback/clock scheduling for cocotb, SRAM DPI
loading and internal hierarchy access, a ChiselSim backend, and an explicit
strategy for SystemC/UVM. The bounded pilot does not establish those features.

## Artifacts and validation

Readable report attachment and durable private raw-artifact links: pending.
Cloud account/instance inventory and private transfer URLs are excluded from
public reports. Authorized reviewers receive environment details and artifact
locations through the Linear attachment. Raw results are gitignored.

Repository linters and macro-signature checks pass. Seven synthetic baseline
evidence checks pass, covering complete data, missing outputs/result records,
missing terminal events, unreceived announced events, malformed JSON and logging
failure. Final AWS script validation is being collected. The pilot was rebuilt after required
formatting changes, and its report contains the final driver/runner hashes.

## Next acceptance gates

1. Complete and classify the repository baseline, retaining every failure and
   unsupported target. Define the first supported Arcilator integration boundary
   before expanding its coverage.
2. Freeze the model/fixture, precision and reference tolerances, interface/memory
   map, baseline cycle/bandwidth targets, and meanings of 2x/4x and eight-tile.
3. Implement the Bonsai F2 Custom Logic wrapper and exercise command/status,
   reset, error and completion behavior against the AWS shell model.
4. Build and load a named AFI, then retain actual MMIO/readback evidence and
   implementation reports. Simulation is a separate prerequisite.
5. Assign architect/RTL/FPGA reviewers, attach the final report and record their
   acceptance of the exact revision before closing ERG-102.

ERG-102 is the first issue because later milestones depend on its interfaces,
reference behavior and reproducible simulator/FPGA environment. Week 6 should
begin from an integrated Week 5 candidate: scheduling/clock changes must be
validated before release freeze, and precision changes reopen affected checks.
