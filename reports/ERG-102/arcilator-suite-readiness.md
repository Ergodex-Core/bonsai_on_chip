# Arcilator readiness: existing SystemVerilog testbenches

Two unchanged repository testbenches were submitted to the installed CIRCT
frontend. **Both returned exit code 1; neither reached Arcilator simulation.**
This is recorded compatibility evidence, not a full-suite pass.

The source revision was `20a2f0f2518c6664f83504b922e94897a99931f0`, with no
changes to the four RTL/testbench inputs. No test checks were removed, no
unsupported constructs were suppressed, and no compiler source was copied.

## Observed results

| Existing target | Frontend | Simulation | Blocking diagnostics |
| --- | --- | --- | --- |
| `//hdl/verilog/rvv/design:aligner_tb` | Exit 1 | Not run | `Aligner_tb.sv:32,34`: unpacked `logic$[3:0]` testbench signals cannot connect implicitly to packed `logic[3:0]` DUT ports. Line 52 also formats an unpacked array without a specification string. |
| `//hdl/verilog/rvv/design:multififo_tb` | Exit 1 | Not run | `MultiFifo_tb.sv:56`: clocking-block `data_in` has unsupported type `!moore.array<4 x l32>`. |

MultiFifo additionally produces a frontend remark at line 15: class builtins
needed for randomization, constraints and covergroups are unsupported and will
be dropped during lowering. **That is a semantic coverage blocker**, even if the
clocking-block error is resolved: successful compilation alone would not prove
that the original randomized test was executed faithfully. Its existing call
to `transaction.randomize()` is at line 39. The separate `$fatal` argument warning
at that line is also preserved in the raw log.

## Reproduce the frontend probes

Use a compatible Linux x86_64 environment from the repository root. Set
`BONSAI_ARC_TOOLCHAIN` to the external toolchain directory containing `bin/`.
The package is an external prerequisite and is not distributed in this public
repository. Verify these pins before interpreting the results:

| Tool | Version | SHA-256 |
| --- | --- | --- |
| `circt-verilog` | CIRCT `e2f80d17f`, LLVM `23.0.0git`, slang `11.0.0+0` | `39b437d8e02f2fe776a9189672e75d33946fc9fdb9e887b26452283eafa0d625` |
| `arcilator` | CIRCT `272cba1b5`, LLVM `23.0.0git` | `17726ec757c1de733934d79f05af4bd398e61ac7eb2db1814c2dfe1709277e91` |

```bash
: "${BONSAI_ARC_TOOLCHAIN:?Set the external toolchain directory}"
audit="${PWD}/reports/ERG-102/raw/arcilator-suite-audit"
mkdir -p "${audit}"
sha256sum "${BONSAI_ARC_TOOLCHAIN}/bin/circt-verilog" \
  "${BONSAI_ARC_TOOLCHAIN}/bin/arcilator"

if "${BONSAI_ARC_TOOLCHAIN}/bin/circt-verilog" \
  --ir-hw --single-unit --timescale=1ns/1ps --top=Aligner_tb \
  hdl/verilog/rvv/design/Aligner.sv \
  hdl/verilog/rvv/design/Aligner_tb.sv \
  -o "${audit}/aligner.mlir" > "${audit}/aligner-frontend.log" 2>&1; then
  status=0
else
  status=$?
fi
printf '%s\n' "${status}" > "${audit}/aligner-frontend.exitcode"

if "${BONSAI_ARC_TOOLCHAIN}/bin/circt-verilog" \
  --ir-hw --single-unit --timescale=1ns/1ps --top=MultiFifo_tb \
  hdl/verilog/rvv/design/Aligner.sv \
  hdl/verilog/rvv/design/MultiFifo.sv \
  hdl/verilog/rvv/design/MultiFifo_tb.sv \
  -o "${audit}/multififo.mlir" > "${audit}/multififo-frontend.log" 2>&1; then
  status=0
else
  status=$?
fi
printf '%s\n' "${status}" > "${audit}/multififo-frontend.exitcode"
```

The recorded probes ran in a local Linux/amd64 container with read-only source
and toolchain mounts at `/source` and `/toolchain`, and output at `/audit`.
The JSON records the exact compiler argument lists, container image ID,
source hashes and log hashes. The commands above use the same inputs and
options with paths relative to the checkout. Expected recorded status is 1
for each probe; absence of a simulator run must remain explicit.

## Evidence and limits

[Machine-readable report](arcilator-suite-readiness.json) contains the complete
source/tool provenance. Raw logs and exit codes live under
`reports/ERG-102/raw/arcilator-suite-audit/`, which is gitignored. No raw compiler
binaries, source trees or generated executables are included.

These probes do not establish compatibility or failure for the remaining
repository targets, including cocotb, Chisel, SystemC and UVM. The external
package contains UVM runtime support; its exact usable coverage was not tested
here. The separate four-configuration handshake parity pilot remains distinct
from these two existing testbench failures and from full-suite validation.

The next step for these two targets is a reviewed portability/compiler issue
that preserves their original stimulus and checks. MultiFifo requires proof
that randomization, constraints and clocking-block behavior survive lowering
before a passing result can be accepted.
