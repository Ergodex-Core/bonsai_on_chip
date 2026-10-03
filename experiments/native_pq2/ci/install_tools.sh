#!/usr/bin/env bash
# Linux x86-64 only. Tool pins also identify the Actions cache.
set -euo pipefail
backend=${1:?verilator or arcilator}
root=${2:?external installation directory}
mkdir -p "${root}"
root=$(cd "${root}" && pwd)
download() {
  local url=$1 sha=$2 destination=$3
  curl --fail --location --retry 3 --connect-timeout 30 --max-time 600 --silent --show-error "${url}" -o "${destination}"
  echo "${sha}  ${destination}" | sha256sum --check --strict
}
case "${backend}" in
  verilator)
    if [[ ! -x "${root}/verilator/bin/verilator" ]]; then
      archive="${root}/verilator.tar.gz"
      download https://github.com/verilator/verilator/archive/refs/tags/v5.052.tar.gz \
        8c8d2e11e6ad32f641dd250742a94195ddecb912e2e2dabe2f42ddbbb99c1092 "${archive}"
      mkdir -p "${root}/verilator-source"
      tar -xzf "${archive}" --strip-components=1 -C "${root}/verilator-source"
      (cd "${root}/verilator-source"; autoconf; ./configure --prefix="${root}/verilator"; make -j2; make install)
      rm -rf "${root}/verilator-source" "${archive}"
    fi
    "${root}/verilator/bin/verilator" --version | grep -E '^Verilator v?5[.]052 '
    ;;
  arcilator)
    if [[ ! -x "${root}/circt/bin/arcilator" || ! -x "${root}/circt/bin/circt-opt" || ! -x "${root}/circt/bin/mlir-translate" ]]; then
      archive="${root}/circt.tar.gz"
      download https://github.com/llvm/circt/releases/download/firtool-1.161.0/circt-full-static-linux-x64.tar.gz \
        a844279a0e3eeb598e00957cbe8cd3d61abf0bf9dc6d3ec582192ec913ea7f18 "${archive}"
      mkdir -p "${root}/circt"
      # Keep the compiler tools; model uses the generated public eval API.
      tar -xzf "${archive}" --strip-components=1 -C "${root}/circt" \
        firtool-1.161.0/bin/arcilator firtool-1.161.0/bin/circt-verilog firtool-1.161.0/bin/circt-opt firtool-1.161.0/bin/mlir-translate firtool-1.161.0/bin/opt firtool-1.161.0/bin/llc
      rm "${archive}"
    fi
    mkdir -p "${root}/circt/runtime"
    upstream=https://raw.githubusercontent.com/llvm/circt/0d63c41c9121106b01372aca2b60eb44ed4a6e87/tools/arcilator
    download "${upstream}/arcilator-runtime.h" \
      52e4e00fd5a949c2e00e19e747054492ea9163dcb577fb095f0b9eea3e42f952 "${root}/circt/runtime/arcilator-runtime.h"
    download "${upstream}/arcilator-header-cpp.py" \
      aea73f31cb8786f4af09dea014e044a3061455d63eecb9d1754c15ebfae38a24 "${root}/circt/runtime/arcilator-header-cpp.py"
    # Source-build the bounded process-loop compatibility pass against the
    # official native shared development package, never a private binary.
    dev="${root}/circt-dev"
    if [[ ! -f "${dev}/include/circt/Dialect/LLHD/LLHDOps.h" ]]; then
      archive="${root}/circt-dev.tar.gz"
      download https://github.com/llvm/circt/releases/download/firtool-1.161.0/circt-full-shared-linux-x64.tar.gz \
        b9ae9472d8cd6c67f6508807a6d03bb9e1917bee3227ed4ee0732008dd12afb0 "${archive}"
      mkdir -p "${dev}"
      tar -xzf "${archive}" --strip-components=1 -C "${dev}"
      rm "${archive}"
    fi
    export LD_LIBRARY_PATH="${dev}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    if [[ -n "${GITHUB_ENV:-}" ]]; then
      echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH}" >> "${GITHUB_ENV}"
    fi
    source_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    source_sha=$(sha256sum "${source_dir}/unroll_processes.cpp" | cut -d ' ' -f1)
    saved_sha=$(cat "${root}/circt/unroll-source.sha256" 2>/dev/null || true)
    if [[ ! -x "${root}/circt/bin/native-pq2-unroll" || "${saved_sha}" != "${source_sha}" ]]; then
      clang++-18 -std=c++17 -O2 -fno-rtti -I"${dev}/include" \
        "${source_dir}/unroll_processes.cpp" -L"${dev}/lib" \
        -Wl,--copy-dt-needed-entries -lMLIRSideEffectInterfaces -lCIRCTLLHD -lCIRCTComb -lCIRCTHW -lCIRCTSeq -lCIRCTSV -lCIRCTSim \
        -lCIRCTSupport -lMLIRControlFlowDialect -lMLIRArithDialect \
        -lMLIRFuncDialect -lMLIRSCFDialect -lMLIRParser -lMLIRPass \
        -lMLIRAnalysis -lMLIRIR -lMLIRSupport -lLLVMSupport \
        -Wl,-rpath,"${dev}/lib" -o "${root}/circt/bin/native-pq2-unroll"
      echo "${source_sha}" > "${root}/circt/unroll-source.sha256"
    fi
    "${root}/circt/bin/arcilator" --version
    ;;
  *) echo "Unknown backend: ${backend}" >&2; exit 2 ;;
esac
