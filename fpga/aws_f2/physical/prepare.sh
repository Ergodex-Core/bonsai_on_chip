#!/usr/bin/env bash
# Build the public AWS SDK and MMIO probes without a global installation.
set -euo pipefail
if [[ $# != 2 ]]; then
  echo 'Usage: prepare.sh PINNED_AWS_FPGA_CHECKOUT NEW_WORK_ROOT' >&2
  exit 2
fi
aws_root=$(realpath -e "${1}")
work_root=$(realpath -m "${2}")
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
expected_revision=b603a81f65666e0cf7a67ee5cf18b148eb6b08c3
revision=$(git -C "${aws_root}" rev-parse HEAD)
tracked_status=$(git -C "${aws_root}" status --porcelain --untracked-files=no)
[[ ${revision} == "${expected_revision}" && -z ${tracked_status} ]]
[[ ! -e ${work_root} ]]
mkdir -m 0755 "${work_root}"
finish() {
  local rc=$?
  printf '%s\n' "${rc}" >"${work_root}/build-exitcode.txt"
}
trap finish EXIT
exec >"${work_root}/build.log" 2>&1
printf 'SOURCE_REVISION %s\n' "${revision}"
printf '%s\n' "${revision}" >"${work_root}/source-revision.txt"
git -C "${aws_root}" submodule status >"${work_root}/submodules.txt"
git -C "${aws_root}" grep -n agfi-06447dea0ca9b0a39 >"${work_root}/published-agfi.txt"
mkdir "${work_root}/aws-sdk-build" "${work_root}/bin" "${work_root}/public-raw" "${work_root}/harness"
git -C "${aws_root}" archive "${expected_revision}" sdk shared release_version.txt hdk/cl/examples/cl_demo/cl_axil_reg_access/software | tar -x -C "${work_root}/aws-sdk-build"
cp "${script_dir}/mmio_probe.c" "${script_dir}/prepare.sh" "${script_dir}/validate.sh" "${work_root}/harness/"
export AWS_FPGA_REPO_DIR="${work_root}/aws-sdk-build"
export SDK_DIR="${AWS_FPGA_REPO_DIR}/sdk"
cd "${SDK_DIR}/userspace"
bash ./mkall_fpga_mgmt_tools.sh
libdir="${SDK_DIR}/userspace/lib/so"
test -f "${libdir}/libfpga_mgmt.so.1.0.0"
ln -s libfpga_mgmt.so.1.0.0 "${libdir}/libfpga_mgmt.so.1"
cp -a "${AWS_FPGA_REPO_DIR}/hdk/cl/examples/cl_demo/cl_axil_reg_access/software" "${work_root}/software"
cd "${work_root}/software/runtime"
make all
gcc -std=gnu11 -O2 -g -Wall -Wextra -Werror -I"${SDK_DIR}/userspace/include" \
  "${work_root}/harness/mmio_probe.c" -L"${libdir}" -Wl,-rpath,"${libdir}" \
  -lfpga_mgmt -o "${work_root}/mmio_probe"
tool="${SDK_DIR}/userspace/fpga_mgmt_tools/src/static-fpga-local-cmd"
for operation in DescribeFpgaImage LoadFpgaImage ClearFpgaImage; do
  case ${operation} in
    DescribeFpgaImage) command=fpga-describe-local-image ;;
    LoadFpgaImage) command=fpga-load-local-image ;;
    ClearFpgaImage) command=fpga-clear-local-image ;;
    *) exit 2 ;;
  esac
  # The argument forwarding is emitted literally into the generated wrapper.
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash\nset -eu\nexec %q %q "$@"\n' "${tool}" "${operation}" >"${work_root}/bin/${command}"
  chmod 0755 "${work_root}/bin/${command}"
done
export LD_LIBRARY_PATH="${libdir}"
ldd "${work_root}/mmio_probe" "${work_root}/software/runtime/test_sum" >"${work_root}/linked-libraries.txt"
gcc --version >"${work_root}/gcc-version.txt"
cd "${work_root}"
sha256sum harness/* bin/* mmio_probe software/runtime/test_sum software/runtime/test_carry software/runtime/test_random \
  aws-sdk-build/sdk/userspace/fpga_mgmt_tools/src/static-fpga-local-cmd \
  aws-sdk-build/sdk/userspace/lib/so/libfpga_mgmt.so.1.0.0 >build-sha256.txt
find software/src software/include -type f -exec sha256sum {} + >upstream-runtime-sha256.txt
printf 'PREPARE_PASS\n'
