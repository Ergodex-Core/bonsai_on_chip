# Reproduce the ERG-103 local simulator

Use Python 3.11+ with `gguf==0.19.0`, Verilator (validated with 5.048), a C++17
compiler and GNU Make. The exact model SHA256/revision is pinned in the
[weight-image instructions](../../utils/weightstore/README.md). Generated model
bytes, build outputs and readbacks stay outside the checkout; reserve at least
2 GiB of free space. The command refuses an existing output directory.

```bash
python3 tests/first_slice/validate.py \
  --gguf /absolute/Bonsai-1.7B-Q1_0.gguf \
  --out /absolute/fresh/erg103-validation --jobs 4
```

This command prepares and verifies the full native image, builds fresh RTL
executables, runs fixture/runner failure tests, store/compute/bridge and mailbox
regressions, then executes all 396 native cases and six directed synthetic
cases through the shared driver. It hashes sources before/after compilation
and validation and binds the executable in a manifest. Every step records its
command, exit code and log; `validation.json` remains FAIL/incomplete if any
step fails. The native and synthetic subdirectories contain per-case evidence,
transport logs and before/after full-image readbacks.

The local backend is Verilator with seed 103, variable DDR response latency
and independent AXI channel stalls. It drives the same RTL used by the custom
F2 wrapper. This command does not run Arcilator, vendor XSIM, synthesis or a
physical FPGA, and it does not generate model tokens. The [F2 workflow](../../fpga/aws_f2/first_slice/README.md)
provides the separate vendor and physical steps. See the [ABI](../../doc/spec/first_slice.md)
for the host/RTL numerical boundary.

The integrated transport handles only bus transactions and byte-addressed DDR;
it does not calculate dot products. The runtime separately derives expected
integer values from the image bytes. `test_run.py` deliberately uses mocked
transports to test failure reporting; those cases are not execution evidence.
Use the unchanged driver with a source-bound physical transport and actual
AGFI/DCP manifest to produce the physical report. Each physical suite requires
a fresh coordinated AFI reload because sealed images cannot be reopened.

## Public SDK transport failure tests

On Linux x86_64, use the pinned AWS checkout from the F2 setup. This test
compiles the physical transport against its exact public headers and a small
mock implementation. It checks 41 software failure/transfer cases, including
multi-page readback and exclusive output creation; it performs no FPGA access.

```bash
python3 tests/first_slice/test_transport.py \
  --sdk /absolute/pinned/aws-fpga --out /absolute/fresh/sdk-mock-results
```

The harness verifies header hashes, refuses existing output directories and
retains per-case stdout/stderr plus `report.json`. It remains effective under
`python3 -O`. The mock result is separate from the actual SDK link check and
from all RTL, XSIM and hardware evidence.

## Arcilator integrated first-slice workflow

The Arcilator path compiles the same store, DOT128, mailbox and F2 memory bridge
through `first_slice_sim_top`. It keeps the existing seed-103 C++ AXI/DDR model,
Python commands, canonical image checks and arithmetic oracle. A generated
adapter resolves only public ports from compiler metadata. It uses the runtime's
settled evaluation API when required; it does not patch internal state or clock
history. The declaration of `image_base`/`image_bytes` precedes their assignments
for compatibility with the CIRCT frontend.

This follows the source emission, explicit compiler stages and retained failure
logs used in [Wispr-on-chip](https://github.com/rachit-ergodex/wispr-on-chip), and
the metadata/settling approach of the existing
[Arcilator parity pilot](../arcilator/README.md). Wispr's model data and generated
RTL are not dependencies of this test.

On a Linux x86-64 host, provide an authorized external toolchain matching the
pilot's documented hashes, Python 3 with Jinja2, Clang/C++17, and the runtime
libraries pthread, atomic, zlib, tinfo, libm and libdl. The toolchain must include
`bin/{circt-verilog,arcilator,opt,llc}`, generated-header/runtime support and the
four static libraries named in `build_arcilator.py`. No tools are downloaded or
installed by these scripts. The recorded external package uses CIRCT frontend
`e2f80d17f` and Arcilator `272cba1b5`.

Prepare the existing pinned 1.7B fixture using `prepare_fixture.py` as documented
above, or use byte-identical previously generated fixtures. The native image must
retain its adjacent canonical `manifest.json`. Then run from a clean checkout:

```bash
python3 tests/first_slice/run_arcilator.py \
  --toolchain /absolute/path/to/toolchain \
  --fixture /absolute/path/to/fixture/fixture.json \
  --native-image /absolute/path/to/native/weights.bin \
  --synthetic-image /absolute/path/to/fixture/synthetic-ternary2.bin \
  --out /absolute/path/to/new-evidence-directory
```

The output directory must be new. Every compiler stage has a timeout; timed-out
process groups are terminated and retained evidence remains failed. The runner
requires the mailbox 3 tests, protocol 4 tests, synthetic 6 cases and native 396
cases, complete subreports, matching binary/build identities and unchanged input
hashes. Native execution includes the full 242,357,184-byte PCIS load and both
full readback hashes. Two synthetic invalid-format cases must be rejected as
expected. No failed or unported test is skipped to obtain a passing report.

`validation.json` and the individual build/suite logs preserve the source and
tool hashes, commands, statuses, cycle counters and wall times. Rates cover the
whole host workflow, including load/readback, hashes and command exchange. Record
whether the host is native x86-64 or an emulated Linux/amd64 container; compare
simulators only with the same host, fixture, bus driver and scheduling conditions.

This command covers the integrated first slice. It does not run the separate
DOT128, store, mailbox-fault or bridge unit benches, the complete repository
suite, full CoralNPU, model token generation, FPGA hardware or the systolic-array
proposal. Those results must remain separately identified.
