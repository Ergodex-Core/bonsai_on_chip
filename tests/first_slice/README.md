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
