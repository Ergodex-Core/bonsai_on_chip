# Independent automated review: HBM ROM contract

Review date: 2026-10-05. Reviewer: Codex `source_audit`, a separate automated
agent from the production RTL, HBM attachment, host runtime and memory-model
authors. The reviewer authored only the earlier PCIS bridge regression additions
in `fpga/aws_f2/first_slice/f2_memory_bridge_tb.sv` and this review report; no
production DUT source was authored or edited by this reviewer. This is not
human approval, accountable-owner acceptance, permission to merge, or evidence
that ERG-103 is complete. Accountable issue owner remains Rachit Tibrewal.

Decision: **no remaining blocking source or CPU evidence finding in the frozen
snapshot below.** The final HBM simulation and independent DDR/HBM comparison
pass. Vendor HBM simulation, physical CDC/timing/DRC and FPGA execution remain
pending. This decision supports a draft PR, not hardware acceptance or merging.

## Reviewed source and provenance

The candidate is based on first-slice PR #2 head
`fc8ff1b5b2e67c459fe8f61bb192c8d010b1216e`; published main is
`26bded5bf809267506058df6bf0b296dddeeaf25`. Published branches and all ten PR
heads were inspected before implementation. No complete native HBM attachment
was found there. PR #7 provides a native-PQ2 read queue, while PR #10 explicitly
excludes its separately held native F2/CDC RTL. Neither was assumed to provide
an implemented HBM seal. The design-only proposal `aa69172d` is provenance,
not implementation evidence.

These hashes identify the reviewed production RTL and CDC constraints, independent
of a later commit ID. A changed file requires renewed review of its delta.

| File | SHA-256 |
| --- | --- |
| `hdl/verilog/first_slice/hbm_cdc_mailbox.sv` | `24b5c62f7a8df23968dd18d7a090aff59ac4c1d0a1491c8a547a18ae50047cdf` |
| `hdl/verilog/first_slice/hbm_line_bridge.sv` | `28401f9411e0a73c4d50cf81c8060aceafc0694269ba1c26857a81a608c81432` |
| `hdl/verilog/first_slice/f2_memory_bridge.sv` | `175f28d15bbfe59c0b1b589451cbd0ab493111cf89e5700924383dd523c33fdf` |
| `hdl/verilog/first_slice/weight_store.sv` | `2d9595bb1bb919a356faf593baf6438f03017493728f290f1a585c25b6af8dc0` |
| `hdl/verilog/first_slice/first_slice_top.sv` | `b9e661003b2930fefb5116c18e754810a234918319957d6ef97595a284b41952` |
| `hdl/verilog/first_slice/dot128.sv` | `cc4b54cf467d0c81d146b3c2c5997d44bd121c3d9aab04014ec307c8be58b8d5` |
| `fpga/aws_f2/hbm_rom/cl_bonsai_hbm_rom.sv` | `21bf27e36b669a069095e99c4ae002f069db2a7e48e2bf24d7c2fa6c56b26cd6` |
| `fpga/aws_f2/hbm_rom/hbm_fixed_clock.sv` | `0e025f62a3db1b813052a71449fa484cdd99dad00f9585f38462325aaab9e51f` |
| `fpga/aws_f2/hbm_rom/hbm_rom_controller.sv` | `dafb4ff52e4974d39a4689d297f91593b2f01b618de2fb1178e25604777c6560` |
| `fpga/aws_f2/hbm_rom/cl_timing_user.xdc` | `704008af270455e17cc624ab114aa5a72dfabd69b18bbd49e4a3940607417a68` |

The native attachment source audit was independently rerun against AWS FPGA
`b603a81f65666e0cf7a67ee5cf18b148eb6b08c3` and IP resource
`6d32be972e6da854e61a8d3d6ec0466ab491c1b3`: all 1,102 HBM and 22 MMCM ports
match the pinned vendor templates. The HBM XCI SHA-256 is
`1f8994d1ba76dc3bd81168de89b8a985fb3ab46602e60c12c25ef347231b0ac9`;
the clock XCI SHA-256 is
`3f236453d1655985abcb33701fa48d2910630bd20b38cda1eb508e4bb05a11c8`.
This checks source/configuration binding, not elaboration of the vendor models.

## Contract and architecture findings

- The shared frontend preserves 64-byte lines, 48-bit logical offsets, 8-bit
  tags, 32-bit epochs, explicit errors and sixteen logical credits. The new
  bridge has one native read and one native write transaction in flight.
- Native addresses are formed as `(15 << 29) | local_offset`. Full 64-bit
  incoming bounds and line attributes are checked before narrowing to 29 local
  bits. The reserved region is 512 MiB, while the logical image remains limited
  to 256 MiB. Two 32-byte AXI3 beats form one logical line.
