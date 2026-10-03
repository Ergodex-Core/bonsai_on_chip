# Native PQ2 engine comparison contract

This experiment preserves the existing E1 mailbox and native model format.
It is related to [ERG-104](https://linear.app/ergodex-ai/issue/ERG-104),
[ERG-108](https://linear.app/ergodex-ai/issue/ERG-108), and
[ERG-109](https://linear.app/ergodex-ai/issue/ERG-109).

## Frozen arithmetic

A PQ2 block is 34 bytes: little-endian FP16 scale, then 128 values in 32 bytes.
Two-bit codes are consumed least-significant pair first and decode as `code - 1`,
including code 3 = +2. Activations cover the complete signed INT8 range.
For every output, hardware returns four exact INT32 sums, one per 32 lanes.
Scale bits are returned unchanged; no floating-point operation occurs in the leaf.

The firmware sums the four integers, then applies each 128-lane block in ascending
order: FP32 factor = FP16 weight scale × FP32 Q8_K activation scale; FP32 term =
factor × INT32 dot; FP32 sum = sum + term. Two blocks share one Q8_K scale.
Build the FPGA-resident Coral firmware with `-ffp-contract=off -fno-fast-math`.
The older Q8_0 helper's subgroup scaling contract does not apply.
Host arithmetic is only an independent reference check, never inference execution.

## Interface version E1-compatible-v1

CPU AXI uses 32-bit addresses, 128-bit data, six-bit IDs and single-beat transfers.
The model is immutable at 0x40000000; engine registers begin at 0x60000000.
Weight offsets are file offsets including the GGUF header, never rebased silently.

| Register offset | Meaning |
| --- | --- |
| 0x000 | Status: busy bit 0, done bit 1, error bit 2 |
| 0x004 | Write 1 to start |
| 0x008 | Active outputs, 1 to 32 |
| 0x010 | Busy cycles of the most recent command |
| 0x100 + 4u | Native 34-byte weight-block file offset |
| 0x200 to 0x27f | 128 signed INT8 activations |
| 0x280 | Raw Q8_K FP32 scale bits |
| 0x400 + 16u + 4g | Exact INT32 subgroup sum |
| 0x600 + 4 floor(u/2) | Two packed raw FP16 scales |

The storage interface transfers aligned 128-bit read beats with valid/ready.
Responses must be ordered and held until consumed. The fetch experiment permits
multiple outstanding reads and drains them after an error. Reset must reset both
engine and memory adapter; no transaction may survive system reset.

## Naming and evidence levels

The architecture deck retains S0 = stock, R1 = ROM, E1 = serial32 and E2 = the
proposed full 32×DOT128 spatial design. This experiment uses descriptive labels:
`E1`, `E1-fetch-depthN`, and `spatial-LN`. Spatial-L is a parallel reduction tile,
not a neighbor-propagating systolic array. Its 32 outputs perform L products each
per compute cycle; the existing E1 has L=1. No full spatial32×DOT128 implementation
or paper throughput claim is implied.

Both source architectures are design references, not Bonsai numerical oracles.
The paper's model, batch behavior, FPGA and throughput differ. Compression remains
deferred until a full checkpoint histogram and a lossless +2 escape/fallback design
justify it. Synthetic +2 coverage remains mandatory even if a checkpoint lacks it.

Operator RTL, memory-service simulation, firmware-cycle measurements, synthesis,
post-route timing, actual board execution and complete token inference are distinct
gates. Target 250 MHz is a constraint, never measured frequency. Simulator cycles/s
is wall-time simulator speed, never FPGA clock or tokens/s. The shared tests use
same fixtures and injected storage latency/II; those profiles are assumptions,
not measured F2 HBM bandwidth. No main push or merge is authorized.
