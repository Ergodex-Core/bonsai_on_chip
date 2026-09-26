# ERG-102 independent automated technical review

Reviewed candidate: `f0540f4ee8a53abe3583fa0b8e750907dc5b208e`.
PR: [#1](https://github.com/Ergodex-Core/bonsai_on_chip/pull/1).
Review date: 2026-09-26. Reviewer: the separate Codex technical-review agent.

**Disposition: accepted for the documented Week 0 technical scope; no unresolved
blocking code or evidence findings.** Merge, required quality checks and the
final Linear attachment/closure are separate recorded actions. This is an
automated technical review, not a human sign-off or a GitHub APPROVED review.
The reviewer also assisted the initial Week 0 specification draft and earlier
environment investigation; this is not a claim that every document received
an external reviewer uninvolved in its preparation.

## Scope reviewed

Reviewed the complete PR change from `origin/main` through the candidate,
including the filesystem-case rename, simulator evidence collector, Arcilator
pilot/oracle, pinned AWS simulation setup, physical preparation/validation/probe,
model diagnostic, native image packer/tests, specifications and result reports.
The final candidate had a clean working tree, and `git diff --check` passed.

Week 0 freezes the existing instruction/interface/map baseline, the initial
Q1_0 packed fixture, numerical comparison rules and performance assumptions,
and demonstrates the runnable simulator and physical vendor-shell/MMIO
environment. Weight-store RTL, native arithmetic, CoralNPU F2 integration,
DDR/HBM behavior, eight-engine fit and FPGA model tokens remain follow-on work.
The 100/200 MHz configuration targets are explicitly an engineering assumption
relative to the existing 50 MHz Nexus baseline, not measured F2 speedups.

## Evidence checks

- Rechecked the real checkpoint and all 310 tensor payloads in the generated
  image against the original GGUF, including tensor hashes, alignment padding,
  aliases and final size. The 242,357,184-byte image hash is
  `ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99`.
  Packer source and the separate one-target/six-case Bazel log/XML hashes match
  the [prototype report](weightstore-image-prototype.json).
- Retrieved the existing ISA-cycle artifacts from the completed native archive,
  verified archive-manifest hashes and the target's PASSED/cached status, and
  checked all 45 values against `(raw_delta - 2) / 32`. The committed JSON hash
  is `57e734f54529f369a510ceef9f564da26dc4359287888d2fef23e23a8c87c480`.
  These are workload-specific RTL cycles, not physical timing measurements.
- Reviewed the preserved 1,332-target native result, its complete evidence
  accounting and cached/fresh distinction. The separate smoke's three RVV early
  returns, excluded/manual cases and disabled DDR pin checks remain disclosed.
  The four-configuration Arcilator pilot and nine AWS XSIM tests retain their
  actual scope; neither is presented as full Arcilator coverage or FPGA execution.
- Verified all 15 final physical phase logs against their recorded byte counts
  and hashes, all phase exit codes, and all three tested harness source hashes.
  Independently recomputed the 2,000 public carry/random sums and all 262
  deterministic probe sums/carries. The logged checks reconcile to 2,264 total
  additions and 2,144 independent assertions, including both reset probes.
  The [physical report](f2-physical.json) matches the reviewed source and traces.
- Checked report/reproduction links, frozen source boundaries and the distinction
  between the 1.7B binary-sign fixture and the later 27B ternary target. The CPU
  model diagnostic remains separate and makes no FPGA token-generation claim.

## Findings resolved before acceptance

1. Corrected IMAGE_SHA256 and seal-handshake wording to use the padded
   `weights.bin` digest, never the separate `manifest.json` digest.
2. Corrected the Linux process-name check for competing static FPGA management
   commands to match the actual 15-character `comm` truncation.
3. Added prepared source/binary/library/wrapper hash verification and invoking
   script equality before physical device operations.
4. Replaced worktree copying with `git archive` of the pinned SDK/runtime commit,
   preventing ignored or untracked build outputs from entering the fresh build.

The corrected physical harness was rebuilt and replayed on the FPGA, and its
final hashes match the candidate. The review introduced no RTL change and did
not reclassify unsupported or unexecuted tests as passes.

## Required checks and finalization

The published candidate passed the required changed-file pre-push checks:
buildifier (2 files), clang-format (3), markdownlint (17), scalafmt (1),
ShellCheck (7), and YAPF (7). `MODULE.bazel.lock` was current and macro-signature
validation passed. These are the successful final push results reported by the
executing agent, separately from this review's source/evidence checks.

GitHub CodeQL completed successfully for this candidate: C/C++, Python, Ruby
and aggregate CodeQL checks passed. The optional `[code]smith` job was skipped,
not counted as a pass. GitHub records the final head checks and merge, and the
final report is attached in Linear before closure. A subsequent commit adding review records or adjusting
evidence file modes does not change tested contents; any other source change
requires review of its affected scope before acceptance is carried forward.

The physical target is a prebuilt public AWS image. Its exact vendor bitstream
build revision, DCP, utilization and routed timing reports are unavailable and
are not inferred from the pinned host software. The final health counters are
a post-reconfiguration snapshot. No private driver transfer, model hardware
execution or public upload of private archives is part of this acceptance.

## Specification cross-review and v0 adoption

A second Codex agent independently cross-reviewed `doc/spec/week0.md` and
`doc/microarch/weightstore.md` at the same candidate. This agent did not draft
those documents; it authored the separate physical harness/evidence, which the
first reviewer independently audited. Neither review is represented as an
independent rerun of every underlying experiment.

The cross-review found no blocking inconsistency in existing control/reset
behavior, memory maps, configured widths, instruction/data routing, line-read
response semantics, sealing/reset/fault behavior, native image arithmetic,
cycle measurements or the disclosed performance assumptions. Reviewed hashes:

- `week0.md`: `f3d0603694bf8fabee86793934f18acdaa71f1a46759cf2296cf576dbd23c381`.
- `weightstore.md`: `45407d1ff256424e01d74cd2a60955097dcad0a801443afd82470fd986cdaa52`.

The executing agent adopts this reviewed contract as the Week 0 v0 baseline,
including its explicit 100/200 MHz modeling assumption and later fit gates.
The review-record commit changes only this record, the specification's status
line/link, and non-executable modes on two evidence files. It does not change
an interface contract, tested implementation, fixture or raw evidence bytes.
