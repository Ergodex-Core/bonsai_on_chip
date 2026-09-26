# ERG-102 physical FPGA bring-up

**PASS** on 2026-09-26, 11:09:52 to 11:10:29 UTC. The final formatted and reviewed
harness passed on a physical AWS F2 FPGA using Amazon's public
`cl_axil_reg_access` image. This closes the Week 0 shell/MMIO bring-up check.
It does not demonstrate CoralNPU/Bonsai integration or model inference.

| Check | Actual result |
| --- | --- |
| Official runtime additions | 2,002 passed: sum, 1,000 carry, 1,000 random, post-reset sum |
| Independent deterministic additions | 262 passed |
| Independent assertions | 2,144 passed, including two 8-assertion reset probes |
| Register loopback and completion | Passed operand readback, start/ready, result acknowledgement |
| Read-only and invalid addresses | Writes preserved sum/carry; three invalid addresses returned `0xDEADBEEF` |
| Reset and recovery | Nonzero operand state cleared by AFI clear/reload; subsequent addition passed |
| Command exit codes | All 15 captured phases returned 0 |
| Final shell health | Reported timeout, protocol and range-error flags/counts were zero |

The invalid-address result is a sentinel response, not an AXI SLVERR/DECERR
validation. Reset used reconfiguration, not a separate warm-reset register.
Final health counters were sampled after reset and are not cumulative across it.

The image was `agfi-06447dea0ca9b0a39` / `afi-04454b01e29b26073`, with shell
`0x10212415` and PCI identity `1d0f:f006`. The source/software checkout was
`b603a81f65666e0cf7a67ee5cf18b148eb6b08c3`; its notebook names this public image.
The isolated SDK build used SDK 2.3.4 and GCC 13.3.0. Committed sources were
exported with `git archive`, and source/binary hashes were verified before the
physical run. The documented shell clock is `clk_main_a0` at 250 MHz; frequency
was not independently measured.

Amazon's prebuilt AFI does not provide an exact bitstream build commit, DCP hash,
or timing/utilization report through its API metadata. No synthesis or bitstream
build was performed here. DDR/HBM, DMA and model token generation were not tested.

See [the machine-readable report](f2-physical.json) for source/binary hashes,
per-phase exit codes and original log hashes. The unchanged
[assertion trace](physical-raw/07-probe.log),
[reset trace](physical-raw/11-reset-reload.log), and
[final device status](physical-raw/13-final.log) are attached with the other raw
logs. Reproduce with the [physical harness](../../fpga/aws_f2/physical/README.md).

The advisory reservation was released. The instance was left running because
it had been started outside this task, and the official image remains loaded.
