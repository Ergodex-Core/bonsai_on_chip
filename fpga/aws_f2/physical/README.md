# Physical F2 register-access smoke test

This harness runs the public AWS `cl_axil_reg_access` image on FPGA slot 0.
It validates the physical PCIe/OCL register path. It does not run CoralNPU,
Bonsai inference, a model weight store, DDR/HBM, or a model bitstream.

## Prerequisites and provenance

- An existing, authorized F2 host with an idle slot 0 and the Small Shell
  `0x10212415`, Ubuntu 24.04, Bash, GCC, Make, Python, `flock`, and `timeout`.
- A clean AWS FPGA checkout at
  `b603a81f65666e0cf7a67ee5cf18b148eb6b08c3`.
  The surrounding `fpga/aws_f2/setup.sh` prepares this pinned checkout.
- Public image `agfi-06447dea0ca9b0a39`, regional image
  `afi-04454b01e29b26073` in `us-east-1`, published by Amazon as
  `cl_axil_reg_access`. Its expected application PCI IDs are `1d0f:f006`.
  The pinned checkout names this AGFI in
  `sdk/notebooks/01_cl_axil_reg_access.ipynb`.
- Root access for application BAR access and slot load/clear operations.
  The preparation step runs as a normal user and installs nothing globally.

The upstream example documents `clk_main_a0` at 250 MHz. Record actual clock
telemetry separately when available. Amazon's public AFI metadata does not
publish the exact bitstream build commit, DCP hash, timing report, or utilization
report. A pinned software/source checkout and public-image identity are not
proof of a locally reproduced bitstream build.

## Reproduce

Choose a **new** work directory, preferably on durable EBS storage. The supplied
checkout must already be pinned; the harness refuses a changed tracked tree.

```bash
AWS_FPGA_CHECKOUT=/path/to/pinned/aws-fpga
PHYSICAL_WORK=/path/to/new/physical-work
bash fpga/aws_f2/physical/prepare.sh "$AWS_FPGA_CHECKOUT" "$PHYSICAL_WORK"
sudo bash "$PHYSICAL_WORK/harness/validate.sh" "$PHYSICAL_WORK"
cat "$PHYSICAL_WORK/physical-result.txt"
```

`prepare.sh` exports only committed SDK/runtime files into the new work directory
with `git archive`, builds the SDK using
`mkall_fpga_mgmt_tools.sh`, compiles the unmodified public runtime examples with
`make all`, and compiles `mmio_probe.c` with `-Wall -Wextra -Werror`.
It supplies the shared-library SONAME link and operation-name wrappers expected
by the static SDK management executable. `build.log`, `build-exitcode.txt`,
`build-sha256.txt`, and `linked-libraries.txt` record what was built and loaded.

`validate.sh` acquires `/var/lock/erg102-fpga-slot0.lock` and writes a visible
reservation in the work directory. This advisory lock only coordinates callers
that respect it; check with anyone sharing the host before running. The script
verifies prepared source/binary hashes, checks for competing processes, and
accepts only a cleared slot or the exact
public image above. An unrelated image aborts the run. It clears/reloads this
public image to test reset, then leaves that image loaded, releases its lock,
and never stops the instance. It refuses to overwrite a previous run's evidence.

## Checks and evidence

- Five registers are zero after initial configuration and again after reload.
- Upstream `test_sum`, `test_carry`, and `test_random` perform 2,001 additions;
  one further `test_sum` validates recovery after reset.
- The independent probe runs 262 deterministic additions, operand readback,
  completion polling and acknowledgement, and checks reserved control bits.
- Writes to read-only sum/carry registers are ignored and preserve their values.
- Three invalid aligned addresses return `0xDEADBEEF`; invalid writes leave
  operands unchanged. This is the example's sentinel behavior, **not an AXI
  SLVERR/DECERR test**.
- Nonzero operand state is established before reconfiguration, then verified
  cleared afterward. This tests reset through AFI reconfiguration; it is not a
  separate warm-reset register test.

The independent probe makes 2,128 assertions in `full` mode and 8 in each
`reset` invocation. Any failed assertion exits nonzero. Every SDK command/test
has a 120-second host timeout; its exit code is captured separately. Upstream
pass markers, failure text, and the 1,000 carry/random result counts are checked.
The upstream misalignment demonstration is deliberately excluded because it
prints observations without validating them.

`public-raw/*.log` contains original command labels, device results, assertions,
and exit codes without account IDs, instance IDs, host paths, or credentials.
Keep these files unchanged when attaching evidence. The surrounding work tree
also retains full build logs, linked-library paths, source/binary hashes and
private process inventory; review those separately before publishing.
