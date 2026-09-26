# ERG-102 local simulator smoke

**PASS for the selected CoreMiniAxi Verilator target.** The uncached execution exercised 26 passing cocotb cases. Three RVV cases returned early because the selected core has no RVV support; those are not counted as exercised coverage.

| Measurement | Result |
| --- | --- |
| Target | `//tests/cocotb:core_mini_axi_sim_cocotb` |
| Backend | Verilator 5.052, cocotb 2.0.0, Bazel 8.6.0 |
| Environment | Debian 13.7 / Linux x86_64 Docker container on Darwin ARM64 |
| Initial run | 2026-09-26 08:17:33 UTC; test began 08:54:39.097 UTC |
| Initial Bazel result | 1 passed target, 0 cached; exit 0 |
| Test wall time | 132.214 seconds; complete Bazel invocation 2355.248 seconds |
| Raw cocotb summary | 29 PASS, 0 FAIL, 0 SKIP |
| Supplied cocotb random seed | 42 |
| Coverage-adjusted result | 26 exercised passes; 3 RVV early returns |
| Bazel XML | 1 wrapper testcase; this is not the cocotb case count |
| Full inventory | 2758 test rules; this smoke selects only 1 explicit manual target |

The three early-return cases are `rvv_exceptions_test`, `rvv_frm_hazard_test`, and `rvv_misa_test`. Each logs “Skipping ... on non-RVV core” and returns; cocotb still labels it PASS.

## Source and evidence

The execution used a dirty working tree based on `5eff3822250fc52b2a0f83315969238805ece7c5`, including the `Sram.scala` → `SramBlock.scala` rename and BUILD reference needed on a case-insensitive host, `.gitignore`, and the baseline runner. It does not establish a clean final PR commit result. See the preserved original manifest (`raw/local-smoke/original-run/manifest.txt`) and source patch (`raw/local-smoke/original-run/source.patch`).

The original summary (`raw/local-smoke/original-run/summary.original.json`), Bazel console (`raw/local-smoke/original-run/smoke.log`), simulator log (`raw/local-smoke/original-run/test.log`), XML (`raw/local-smoke/original-run/test.xml`), and BEP are retained unchanged. The older runner did not record tee's exit status. Running the final collector against this evidence exits 1 for the missing `smoke.logging-exitcode`; that value remains unknown. The original run also predates the complete source-file hash manifest.

The final runner (`b10a2bbb462380cce7f0b1a41e07fb55635574cbfc6026d10d0abfb1a14e61ba`) replayed the target from cache at 09:08:59 UTC. Bazel and tee both exited 0, the collector marked evidence complete, and all expected BEP events and test artifacts were present. It took 27.260 seconds and **executed zero new tests**. Preserved test.log and test.xml hashes exactly match the uncached execution. The final collector summary (`raw/local-smoke/final-run/summary.json`) and full source-file hashes (`raw/local-smoke/final-run/source-files.sha256`) describe this replay. An intermediate old-runner cache replay is retained separately for audit and adds no coverage.

| Preserved artifact | SHA-256 |
| --- | --- |
| test.log | `8bb1879560ffe3ad4cff446997b5dd8a97759cb0a2e6497d3b2f9ca1c34260e1` |
| test.xml | `460ae57a09e47b83ba52650837439dcc63ef777dcdd76c7a3aafea6c375b9f69` |
| Initial runner | `dd3c0df5cfbaf9442192e848d4dfb42ef3266f4c1a3d3b6e6704746b6be3c709` |

[Machine-readable summary](smoke-baseline.json) records individual exercised cases and further hashes. SHA256SUMS (`raw/local-smoke/SHA256SUMS`) covers the local raw evidence. Raw files are gitignored and retained locally; no external upload is claimed.

This result covers one Verilator smoke target. It establishes no Arcilator, complete repository suite, AWS F2 XSIM, or physical FPGA pass; those require separate evidence.
