# Native HBM ROM-equivalent F2 attachment

This CL connects the sealed first-slice store and DOT128 to the native AMD HBM
controller. It is separate from the DDR CL. Implementation: Codex; issue owner:
Rachit Tibrewal; independent review: the separate automated source/evidence
review recorded in [the HBM report](../../../reports/ERG-103/hbm-rom/README.md).
The issue remains open. No HBM vendor simulation, synthesis, routed timing,
resource utilization, AFI or physical execution result is claimed here.

## Fixed allocation and writer boundary

`cl_bonsai_hbm_rom` connects PCIS through `f2_memory_bridge`, then
`hbm_line_bridge`, then `hbm_rom_controller.HBM_CORE_I` (`cl_hbm`). A logical
64-byte line is two 256-bit beats, LEN=1, SIZE=5, INCR, native ID=0. Byte order
and the tagged/epoch frontend remain unchanged. The adapter checks the complete
64-bit local address before producing `{5'd15, local_address[28:0]}`. AXI ingress
port 15 accesses pseudochannel 15's 512 MiB region; the image is still limited
to 256 MiB. There is no striping, runtime map register or large-model extension.
The native address selection follows the pinned kernel's 512 MiB/channel map,
not its outdated channel-0 comment.

Only port 15 has a master. Every other native AWVALID/WVALID/ARVALID is zero.
There is no performance/test/scrub master, writer mux, debug writer, controller
reset CSR or clock-reconfiguration route. Controller APB write inputs and the
clock MMCM's AXI-Lite input requests are tied off. Shell APB monitor signals
remain connected. DDR is instantiated with `DDR_PRESENT=0`; PCIM and unused
shell interfaces are tied off. This standalone slice has no KV/activation
allocation: those must remain separate mutable storage when integrated later.

The existing load, close admission, drain, whole-image readback/hash and seal
handshake controls the sole PCIS writer. The hash is the disclosed trusted-host
handshake. Seal and fault state reset only with the shell-wide reset. FLR is
unsupported (`cl_sh_flr_done=0`, the pinned unused-FLR convention); it cannot
unlock weights, and callers must not expect FLR recovery.

## Clocks, reset and errors

The main clock is shell `clk_main_a0` at 250 MHz. A direct pinned
`clk_mmcm_hbm` generates fixed H2/450 MHz from the shell's 100 MHz HBM reference.
Its configuration bus has no writer. The `AWS_CLK_GEN/CLK_HBM_EN_I/CLK_MMCM_HBM_I`
hierarchy intentionally matches the pinned AWS clock constraints and recipe
application. Unused A/B/C clock groups can produce missing-cell messages from
that common flow; the HBM MMCM must exist and be constrained.

The native controller resets on clock-lock loss. Its reset deassertion uses
8 destination synchronization stages, so even immediate relock holds native
readiness low for more than 15 ns (over three main-clock periods). The bridge
retains source transaction ownership across native resets: its paired reset is
only `rst_main_n`; the independent source readiness monitor faults unfinished
transactions and blocks reuse until shell reset. This prevents relock from
silently resuming a sealed image. Reload, complete verification and reseal are
required after shell reset, reconfiguration or power loss.

Bundled-data CDC mailboxes hold payloads until acknowledgement. The XDC applies
2 ns maximum datapath delay and bus skew to each payload bank; only the first
stages of handshake/status synchronizers receive false-path exceptions. There
is no whole-clock false path. These constraints and recovery/removal behavior
still require Vivado CDC and post-route review; CPU simulation is not that proof.

Every AXI read beat and write response is checked by the adapter. Both stacks'
initialization and catastrophic-temperature trip status affect readiness.
`cl_sh_status0` is read-only telemetry: bits [1:0] initialization, [3:2]
catastrophic trips, [10:4]/[17:11] sampled temperatures, bit 18 clock lock and
bit 19 sticky readiness-loss/trip fault. Temperatures are sampled, non-atomic
telemetry; the trip signal controls protection. `cl_sh_status1[1:0]` reports
adapter fault/backend readiness; virtual LEDs [1:0] show readiness.

The exact pinned HBM XCI has user/DQ parity checks, ECC correction and scrubbing
disabled. This implementation preserves that configuration. It does not claim
parity/ECC correction coverage or poll ECC APB counters. AXI errors, readiness
loss, thermal trip and logical hash/readback checks are the implemented checks.

## Pins and source-only audit

| Input | Exact version |
| --- | --- |
| AWS HDK / SDK | `b603a81f65666e0cf7a67ee5cf18b148eb6b08c3` (2.3.4) |
| `hdk/common/ip` | `6d32be972e6da854e61a8d3d6ec0466ab491c1b3` |
| `hdk/common/shell_stable/hlx` | `2383c2b64572c75163b1b60fbd0abea482c637e6` |
| Vivado / target | 2025.2 / `xcvu47p-fsvh2892-2-e`, small shell |
| `cl_hbm.xci` SHA256 | `1f8994d1ba76dc3bd81168de89b8a985fb3ab46602e60c12c25ef347231b0ac9` |
| `clk_mmcm_hbm.xci` SHA256 | `3f236453d1655985abcb33701fa48d2910630bd20b38cda1eb508e4bb05a11c8` |

The CPU source audit reads the vendor's pinned `.veo` declarations through Git;
it checks all 1,102 HBM ports and all 22 clock ports. Optional width lint uses
temporary declarations with **no functional behavior**. These declarations are
never staged into FPGA builds or vendor simulation.

