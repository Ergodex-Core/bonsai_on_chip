# Arcilator handshake-control parity pilot

This pilot compiles the repository's unchanged `handshake_multistage_ctrl.sv` and
`cdffr.sv` with Verilator and the external Ergodex CIRCT toolchain. It runs one
shared C++ driver against an independent behavioral oracle and compares every
recorded input/output phase. It is not a cocotb backend or a full-repository test
runner.

The four configurations are `NUM_PIPE_REGS=1,3` crossed with
`REMV_PIPE_BUBBLE=0,1`. Tests cover backpressure, bubbles, flush, asynchronous
reset, rising/falling clock edges, and input changes without clock edges. Random
stimuli use the fixed seed `0x5eed1234`.

## Prerequisites

- Python 3.9 or newer, Verilator, a C++17 compiler, and make on the reference host.
- An authorized external copy of the private Linux/amd64 Ergodex CIRCT toolchain
  with `circt-verilog`, `arcilator`, LLVM `opt`/`llc`, the generated-header runtime,
  and the four libraries linked in `run_parity.sh`.
- A compatible Linux/amd64 environment with Clang, Python/Jinja2 and the runtime
  system libraries. The recorded run used the existing
  `whisper-arcilator:portable` image. The repository does not install, download,
  distribute, or build this private toolchain/image.

The tested compiler package is identified by the external `PROVENANCE.txt` and
the committed report's full hashes. Key pins:

| Artifact | SHA-256 |
|---|---|
| `bin/arcilator` (CIRCT `272cba1b5`) | `17726ec757c1de733934d79f05af4bd398e61ac7eb2db1814c2dfe1709277e91` |
| `bin/circt-verilog` (CIRCT `e2f80d17f`) | `39b437d8e02f2fe776a9189672e75d33946fc9fdb9e887b26452283eafa0d625` |
| `lib/libCIRCTArcRuntime.a` | `c861393b8a009874774f89c7bc48f8dd3ce7e7ed9809870a35f976f943938234` |
| Docker image | `sha256:75ff5522c3b54b1c3802f8d8ea201198e14a5a22e7932e5d0d96b93b0a1061ac` |

## Run from the repository root

Set `BONSAI_ARC_TOOLCHAIN` to your authorized external toolchain directory.
Use the pinned artifacts when reproducing the recorded results.

```bash
export BONSAI_ARC_TOOLCHAIN=/absolute/path/to/toolchain
bash -n tests/arcilator/run_parity.sh
bash tests/arcilator/run_parity.sh \
  verilator . reports/ERG-102/raw/arcilator-pilot

docker run --rm --platform linux/amd64 --network none --entrypoint bash \
  -v "$PWD:/repo:ro" \
  -v "$PWD/reports/ERG-102/raw/arcilator-pilot:/output" \
  -v "${BONSAI_ARC_TOOLCHAIN:?set external toolchain path}:/toolchain:ro" \
  whisper-arcilator:portable \
  /repo/tests/arcilator/run_parity.sh arcilator /repo /output /toolchain

bash tests/arcilator/run_parity.sh \
  compare . reports/ERG-102/raw/arcilator-pilot
```

On a Linux host with both toolchains installed, the container command can be
replaced with:

```bash
bash tests/arcilator/run_parity.sh \
  arcilator . reports/ERG-102/raw/arcilator-pilot "${BONSAI_ARC_TOOLCHAIN:?set external toolchain path}"
```

Each backend must print four `PASS` lines. `compare` must print four
`PASS full trace parity` lines and write `results.json` with `status: complete`.
An oracle error, incomplete backend run, trace mismatch, or stale input manifest
exits nonzero. Comparison checks both backend manifests against the **current**
hashes of both DUT files, the shared driver, and this runner, then checks each
trace against its completed-run hash. Starting a backend marks the aggregate
`results.json` as `incomplete` and invalidates that backend's previous success
manifest before checking tools or compiling. A failed or interrupted rebuild
cannot leave an earlier comparison marked complete. Only a successful `compare`
sets the aggregate result to `complete` again.

`raw/` artifacts are gitignored. Retain them in authorized private storage and
attach or link durable evidence through the Linear issue for review,
including source/tool manifests, result logs, both CSV traces, and compile logs.
The compact committed evidence is
[`reports/ERG-102/arcilator-pilot.md`](../../reports/ERG-102/arcilator-pilot.md) and
[`arcilator-pilot.json`](../../reports/ERG-102/arcilator-pilot.json).

The driver uses generated metadata for public ports and the settled Arc runtime
API when required. It does not patch RTL, generated state, or clock history. No
PGO, performance claim, FPGA result, cocotb compatibility, or full-suite pass is
implied by this pilot.
