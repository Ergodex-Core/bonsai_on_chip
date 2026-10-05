#!/usr/bin/env bash
# Compile only; never opens an FPGA or changes a cloud resource.
set -euo pipefail
[[ $# == 2 ]] || {
    echo 'Usage: build_transport.sh PINNED_HDK NEW_WORK_ROOT' >&2
    exit 2
}
hdk=$(realpath -e "$1")
work=$(realpath -m "$2")
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
revision=b603a81f65666e0cf7a67ee5cf18b148eb6b08c3
actual_revision=$(git -C "${hdk}" rev-parse HEAD)
[[ ${actual_revision} == "${revision}" && ! -e ${work} ]]
git -C "${hdk}" diff --exit-code --quiet HEAD
mkdir -p "${work}/source"
finish() {
    local rc=$?
    printf '%s\n' "${rc}" > "${work}/build-exitcode.txt"
}
trap finish EXIT
exec > "${work}/build.log" 2>&1
git -C "${hdk}" archive "${revision}" sdk shared release_version.txt | tar -x -C "${work}/source"
cp "${script_dir}/../first_slice/transport.cpp" "${work}/transport.cpp"
export AWS_FPGA_REPO_DIR="${work}/source"
export SDK_DIR="${AWS_FPGA_REPO_DIR}/sdk"
cd "${SDK_DIR}/userspace"
bash ./mkall_fpga_mgmt_tools.sh
g++ -std=c++17 -O2 -Wall -Wextra -Werror -DMEMORY_BACKEND_HBM=1 -I"${SDK_DIR}/userspace/include" \
    "${work}/transport.cpp" "${SDK_DIR}/userspace/lib/libfpga_mgmt.a" \
    -lrt -lpthread -o "${work}/transport"
g++ --version > "${work}/tool-version.txt"
ldd "${work}/transport" > "${work}/linked-libraries.txt"
printf '%s\n' "${revision}" > "${work}/sdk-revision.txt"
cd "${work}"
sha256sum transport transport.cpp source/sdk/userspace/lib/libfpga_mgmt.a > build-sha256.txt
python3 - "${work}" <<'PYMANIFEST'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
paths = ('transport', 'transport.cpp', 'source/sdk/userspace/lib/libfpga_mgmt.a')
manifest = {'schema': 1, 'memory_backend': 'hbm', 'backend_id': 3,
            'sdk_revision': 'b603a81f65666e0cf7a67ee5cf18b148eb6b08c3',
            'defines': ['MEMORY_BACKEND_HBM=1'],
            'compile_flags': ['-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror'],
            'source_and_binary_sha256': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths},
            'tool_version': (root / 'tool-version.txt').read_text()}
(root / 'transport-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
PYMANIFEST
printf 'HBM_TRANSPORT_BUILD_PASS\n' 
