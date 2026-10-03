#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
build=${1:?provide an isolated build directory}
mkdir -p "$build"
build=$(cd "$build" && pwd)
verilator --cc --exe --build -j 2 --top-module leaf -Wno-fatal --Mdir "$build/obj" "$here/leaf.sv" "$here/check.cpp"
"$build/obj/Vleaf"
iverilog -g2012 -s four_state_tb -o "$build/four_state.vvp" "$here/leaf_flattened.sv" "$here/four_state_tb.sv"
vvp "$build/four_state.vvp"
