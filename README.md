# Coral NPU

Coral NPU is a hardware accelerator for ML inferencing. Coral NPU is an Open
Source IP designed by Google Research and is freely available for integration
into ultra-low-power System-on-Chips (SoCs) targeting wearable devices such as
hearables, augmented reality (AR) glasses and smart watches.

Coral NPU is a neural processing unit (NPU), also known as an AI accelerator or
deep-learning processor. Coral NPU is based on the 32-bit RISC-V Instruction Set
Architecture (ISA).

Coral NPU includes three distinct processor components that work together:
matrix, vector (SIMD), and scalar.

![Coral NPU Archicture](doc/images/arch_data_flow.png)
[Coral NPU Architecture Datasheet](https://developers.google.com/coral/guides/hardware/datasheet)

## Coral NPU Features

Coral NPU offers the following top-level feature set:

* RV32IMF_Zve32x RISC-V instruction set (specifically `rv32imf_zve32x_zicsr_zifencei_zbb`)
* 32-bit address space for applications and operating system kernels
* Four-stage processor, in-order dispatch, out-of-order retire
* Four-way scalar, two-way vector dispatch
* 128-bit SIMD, 256-bit (future) pipeline
* 8 KB ITCM memory (tightly-coupled memory for instructions)
* 32 KB DTCM memory (tightly-coupled memory for data)
* Both memories are single-cycle-latency SRAM, more efficient than cache memory
* AXI4 bus interfaces, functioning as both manager and subordinate, to interact
  with external memory and allow external CPUs to configure Coral NPU

## System Requirements

* Bazel 8.6.0
* Python 3.9-3.13

See [coralnpu.dockerfile](utils/coralnpu.dockerfile) for a detailed list of
requirements.  Our CI systems run most builds and tests using this image.

## Verification & Testing

For details on our testing methodologies and how to run or write tests, see the
corresponding test READMEs:

* [Cocotb Tests (RTL & Netlist simulation)](tests/cocotb/README.md)
* [UVM Testbench (Co-simulation)](tests/uvm/README.md)

## Quick Start

```bash
# Ensure that test suite passes
bazel run //tests/cocotb:core_mini_axi_sim_cocotb

# Build a binary
bazel build //examples:coralnpu_v2_hello_world_add_floats

# Build the Simulator (non-RVV for shorter build time):
bazel build //tests/verilator_sim:core_mini_axi_sim

# Run the binary on the simulator:
bazel-bin/tests/verilator_sim/core_mini_axi_sim --binary bazel-out/k8-fastbuild-ST-dd8dc713f32d/bin/examples/coralnpu_v2_hello_world_add_floats.elf
```

![](doc/images/Coral_Logo_200px-2x.png)

## Bonsai development and simulation

Development is tracked in [ERG-102](https://linear.app/ergodex-ai/issue/ERG-102/week-0-finalize-interface-spec-and-fpga-setup).
Use a Linux x86_64 environment for the Bazel and FPGA toolchains. Include the
Linear issue identifier, such as `ERG-102`, in PR titles and relevant commits;
repository autolinks point these references to the matching issue. Link the PR
and validation report from Linear and follow the PR evidence template.

```bash
# Capture every declared test target, including excluded simulator variants.
utils/run_simulation_baseline.sh inventory
# Run the documented core cocotb smoke suite with the existing backend.
utils/run_simulation_baseline.sh smoke
# Run the repository-default test baseline in parallel/exclusive groups.
utils/run_simulation_baseline.sh all
```

The baseline uses the repository's existing Verilator/Chisel/host backends. It
records explicit exclusions, Bazel events, exit codes, source hashes, test logs
and XML outputs under `reports/ERG-102/raw/`. Set `SIM_BUILD_JOBS` and `SIM_TEST_JOBS` to bound local
resource use. The full repository does not yet have an Arcilator test backend.
See the [Arcilator parity pilot](tests/arcilator/README.md) for the initial checked
RTL boundary and its separate toolchain requirements.

The existing Nexus-board instructions are in `fpga/README.md`. For an AWS FPGA
Developer AMI, use the separate [AWS F2 simulation setup](fpga/aws_f2/README.md).
AWS shell-model simulation and execution of a loaded FPGA image are distinct
acceptance gates; a simulator pass is not a hardware execution result.
