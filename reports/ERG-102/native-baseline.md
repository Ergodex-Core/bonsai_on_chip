# ERG-102 native simulator baseline

**Result: all 1,332 selected Bazel targets passed**, with complete evidence for both execution groups. This was the repository-default Verilator/Chisel/host baseline running on the CPU of an AWS F2 host. It establishes no full-suite Arcilator result, physical FPGA execution, or language-model inference.

The final run lasted **39 minutes 26 seconds**, from **2026-09-26 09:21:20 UTC** to **10:00:46 UTC**, on Ubuntu 24.04 with 24 vCPUs and 249 GiB RAM. It used Bazel 8.6.0, Clang 19.1.1, repository-built Verilator 5.052, Java 21, 16 build jobs, eight test jobs, and a 16 GiB Bazel JVM heap.

| Group | Selected | Passed | Cached | Fresh |
| --- | ---: | ---: | ---: | ---: |
| Parallel | 1,325 | 1,325 | 286 | 1,039 |
| Exclusive | 7 | 7 | 0 | 7 |
| Total | 1,332 | 1,332 | 286 | 1,046 |

The inventory contained 2,758 test rules. Selection follows the existing baseline convention: exclude `manual`, `vcs`, `synthesis`, `power`, and `spyglass`. Bazel and log-capture exits were zero for both groups. Both Build Event Protocol streams had their terminal event and all announced events; there were no missing targets, parse errors, aborted events, or artifact errors. All **6,652 copied artifact records** were rehashed with zero mismatches.

## Coverage and limitations

The 1,332 test XML files declare 1,400 testcases, with zero XML failures, errors, or framework-reported skips. These are framework declarations; they do not prove that every nested test body ran.

- `//fpga:check_pins_test` explicitly disabled DDR pin validation. Its pass does not establish correct DDR pin mappings.
- Clock, DMA, SPI-flash, and high-memory trivial-pass simulations used backdoor segment loading. Their logs intentionally skip host segment writes in `csr_only` mode; those cases do not establish host streaming of those segments.
- The DMA `cfg_error` log describes testing unsupported configurations, rather than a skipped test.
- The separate manual CoreMiniAxi aggregate smoke run has three RVV early-return cases, as documented in [the smoke report](smoke-baseline.md). That aggregate is excluded here; this full run selects split scalar and RVV-enabled targets.

All seven exclusive targets passed:

| Target under `//fpga:` | Declared size | Bazel timeout | Internal simulation limit |
| --- | --- | --- | ---: |
| `clk_sim_test` | large | long | 30 s |
| `dma_sim_test` | large | long | 120 s |
| `spi_flash_sim_test` | large | long | 600 s |
| `trivial_pass_highmem_sim_test` | large | long | 30 s |
| `trivial_pass_sim_test` | large | long | 30 s |
| `rom_boot_sim_test` | not explicitly set | long | 600 s |
| `rom_boot_highmem_sim_test` | not explicitly set | long | 600 s |

## Source and reproduction

Execution used base `5eff3822250fc52b2a0f83315969238805ece7c5` plus the recorded source patch and final runner. The case-collision fix renames `Sram.scala` to `SramBlock.scala` without changing its contents and updates the BUILD reference. The final runner came from published commit `bd81cb5198f729ac9325d0f886d46c902a4a9d65`; its SHA256 is `b10a2bbb462380cce7f0b1a41e07fb55635574cbfc6026d10d0abfb1a14e61ba`. Source hashes and patch were unchanged after the run. This is base-plus-patch evidence, rather than a claimed checkout of the PR head.

From a matching checkout on the retained host:

```bash
source /home/ubuntu/bonsai-erg102-tools/activate.sh
SIM_BUILD_JOBS=16 SIM_TEST_JOBS=8 BAZEL_JVM_HEAP=16g \
  bash utils/run_simulation_baseline.sh all /path/to/new-result-directory
```

The earlier eight-build/two-test-job attempt was deliberately interrupted after 286 passing target summaries; it remains **incomplete** and is archived separately. The final run reused those cached results and completed all remaining targets.

## Preserved evidence

The EBS archive contains the source snapshot/provenance, inventory, commands, logs, BEP streams, copied test outputs, hashes, and coverage audit:

`/home/ubuntu/bonsai-erg102-evidence/native-baseline-20260926T0920Z-all-16x8.tar.gz`

Size: **3,707,813 bytes**. SHA256: `c370de156107062c4f012a9b880c8cc6e666d10c74ed14282d16c4f41e121d5f`.

Gzip integrity passed. The scoped Bazel server was shut down, and no native baseline processes remained. The instance remains running. No raw evidence was uploaded externally. [Machine-readable results](native-baseline.json) include detailed provenance, individual evidence hashes, coverage notes, and the interrupted-attempt archive.
