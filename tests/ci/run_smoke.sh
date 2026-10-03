#!/usr/bin/env bash
set -euo pipefail
backend=${1:?backend required}
repo=${2:?repository required}
output=${3:?fresh output directory required}
tools=${4:?toolchain installation root required}
[[ ! -e "${output}" ]] || { echo 'Refusing existing evidence directory' >&2; exit 2; }
mkdir -p "${output}"
output=$(cd "${output}" && pwd)
repo=$(cd "${repo}" && pwd)
tools=$(cd "${tools}" && pwd)
report_exit() {
  local status=$1
  printf "%s\n" "${status}" > "${output}/exit-code.txt"
  if (( status != 0 )); then
    echo "FAIL ${backend}; compiler logs:" >&2
    find "${output}" -name "*.log" -exec tail -n 30 {} \; >&2
  fi
}
trap 'report_exit "$?"' EXIT
export PATH="${tools}/verilator/bin:${PATH}"
if [[ -d "${tools}/verilator/share/verilator" ]]; then
  export VERILATOR_ROOT="${tools}/verilator/share/verilator"
else
  export VERILATOR_ROOT="${tools}/verilator"
fi
clang++ --version > "${output}/host-compiler.txt"
python3 --version > "${output}/python-version.txt"
python3 "${repo}/tests/ci/evidence.py" start "${backend}" "${repo}" "${output}" "${tools}"
sources=("${repo}/tests/ci/arithmetic_top.sv" "${repo}/hdl/verilog/rvv/common/adder.sv"
         "${repo}/hdl/verilog/rvv/common/barrel_shifter.sv" "${repo}/hdl/verilog/rvv/common/compressor_3to2.sv")
case "${backend}" in
  verilator)
    timeout --kill-after=10s 300 verilator --cc --exe --build -j 2 --top-module arithmetic_top \
      --prefix Vdut --Mdir "${output}/obj" -CFLAGS '-std=c++17' "${sources[@]}" \
      "${repo}/tests/ci/arithmetic.cpp" > "${output}/build.log" 2>&1
    timeout --kill-after=10s 60 "${output}/obj/Vdut" "${output}/trace.csv" | tee "${output}/result.txt"
    timeout --kill-after=10s 300 bash "${repo}/tests/arcilator/run_parity.sh" verilator "${repo}" "${output}/handshake" \
      2>&1 | tee "${output}/handshake.log"
    ;;
  arcilator)
    arc="${tools}/circt"
    timeout --kill-after=10s 120 "${arc}/bin/circt-verilog" --ir-hw --sroa --single-unit --top=arithmetic_top \
      "${sources[@]}" -o "${output}/design.mlir" > "${output}/frontend.log" 2>&1
    timeout --kill-after=10s 120 "${arc}/bin/arcilator" --no-runtime --no-generate-driver --emit-llvm --state-file="${output}/state.json" \
      "${output}/design.mlir" -o "${output}/model.ll" > "${output}/compile.log" 2>&1
    timeout --kill-after=10s 120 "${arc}/bin/opt" --strip-debug -O2 -S "${output}/model.ll" -o "${output}/optimized.ll" > "${output}/opt.log" 2>&1
    timeout --kill-after=10s 120 "${arc}/bin/llc" -O2 -filetype=obj "${output}/optimized.ll" -o "${output}/model.o" > "${output}/llc.log" 2>&1
    python3 "${arc}/runtime/arcilator-header-cpp.py" "${output}/state.json" > "${output}/arithmetic_top.h"
    timeout --kill-after=10s 120 clang++ -O2 -std=c++17 -DUSE_ARC -no-pie -I"${output}" -I"${arc}/runtime" \
      "${repo}/tests/ci/arithmetic.cpp" "${output}/model.o" -o "${output}/arithmetic" > "${output}/link.log" 2>&1
    timeout --kill-after=10s 60 "${output}/arithmetic" "${output}/trace.csv" | tee "${output}/result.txt"
    ;;
  *) echo "Unknown backend: ${backend}" >&2; exit 2 ;;
esac
python3 "${repo}/tests/ci/evidence.py" finish "${backend}" "${repo}" "${output}" "${tools}"
