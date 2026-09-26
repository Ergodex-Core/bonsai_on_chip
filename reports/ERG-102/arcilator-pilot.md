# Arcilator / Verilator handshake control parity pilot

**Result: PASS in four configurations. This is a bounded RTL backend pilot, not an execution of the repository's test suite.**

Run date: 2026-09-26. Rebuilt from repository paths on branch
`codex/erg-102-simulation-setup`, with HEAD
`9ab1dcc870dab7b33ef8a46c218bc40a2e54fdf2` and an updated,
uncommitted runner. Its hash in the JSON identifies the exact tested bytes;
`raw/arcilator-pilot/runner.patch` records the change against that HEAD. The driver
and DUT contents are unchanged. The DUT originated at
`5eff3822250fc52b2a0f83315969238805ece7c5`.

The experiment compiles these repository files directly, without modifying or copying RTL:

- `hdl/verilog/rvv/common/handshake_multistage_ctrl.sv`
- `hdl/verilog/rvv/common/cdffr.sv`

Both backends use the same `handshake_parity.cpp` driver. An independent behavioral
pipeline occupancy model checks every public output before the driver writes each
trace row. The final comparison requires the complete Verilator and Arcilator CSV
traces to be byte-for-byte identical, including input phases and all output values.

| NUM_PIPE_REGS | REMV_PIPE_BUBBLE | Rising edges | Observed samples per backend | Output assertions per backend | Oracle checks | Trace parity |
|---:|---:|---:|---:|---:|---|---|
| 1 | 0 | 2,105 | 10,414 | 52,070 | PASS both | PASS |
| 1 | 1 | 2,105 | 10,414 | 52,070 | PASS both | PASS |
| 3 | 0 | 2,115 | 10,444 | 52,220 | PASS both | PASS |
| 3 | 1 | 2,115 | 10,444 | 52,220 | PASS both | PASS |

The final repository-path run passed all four configurations again. Both backends
were rebuilt after the runner was changed to invalidate stale aggregate results;
the evidence hashes identify the final driver and runner. Prior complete evidence
is preserved separately in `raw/arcilator-pilot-before-result-invalidation-9ab1dcc8/`.
Pilot-file clang-format,
Markdown lint, and ShellCheck pass, and the repository macro-signature check
passes. Evidence
guards were also checked using temporary copies: both backend manifests reject
stale hashes for each DUT file, the driver, and the runner; incomplete backend
runs and altered traces are rejected. All 15 guard checks passed, including the
untampered control and two failed-rerun cases. Each new case begins with completed
evidence, forces a backend dependency check to fail, and verifies that
`results.json` becomes `incomplete` before the tool check, while the previous
traces remain available without a valid aggregate PASS. These are evidence
integrity checks, not additional DUT coverage. Details are retained in
`raw/arcilator-pilot/guard-results.json`.

Total: 41,716 observed samples and 208,580 output assertions per backend; four
passed configurations, zero failed, zero skipped within this pilot. Counts and
trace hashes are in `arcilator-pilot.json` (derived from `raw/arcilator-pilot/results.json`).

## What was checked

The driver validates `up_ready`, `down_valid`, `reg_enable`, `valids`, and `busy`
against behavioral expectations. It covers:

- Filling, draining, replacement, upstream bubbles, and downstream backpressure.
- Both the global-shift policy and bubble-removal policy.
- Values immediately before rising edges, after rising edges, and after falling edges.
- Combinational ready changes without a clock edge.
- Synchronous flush, including flush while a full pipeline is stalled.
- Asynchronous reset asserted with clock low and with clock high; clock-high reset
  is asserted while the pipeline is full, without a further rising edge.
- Reset release and recovery, plus 2,048 deterministic randomized iterations per
  configuration using xorshift32 seed `0x5eed1234`.

The oracle tracks logical occupied slots without reading DUT internals or generated
state storage. The Arcilator adapter resolves public port offsets and widths from
the generated header metadata. All four generated models declare
`settlesInOneEval=false`, so the driver calls `arcRuntimeIR_simStepSettled` and
allows the runtime to drain tasks/NBA updates before observing outputs. No state
offsets, clock histories, or model internals are patched.

## Tools and artifacts

- Verilator: `5.048 2026-04-26 rev vUNKNOWN-built20260426`, run on the macOS host.
- Arcilator: bundled CIRCT `272cba1b5`, LLVM `23.0.0git`.
- SystemVerilog frontend: bundled `circt-verilog`, CIRCT `e2f80d17f`, slang `11.0.0+0`.
- Arcilator runs in the existing Linux/amd64 `whisper-arcilator:portable` image,
  ID `sha256:75ff5522c3b54b1c3802f8d8ea201198e14a5a22e7932e5d0d96b93b0a1061ac`.
- Compiler, runtime, source, driver, and build script hashes are in
  `raw/arcilator-pilot/arcilator-inputs.json` and `raw/arcilator-pilot/verilator-inputs.json`. Both completed manifests
  confirm the same repository revision and unchanged DUT sources. The toolchain's
  Arcilator, frontend, LLVM tools, and Arc runtime library hashes match the
  corresponding entries in the external toolchain package's `PROVENANCE.txt`.
- `raw/arcilator-pilot/n*_b*/` contains both raw CSV traces, per-backend result files, generated
  model/header/state metadata, executable artifacts, and build logs.
- The frontend and Arcilator compile logs contain no diagnostics. Each Arcilator
  link emits 21 warnings from the bundled runtime headers concerning C linkage
  and C++ types; these are retained in `arcilator/link.log`.

No PGO, model-specific LLVM plugin, reused state layout, or performance measurement
is used. LLVM compilation and C++ linking use `-O2` for the Arcilator backend.

## Exact reproduction commands

Run from the repository root. Prerequisites and external private-toolchain pins
are documented in `tests/arcilator/README.md`. No dependency installation, Bazel
command, network access, or shared-container modification is required.

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

Each backend must print four `PASS` lines. Comparison must print four
`PASS full trace parity` lines and produce `results.json` with `status: complete`.
The runner resolves repository, build, and driver paths absolutely before builds.
At backend start it first marks aggregate `results.json` as `incomplete`, then
invalidates that backend's prior success manifest before dependency checks or
compilation. A failed or interrupted rerun cannot retain the earlier aggregate
PASS. It completes the backend manifest only after all four oracle runs pass;
only a successful comparison completes the aggregate result. Comparison requires
both completed manifests to match the current DUT, driver, and runner hashes, and
checks each trace against the hash in its completed manifest before comparing
all bytes. An error produces a nonzero exit status.

The script compiles parameter overrides directly on the original top module,
with no wrapper RTL. Raw evidence is retained under the gitignored directory
`reports/ERG-102/raw/arcilator-pilot`. Retain it in authorized private storage
and attach or link durable evidence through the Linear issue for review.

## Scope limits

This checks handshake control with driven binary inputs. The DUT has no payload
data path. It does not establish four-state/X behavior, cocotb compatibility,
ChiselSim compatibility, SystemC/UVM integration, SRAM DPI loading, CPU/ISA or
model-inference correctness, full-repository test coverage, FPGA execution, or
performance. Existing repository tests have not been replaced or marked passing
on Arcilator. Those remain separate integration and validation work.

The pilot driver, runner, and README live under `tests/arcilator/`. This report
and its JSON summary are reviewable repository artifacts. The original DUT RTL
remains unchanged; no result is claimed for other repository tests.
