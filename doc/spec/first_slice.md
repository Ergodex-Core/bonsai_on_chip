# ERG-103 first-slice mailbox and execution contract

The separate [HBM backend candidate](../../reports/ERG-103/hbm-rom/README.md)
preserves this mailbox/arithmetic contract and adds its own controller, CDC,
reset and validation evidence. DDR-specific references below describe the
original backend and do not imply that its FPGA results validate HBM.

Version `0x00010000`, magic `0x444f5431` (DOT1). This interface implements the
first executable Week 1 slice of the [Week 0 contract](week0.md), with the
existing [weight-store control ABI](../microarch/weightstore.md) unchanged.
The host/testbench occupies the CPU bus-master boundary. CoralNPU firmware,
RVV helper instructions, the SoC interconnect mapping and full model inference
remain later milestones.

## Data path and numerical boundary

The path is host BAR4/PCIS loader, locked DDR bridge, 64-byte weight-store
reader, DOT128, and BAR0/OCL mailbox completion. Local simulation instantiates
the same bridge/store/compute modules with an AXI DDR memory model. The AWS
wrapper substitutes the public `sh_ddr` controller. The engine has no write
port and receives every weight through the store. FPGA DDR is a read-only
weight source after sealing; it is not fabricated on-chip ROM or HBM.

A native Q1_0 command reads the unchanged 18-byte group: one little-endian
FP16 scale followed by 128 binary signs in LSB-first bit order. A separate
synthetic ternary command reads 32 bytes with codes 00=0, 01=+1, 10=-1;
11 rejects the entire command. Both consume 128 signed int8 inputs and return
an exact signed int32 dot product, widening before negation of -128.
No intermediate rounding or saturation occurs. Native results preserve the
original scale bits; valid synthetic results return FP16 1.0 (`0x3c00`).

The runtime performs the separately disclosed single FP32-rounded multiply
of the integer result and FP16 scale. That stage runs on the host. This is a
group arithmetic test with seeded integer activations, not the model's native
activation kernel, decoder or token-generation implementation. An invalid
ternary encoding or nonfinite native FP16 scale returns status 7 and zero
result/scale.

## Mailbox register page

All registers are aligned 32-bit little-endian words. AXI-Lite AW and W may
arrive independently; responses remain stable under backpressure. Full word
strobes are required for writes. BAR0 offset `0x1000` contains this mailbox;
page zero contains the weight-store ABI. The proposed SoC address is
`0x40090000`, adjacent to weight control at `0x40080000`; these reservations
are not yet wired into the CoralNPU SoC.

| Relative offset | Register | Contract |
| --- | --- | --- |
| 0x00 / 0x04 / 0x08 | MAGIC / ABI / CAPS | DOT1 / v1 / 7 |
| 0x0c | STATUS | Bits 0 busy, 1 done, 2 result error, 3 dot fatal, 4 store fault, 5 job aborted |
| 0x10 | COMMAND | 1 submit, 2 acknowledge |
| 0x14 | RESULT_STATUS | Weight-read status, or 7 invalid descriptor/encoding |
| 0x18 | COOKIE | Submitted identifier |
| 0x1c | OPCODE | 1 native Q1_0, 2 synthetic ternary2 |
| 0x20 | ELEMENTS | Must be 128 |
| 0x24 / 0x28 | WEIGHT_OFFSET | 48-bit logical byte address; high 16 bits must be zero |
| 0x2c | EPOCH | Copy the sealed store's epoch |
| 0x30 / 0x34 / 0x38 | RESULT / SCALE / COOKIE | Signed int32, raw FP16 bits, completed identifier |
| 0x40 / 0x44 | CYCLES | 64-bit command cycles, excluding held completion |
| 0x48 / 0x4c | MEMORY_STALLS | 64-bit cycles waiting for request/response |
| 0x50 / 0x54 | READ_REQUESTS | 64-bit accepted line requests |
| 0x80–0xfc | ACTIVATIONS | 128 int8 lanes, increasing byte/lane order |

Only one job may be active. Descriptor/input changes, repeated SUBMIT and ACK
while busy return an AXI error. DONE holds results until ACK; a new job cannot
overwrite an unacknowledged result. Invalid dimensions/opcodes return status
7 without issuing reads. A not-ready store completes status 6. A valid payload
needs exactly one line, or two when it crosses a 64-byte boundary.

A store failure during a job produces a sticky aborted completion with the
original cookie, backend error and zero data. Existing accepted transactions
retain their identity until drained or coordinated reset; the abort does not
permit reuse. New SUBMITs remain rejected after ACK until whole-design reset.
Counters from an aborted computation are not successful-operation evidence.
Reset/reloading the entire AFI is the only recovery mechanism; there is no
in-band reopen, partial reset or weight mutation path.

## Image loading and evidence

Set the DDR base/length and all eight expected SHA256 words while EMPTY.
Enter LOADING, copy the image, flush host write-combining buffers and perform
a same-device read. BEGIN_VERIFY closes admission and drains accepted writes.
Read back the whole allocation, verify its digest on the host, write all eight
readback digest words, then COMMIT_SEAL. READY and LOCKED are required before
execution. The digest comparison registers enforce the protocol; hardware does
not contain a SHA256 engine. The loader is a trusted host process.

The bridge rejects later BAR4 writes and counts them. Nonzero DDR responses,
bad IDs and malformed read termination fail the backend closed. An intentional
sealed-write rejection does not itself corrupt the store. The physical SDK
uses memory-mapped PCIe and cannot observe AXI BRESP/RRESP directly. Its
`resp: 0` means SDK completion only, explicitly marked in transport metadata.
The simulator returns real AXI response codes. Physical rejection evidence
requires the hardware rejection counter and unchanged readback, not SDK status.

The host runner requires a build manifest binding the executable, compiled
source hashes, tool version and execution backend. Physical manifests also
bind the DCP digest and expected AGFI. It checks identity, full image hashes
before sealing and after execution, epoch/lock persistence, exact per-case
results, completion cookies and line-read counts. Reports start incomplete,
retain partial failures and reject cleanup errors. Mock transport tests are
host-runtime tests only and cannot establish FPGA execution.

## Remaining integration work

Week 3 still needs CoralNPU memory-map wiring and firmware helpers, all layer
operators and native activation formats, scale/dequantization execution,
KV-cache management, scheduling and end-to-end token generation. Multi-client
throughput, larger-model formats, HBM and synthesis timing closure require their
own tests. No result in this slice demonstrates a 27B model or full Bonsai
inference.

| Deferred helper | Required Week 3 acceptance case |
| --- | --- |
| CoralNPU/RVV driver and address mapping | A simulated core submits jobs, handles stale epochs and failures, and matches the same host oracle |
| Scaling and dequantization | Preserve native scale bits; compare signed extremes, zeros and rounding boundaries with a frozen numerical reference |
| Matrix/layer operators and reductions | Compare full operator outputs with the pinned model reference, including tail dimensions and row/group boundaries |
| Normalization, activation and attention helpers | Freeze precision/tolerances before implementation; compare adversarial and actual activation tensors |
| KV-cache and prefill/decode scheduling | Compare cache contents and token-step outputs with the reference across context boundaries and reuse |
| Multi-client memory arbitration | Retain unique tags/epochs and eventual progress under saturation, response stalls, errors and coordinated reset |

These are pending acceptance cases, not claims that helper RTL or numerical
thresholds for full layers already exist.