```bash
python3 fpga/aws_f2/hbm_rom/audit_sources.py \
  --hdk /absolute/pinned/aws-fpga \
  --resources /absolute/pinned/aws-fpga/hdk/common/ip \
  --verilator /absolute/bin/verilator
```

This audit and declaration-only Verilator 5.048 lint passed in the cloud CPU
workspace. Verible parsed the new RTL; Python compilation and shell syntax checks
passed. The exact pinned AWS Makefiles parsed `test_hbm_rom` as an XSIM target
with the pinned `common_liblists.mk`. None of these checks executes vendor IP.
The HBM physical transport also compiled against the real pinned SDK with GCC
14.2.0 and `MEMORY_BACKEND_HBM=1`; this compile never opened an FPGA.

## Prepare and vendor simulation

Use a dedicated clean HDK checkout, fully initialized pinned IP/HLX submodules,
Git LFS objects and Vivado 2025.2. Preparation verifies origins, exact revisions,
tracked cleanliness and LFS checks. Generated model/fixture bytes stay outside
Git. The shared canonical fixture packer first checks the complete canonical
242,357,184-byte image, then generates the same 4,160-byte compact relocation
fixture: three actual Q1_0 groups plus one separate synthetic ternary group.
The canonical image, independent oracle and full-image simulation command are
specified in [the HBM report](../../../reports/ERG-103/hbm-rom/README.md).

```bash
source /tools/Xilinx/2025.2/Vivado/settings64.sh
python3 fpga/aws_f2/hbm_rom/prepare.py \
  --hdk /absolute/pinned/aws-fpga \
  --fixture /absolute/model/first-slice/fixture.json \
  --image /absolute/model/native/weights.bin \
  --output /absolute/fresh/hbm-work
bash fpga/aws_f2/hbm_rom/run_sim.sh \
  /absolute/fresh/hbm-work/cl_bonsai_hbm_rom 3600
```

The example 3,600-second simulation limit is a proposed execution budget, not an
allocation approval or measured duration. Coordinate a licensed host first.
`run_sim.sh` uses the pinned shell BFM/Makefiles, including their HBM
`hbm_v1_0_vl_rfs.sv`, compiled IP libraries and XSIM HBM configuration files.
It polls native readiness, verifies BACKEND=3, loads/drains/compares every compact
byte, seals, checks an explicit SLVERR write rejection and counter, executes the
four oracle DOT128 cases, and compares every byte again. It does not force
readiness or modify vendor memory directly. The expected completion marker is
`HBM_ROM_TEST_PASS`; actual assertion/job counts must come from a future run.
The watchdog, logs and source manifest stay with that run. Q1_0 remains binary
±1 with raw FP16 scales, not native ternary or full-model proof.

## Build and physical evidence binding

Heavy builds, uploads and image/host operations need separate coordination with
the F2 lifecycle owner. These scripts do not start/stop hosts or create/load AFIs.
A new dedicated source/work directory and sufficient storage are required;
there is no measured HBM build time or resource result yet.

```bash
bash fpga/aws_f2/hbm_rom/run_build.sh \
  /absolute/fresh/hbm-work/cl_bonsai_hbm_rom 7200
bash fpga/aws_f2/hbm_rom/build_transport.sh \
  /absolute/pinned/aws-fpga /absolute/fresh/hbm-transport
```

The example 7,200-second DCP limit is a proposed budget; the routed-DCP audit has
an additional 900-second timeout. The runner captures tool/source manifests,
uses small shell/H2 explicitly, rejects negative timing and DRC errors, and emits
CDC/exceptions reports. Its result explicitly leaves independent CDC/DRC review
pending. `build_transport.sh` records source/binary/SDK hashes, compiler version
and the HBM compile define. The shared transport and runtime require BACKEND=3;
a DDR AFI or mock cannot silently count as HBM.

Only after reviewed routing, coordinated upload/AFI creation and an available
custom AFI can the local evidence binder produce `transport.manifest.json`:

```bash
python3 fpga/aws_f2/hbm_rom/bind_manifest.py \
  --cl /absolute/fresh/hbm-work/cl_bonsai_hbm_rom \
  --build-result /absolute/fresh/hbm-work/cl_bonsai_hbm_rom/logs/build-RUN/build-result.json \
  --transport-work /absolute/fresh/hbm-transport \
  --afi-description /absolute/private/describe-fpga-images.json \
  --implementation-review /absolute/private/implementation-review.json \
  --afi-submission-record /absolute/private/afi-submission-record.json
```

The independent review JSON requires `reviewer`, `decision: "PASS"`, and exact
`dcp_sha256`, `source_manifest_sha256`, `cdc_report_sha256`, `drc_report_sha256`.
The trusted uploader record requires `uploader`, `afi`, `agfi`, `dcp_sha256`,
`tar_sha256`, `s3_bucket`, `s3_key`, `create_receipt_sha256`, and
`upload_checksum_sha256` (hex SHA256 equal to the local tar). The binder checks
these bindings; it cannot authenticate remote upload receipts from local JSON.
The uploader/reviewer must retain the original upload/create receipts. Keep
private bucket/object/instance identifiers and model bytes out of public Git.
No review or receipt is synthesized by this workflow.

Use the shared first-slice physical runner with the HBM transport and this
manifest, as documented in the HBM report. Record actual arithmetic outputs,
raw scales, hashes before/after, counters, tool/source/AFI/board identifiers and
all error cases. Physical execution, final implementation review and merge are
still required before the issue can be completed.
