# AWS F2 simulation environment

These scripts prepare and validate the AWS HDK AXI-Lite register example on an
**existing Ubuntu 24.04 x86_64 FPGA Developer AMI with Vivado 2025.2**. They do not
provision EC2, alter IAM/security groups, install system packages, create an AFI,
or load a physical FPGA. An F2 instance can run them; the simulation itself runs
on its CPU through XSIM and the AWS shell bus-functional models.

The initial environment deliverable belongs to **ERG-102**. A passing AWS example
establishes that the HDK simulator works. Bonsai integration and actual FPGA
execution remain separate acceptance gates.

## Pinned inputs

| Input | Pin |
| --- | --- |
| AWS FPGA repository | `https://github.com/aws/aws-fpga.git` |
| F2 source revision, inspected 2026-09-26 | `b603a81f65666e0cf7a67ee5cf18b148eb6b08c3` |
| Vivado | `2025.2` |
| `hdk/common/ip` submodule | `6d32be972e6da854e61a8d3d6ec0466ab491c1b3` |
| `hdk/common/shell_stable/hlx` submodule | `2383c2b64572c75163b1b60fbd0abea482c637e6` |
| Example | `hdk/cl/examples/cl_demo/cl_axil_reg_access` |

The submodule revisions are the gitlinks in the pinned parent. The setup script
checks their commits, Git LFS objects, and the IP `VIVADO_VERSION`. It deliberately
uses `source hdk_setup.sh -s` after downloading pinned resources: the normal HDK
setup follows moving submodule branches. The `-s` flag also skips the routed shell
checkpoint download. That checkpoint is needed for the later FPGA build flow;
this preparation covers simulation only.

## Preconditions

- Use a dedicated directory owned by your user, on a volume with enough free
  space for the developer kit, its LFS IP resources, compiled IP and waveforms.
  Resource usage can be substantial; the scripts record free space and memory.
- Enable the AMI's Vivado 2025.2 environment by sourcing the installed
  `settings64.sh` if `vivado` is not already on `PATH`. Use the actual installation
  path from the AMI; `XILINX_VIVADO/data/xsim/xsim.ini` must exist.
- Bash 4+, Git, Git LFS, GNU Make, Python 3.10+, Perl, GCC/G++, GNU coreutils,
  `flock`, and Vivado's `xvlog`, `xelab`, `xsim`, `xsc` must already be installed.
  Missing tools cause a clear failure; the scripts do not invoke `sudo`.
- Outbound HTTPS access to GitHub and Git LFS resources is needed for setup. No
  AWS credentials are embedded in these scripts. AWS credentials are needed for
  account inventory or later instance/AFI operations, which are separate.
- The scripts require an absolute work directory without whitespace or shell
  metacharacters, because upstream HDK Makefiles contain unquoted paths. Use a
  new dedicated directory when intentionally changing the pins or Vivado version.

## Run on the existing developer instance

From the Bonsai repository checkout on the instance:

```bash
F2_WORK_ROOT=/home/ubuntu/work/bonsai-f2-erg102
bash fpga/aws_f2/setup.sh "$F2_WORK_ROOT"
bash fpga/aws_f2/validate.sh "$F2_WORK_ROOT"
```

Set `F2_WORK_ROOT` to a path owned by the actual login user. Do not run these
commands on the local macOS machine. No home directory is mounted or scanned.
The scripts serialize work on this directory with `flock` and preserve prior
simulation output when re-running a test.

The runner invokes each of these upstream tests individually, using XSIM:

1. `test_null`: power-up/reset.
2. `test_adder`: operand write, start, sum/carry readback, repeated operations.
3. `test_arithmetic_operations`: arithmetic and boundary cases.
4. `test_axil_registers`: register access and read-only protection.
5. `test_control_bits`: completion handshake.
6. `test_error_handling`: invalid and misaligned accesses.
7. `test_random`: the pinned test's default random configuration.
8. `test_reset`: reset during different operational states.
9. `test_stress_axil`: consecutive/mixed AXI-Lite traffic.

The exact underlying command, run from the example's `verif/scripts`, is:

```bash
make test_adder VCS=0 QUESTA=0 IES=0 SHELL=/bin/bash '.SHELLFLAGS=-o pipefail -c'
```

Each test is compiled/run separately so an earlier failure cannot disappear into
the upstream regression loop. Bash `pipefail` applies both to the runner's
logging and the upstream XSIM recipe. A test passes only when its make process
and log capture both succeed, its **fresh** simulator log reports
`*** TEST PASSED ***` and zero errors, and no recognized fatal/error marker is
present. The runner retains the first process failure as its exit code; a failed
evidence check produces exit `1`. A missing log or missing pass marker is a failure.

## Preserved developer environment

The configured developer instance has a reusable copy on root EBS, verified on
2026-09-26. The pinned HDK source and LFS resources are at
`/home/ubuntu/bonsai-erg102-f2-work/aws-fpga` (1.33 GiB). Setup exited `0`, all
three Git pins matched, both LFS stores passed checks, and all 5,435 tracked-file
hashes matched the original source. The verified Bazel 8.6.0 binary and LLVM 19
aliases are preserved at `/home/ubuntu/bonsai-erg102-tools` (61.46 MiB).