- Port 15 is the sole connected AXI writer. All other native AWVALID/WVALID
  ports are constant zero. PCIS reaches that port through the existing loader
  gate; PCIM, secondary/test/scrub masters and performance muxes are absent.
  HBM management writes and MMCM AXI-Lite write controls are tied off. No
  writable channel-map/reset/clock alias was identified.
- Load, close admission, drain, whole-image host readback/hash and seal remain
  ordered. The digest is an explicitly trusted host handshake, not a hardware
  hash engine. The allocation and expected digest cannot change after loading
  begins. Mutable mailbox activations/results are separate from HBM weights;
  no KV-cache or full CoralNPU firmware implementation is claimed.
- Ordinary admission closure preserves an already accepted burst. A malformed
  beat, write-response error or backend readiness loss suppresses remaining
  writes and yields failure. Readiness loss during LOADING, VERIFYING or use
  permanently faults the image until coordinated reset, reload, verification
  and reseal.
- Main-domain transaction accounting survives native HBM clock/reset loss.
  Pending source reads/writes receive zero-data/error completion even when the
  HBM clock stops; already presented responses remain stable. Late physical
  responses are drained/discarded, and fatal state prevents ID reuse until
  whole-store reset. Relock does not restore the old seal.
- The fixed clock IP is configured for 100 MHz input and 450 MHz output; main
  remains 250 MHz. An eight-stage native reset release stretches a lock-loss
  indication long enough for the main-domain sampler. These are source-level
  configuration targets, not achieved frequency or measured reset timing.
- Mailbox data stays stable until its acknowledgement crosses back. Constraints
  bound payload paths to 2 ns and apply narrow first-stage synchronizer
  exceptions. Physical placement, reset recovery/removal and complete CDC
  findings still require actual vendor implementation review.

## Findings addressed during review

| Finding | Resolution and verification |
| --- | --- |
| Inherited PCIS bridge continued later burst writes after malformed WLAST/strobes or a DDR/HBM B error | Per-beat checks now use the admitted permission, sticky burst error and current backend readiness. New regression fails against the original published bridge and passes the candidate, including drain after ordinary admission closure. |
| Native HBM reset could flush bridge accounting while upstream PCIS still awaited completion | Bridge reset is restricted to the coordinated global boundary. Native readiness/clock loss latches a main-domain fatal condition and completes unfinished source commands without relying on the stopped HBM clock. |
| A clean response concurrent with an error on the other native channel could be marked successful | Same-cycle `response_fault` now qualifies native completion success. Already presented responses remain unchanged. |
| Readiness/fault synchronizer first stages were missing narrow timing exceptions | Exact first-stage constraints were added; broad asynchronous clock exceptions do not replace bundled-data limits. Physical exception coverage is a pending vendor gate. |
| Native/synthetic reproduction steps incorrectly required unittest output | The runner now checks the actual completed suite reports and exact 396/6 case inventories. It also retains hash-verified tested-source snapshots. |
| Simulation latency descriptions omitted one observable native clock cycle | The final complete run uses corrected metadata labels (6–24 initial-read, 3–9 inter-beat and 4–20 write-response HBM cycles). The initial run remains separate provenance; model behavior did not change. |

## Evidence review

The final run, `full-validation-02`, completed at 2026-10-05 16:35:45 UTC.
The reviewer independently recomputed all 19 source hashes against both live
files and saved tested-source copies, all 13 step log hashes, both suite report
hashes, the fixture hash and the actual complete readback files. All step exit
codes are zero. The copied repository validation, logs and suite reports were
also compared byte-for-byte by SHA-256 with the original run artifacts.

| Evidence | SHA-256 |
| --- | --- |
| [Final HBM validation](evidence/hbm/validation.json) | `ad62200a0d0e4a37bd6603ac02aaa8480b80e70d9c3b4aa60aa8cee8d4205592` |
| [Native Q1_0 report](evidence/hbm/native-report.json) | `b2fc4f572d27ef99254ac71b7fb88c6c88312daa9ac45de5267e116e3f926e19` |
| [Synthetic ternary report](evidence/hbm/synthetic-report.json) | `21be6f1cc091e03096f9e36cb8c8dd20a1e5f3b8acaca5441967a8610fed7b01` |
| [DDR/HBM comparison](evidence/backend-comparison.json) | `32e53311c2705925934afef5a0418116dc29646c57a4ac0d24659d5aed5bcaf8` |
| `tests/hbm_rom/compare_backends.py` | `7ed0b94a076d661da6c87009c8167a493839a2070fa36b7a242ced8a16cc067c` |

