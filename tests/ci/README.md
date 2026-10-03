# Bounded PR simulator checks

`Simulator smoke and parity` runs on PR creation, pushes to the PR, reopening,
ready-for-review and base-branch edits. Title/description-only edits are ignored
without cancelling active tests. Drafts also run. Each independent
Ubuntu 24.04 job has a 25-minute ceiling; a backend failure does not cancel the
other backend. Superseded PR runs are cancelled. The comparison job has a
5-minute ceiling. No model download, full-model inference, AWS or FPGA access
occurs. Existing broader CoralNPU workflows retain their existing schedules.

The common gate compiles **unchanged production RTL** for the 8- and 32-bit
adder, 32-bit compressor, and 32-bit barrel shifter. One C++ driver checks seven
outputs against independent integer operations and records every sample.
It covers all 131,072 8-bit input/carry combinations, 16,384 boundary cases
(including overflow, sign extension, shift 0/31 and all four modes), and 4,096
fixed-seed random cases. Both jobs must pass all 151,552 samples and 1,060,864
output checks, then the comparison job must find identical source/revision
identities and complete byte-identical traces. A running/incomplete manifest,
changed source, changed trace or failed oracle exits nonzero.

Verilator additionally runs the established four-configuration
[handshake oracle](../arcilator/README.md), including a new directed reset/flush
under backpressure and repeated evaluation without a clock edge regression.
The public CIRCT release rejects that module's asynchronous `seq.firreg` reset;
therefore **the public Arcilator job tests arithmetic RTL only**. It uses public
`model.eval()` for combinational evaluation, with the compiler's `--sroa`
array destructuring and `--no-runtime --no-generate-driver` options. It does not
replace or weaken the existing private-toolchain handshake parity runner.

Toolchain pins are in `install_tools.sh`: Verilator 5.052 (matching the Bazel
pin), public CIRCT `firtool-1.161.0`, and runtime/header sources from CIRCT
revision `0d63c41c9121106b01372aca2b60eb44ed4a6e87`. All external source/package
bytes are SHA256 checked. Clang 18 and Python come from Ubuntu 24.04; Jinja2
3.1.6 and MarkupSafe 3.0.3 are pinned. Host compiler/Python versions and exact
tool binary hashes are retained. This pins simulator artifacts, not the entire
rolling GitHub host image. Caches contain installations only; DUT models and
results are rebuilt on every PR. CIRCT cold download is about 1.1 GB; only four
binaries and two runtime files remain cached.

The independent five-minute UVM launcher regression runs 19 focused tests. The
legacy UVM launcher now queries Bazel for the Verilator executable and resolves
its complete runtime from that same repository, covering canonical Bzlmod names
and rejecting missing, ambiguous or incomplete outputs. Its test log is retained
as a separate artifact. The existing broader UVM workflow validates integration.

## Reproduce on Linux x86-64

Install the host dependencies listed in the workflow, then from the root:

```bash
python3 -m venv /tmp/simulator-python
/tmp/simulator-python/bin/pip install Jinja2==3.1.6 MarkupSafe==3.0.3
export PATH=/tmp/simulator-python/bin:$PATH
bash tests/ci/install_tools.sh verilator /tmp/bonsai-ci-tools
bash tests/ci/install_tools.sh arcilator /tmp/bonsai-ci-tools
bash tests/ci/run_smoke.sh verilator "$PWD" /tmp/fresh-evidence/verilator /tmp/bonsai-ci-tools
bash tests/ci/run_smoke.sh arcilator "$PWD" /tmp/fresh-evidence/arcilator /tmp/bonsai-ci-tools
python3 tests/ci/evidence.py compare both "$PWD" /tmp/fresh-evidence /tmp/bonsai-ci-tools
```

Evidence directories must be new. `exit-code.txt`, backend manifests, test and
compiler logs, CSV traces and generated MLIR are downloadable as separate
14-day Actions artifacts, including partial evidence on failure. Comparison
writes `results.json`. Artifact names include run ID and attempt. Job logs and
summaries distinguish a failed test from an install failure or a test not run.

## Runner, security and activation

The repository was verified public and used standard hosted GitHub runners.
These standard public-repository jobs use GitHub's free runner allowance; no
paid service is created or configured. Blacksmith's supplied MCP page required
sign-in, no callable Blacksmith tool was present, and repository runner inventory
provided no verified Blacksmith labels. Consequently this workflow uses
`ubuntu-24.04`. A later move to Blacksmith needs verified configured labels,
fork isolation and billing authorization. The $300 F2/EC2 cap is separate.

The workflow uses `pull_request`, read-only contents permission, immutable
Actions references and checkout without persisted credentials. It consumes no
secrets or deploy keys and does not execute PR code in `pull_request_target`.
GitHub may require maintainer approval for external contributor runs. Caches
are scoped by GitHub's PR cache isolation and tool pin hash, with no broad
restore keys. No branch protection changes are made. New default-branch workflow
manual dispatch requires merging; PR checks should run on this draft PR when
GitHub evaluates its merge ref and Actions are enabled.

## Longer and blocked coverage

[ERG-102](https://linear.app/ergodex-ai/issue/ERG-102/week-0-finalize-interface-spec-and-fpga-setup) is the existing handshake
parity/interface evidence mapping. This CI change does not close its FPGA
acceptance gates. The existing private Arcilator handshake reproduction remains
in `tests/arcilator/README.md`. ERG-103 first-slice regressions live on separate
ongoing implementation branches and are not present on this PR's base main.
Full-vector Arcilator currently encounters the RVV `f_cout2cin` carry-array
compiler cycle blocker; the six-cell full-inference comparison is separate work.
No full-core, full-repository, model token, performance or hardware pass is
implied. Run those long jobs explicitly under the simulation owner's coordinated
compute ledger and retain matched-source/fixture/tool evidence.