As `ubuntu`, activate the preserved tools and installed Vivado before running:

```bash
source /home/ubuntu/bonsai-erg102-tools/activate.sh
source /opt/Xilinx/2025.2/Vivado/settings64.sh
F2_WORK_ROOT=/home/ubuntu/bonsai-erg102-f2-work
bash /home/ubuntu/bonsai-erg102-bootstrap-v2/fpga/aws_f2/setup.sh "$F2_WORK_ROOT"
bash /home/ubuntu/bonsai-erg102-bootstrap-v2/fpga/aws_f2/validate.sh "$F2_WORK_ROOT"
```

Source Vivado's settings before enabling shell `nounset`. The source copy omits
generated test output, compiled IP libraries and three generated headers because
the old XSIM outputs contain absolute paths to instance-store storage. The first
validation at this durable root rebuilds those files locally. **XSIM has not been
rerun at the durable root**; the earlier 9/9 result remains separate evidence.
Repository build caches were not copied.

Detailed hashes, excluded paths and evidence locations are recorded in
[`reports/ERG-102/f2-xsim.json`](../../reports/ERG-102/f2-xsim.json).
The preserved tools directory contains `REUSE.md` with restart and activation
instructions; setup logs remain under the durable work root's `logs/` directory.

## Evidence and report

Each invocation prints its evidence directory under
`$F2_WORK_ROOT/logs/setup-<UTC>-<unique>` or
`$F2_WORK_ROOT/logs/validate-<UTC>-<unique>`. Setup produces a readiness record;
it does not produce simulation results. Validation records:

- `environment.txt`, `vivado-version.txt`, `hdk-setup.log` and
  `source-manifest.txt`: OS, tools, source/IP revisions and shell version.
- `results.tsv`: one status and make exit code per test.
- Per-test command, console log, raw XSIM logs, journals and waveform files.
- `result.txt`, `runner-sha256.txt`, `artifact-sha256.txt`, `exit-code.txt`.

The exit-code file is written by the final exit trap, so it is not included in
the preceding artifact hash manifest. Interrupted/failed early runs may have a
partial manifest; they must remain failed/incomplete evidence.

For ERG-102, commit a report at `reports/ERG-102/README.md`, link the implementation
PR, and attach a readable copy to Linear. Store large raw outputs in the agreed
durable artifact location and link them from the report/PR. Include the Bonsai
source revision, runner hashes, exact commands, developer AMI/instance/region
identifiers (record these from trusted EC2 inventory), tool versions, result
counts, logs, limitations and reviewer decision. Never label a command prepared
or a simulator merely installed as a successful test run.

## Bonsai and hardware scope

The existing `fpga/README.md` and Nexus targets describe Coral lab-board
integration. They are not an AWS F2 Custom Logic target. This directory establishes
the AWS environment against its official example; it does not port the Nexus
top-level RTL, simulate Bonsai under the AWS shell, or run model inference.

Keep these report rows distinct:

| Evidence | What it establishes |
| --- | --- |
| Bonsai Arcilator regressions | Repository simulation correctness for the tested targets |
| AWS example XSIM results from this runner | HDK/tool installation and AWS example/shell-model operation |
| Bonsai F2 CL XSIM results | Future Bonsai wrapper/interface behavior against the AWS shell model |
| Synthesis / routed implementation | Resource and timing results for a named CL configuration |
| Loaded AFI and host readback on F2 | Actual hardware execution, with AFI/AGFI and instance identifiers |

The next integration work is a Bonsai CL wrapper against the F2 shell with an
agreed register/command map, clock/reset behavior, and memory interface, followed
by a simulator fixture that drives it. A shell example pass does not satisfy the
full ERG-102 FPGA execution/interface acceptance gate by itself.

## Primary references

- [Pinned HDK setup source](https://github.com/aws/aws-fpga/blob/b603a81f65666e0cf7a67ee5cf18b148eb6b08c3/hdk_setup.sh)
- [Pinned example verification guide](https://github.com/aws/aws-fpga/blob/b603a81f65666e0cf7a67ee5cf18b148eb6b08c3/hdk/cl/examples/cl_demo/cl_axil_reg_access/verif/README.md)
- [Pinned test targets](https://github.com/aws/aws-fpga/blob/b603a81f65666e0cf7a67ee5cf18b148eb6b08c3/hdk/cl/examples/cl_demo/cl_axil_reg_access/verif/scripts/Makefile.tests)
- [Pinned XSIM recipe](https://github.com/aws/aws-fpga/blob/b603a81f65666e0cf7a67ee5cf18b148eb6b08c3/hdk/common/verif/tb/scripts/Makefile.xsim.inc)
- [Pinned pass/fail implementation](https://github.com/aws/aws-fpga/blob/b603a81f65666e0cf7a67ee5cf18b148eb6b08c3/hdk/common/verif/include/common_base_test.svh)
- [AWS HDK overview](https://awsdocs-fpga-f2.readthedocs-hosted.com/latest/hdk/README.html)