Observed final gates: 270 HBM bridge assertions, 218 PCIS bridge assertions,
11 store scenarios, nine integrated contract tests at each of two clock ratios,
and four transport protocol tests. All 396 actual Q1_0 cases and six separate
synthetic cases pass, including two expected illegal-code rejections. The real
cases compare exact int32 accumulation, raw FP16 scale bits and one disclosed
host FP32 multiply against the pinned independent fixture. For example,
`q1_000` at offset 8192 returns dot 899, raw scale `0x26f0` and host FP32
little-endian bytes `80e6c241`, using 157 main-domain cycles and one line read.
Q1_0 cases use unchanged 18-byte groups with ±1 weights; synthetic zeros and
illegal encodings are a separate two-bit format. Neither is full-model proof.

Both actual 242,357,184-byte native readbacks, before and after arithmetic and
sealed-write rejection, hash to
`ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99`.
Both 192-byte synthetic readbacks hash to
`966088f726e00ad969b7c7bc5ec64e9a2eee1e6734660f47fb2e222fb121ae36`.
The fixture hashes to
`1fa4d0b6a9a8ea1baf43e9aad5fb60cbba407dcb6f33adc69d8a613d41f9b066`.
The model payloads and readback binaries remain outside Git; the reports retain
their sizes and hashes. HBM hardware identity remains backend 3 before and
after each suite, with seal-write rejection recorded and no FPGA scale stage.

The reviewer read `compare_backends.py` and independently reran it against the
actual DDR and HBM result directories using this command:

```sh
python3 tests/hbm_rom/compare_backends.py \
  --ddr /workspace/hbm-validation/ddr-regression-final \
  --hbm /workspace/hbm-data/full-validation-02 \
  --out /tmp/hbm-independent-backend-comparison-02.json --self-test
```

It passes 402 exact case comparisons and ten mutations that must be rejected.
It hashes both actual readbacks and transport binaries for each backend and
checks the strict case inventory, reference/source pins, backend identity,
int32 results, raw scales, FP32 bytes and line-request counts. The independent
repeat has identical input hashes, source hashes, results and mutation outcomes
to the copied comparison receipt; its SHA-256 is
`da43e47dcf627b2ee6ed066250fe2143ebcbd555dbc0efa8c3ac09701d25042f`.
Its timestamp, command and output path differ, so the complete receipts differ.

| Suite/backend | Cases | Total core cycles | Total stall cycles | Logical line reads |
| --- | ---: | ---: | ---: | ---: |
| Native DDR | 396 | 56,695 | 4,815 | 398 |
| Native HBM | 396 | 61,273 | 9,393 | 398 |
| Synthetic DDR | 6 | 719 | 60 | 6 |
| Synthetic HBM | 6 | 800 | 141 | 6 |

These are per-operation mailbox counters from separate CPU memory models,
not hardware performance. Native HBM operations span 148–187 main cycles and
17–54 stall cycles. The normal core/HBM simulated period ratio is 18:10;
the alternate contract run uses 4:10 with a nonzero phase.

An additional independent probe accepted both source and native AR, stopped the
HBM clock, lowered readiness, and observed a zero-data SLVERR using the main
clock alone. It was rerun against the frozen bridge/mailbox hashes above.
Its log SHA-256 is
`bb250656579523a9c2573376f64a78941a2f74815a6b047ec27628501d1c4472`.
The probe is supplemental review evidence; committed regressions cover the same
failure class with both read and write traffic, late responses and held replies.
The final native binding/configuration audit was also independently rerun,
including declaration-based width lint; it is not a vendor behavioral test.
The original validation receipt is retained separately under `evidence/hbm-initial`
to document the corrected timing-description labels.

## Remaining gates and limitations

No new HBM vendor-model XSIM run, synthesis, utilization report, routed timing,
DRC/CDC sign-off, DCP/AFI/AGFI or physical execution was performed by this review.
The memory model is explicit CPU AXI timing/backpressure simulation and does not
model metastability, PHY calibration or calibrated hardware performance.
Pinned IP disables user parity, ECC correction and scrubbing; no protection
claim is inferred from ECC-bypass configuration. FLR is explicitly unsupported
in this candidate and tied off as in the vendor unused template; recovery uses
coordinated shell reset/reconfiguration. Warm CoralNPU reset and mutable KV
integration are outside this first-slice host-bus-master implementation.

Actual hardware acceptance must bind the image, source, vendor tools,
configuration and device image; demonstrate full loading/readback/hash/seal,
write rejection, resets/errors and unchanged post-run hash; and receive
accountable review and merge. The issue remains open. No human approval,
hardware allocation or external publication is implied by this automated review.
