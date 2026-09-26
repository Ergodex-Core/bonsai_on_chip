#!/usr/bin/env bash
# Prepare pinned AWS F2 HDK sources for XSIM on an existing Developer AMI.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly SCRIPT_DIR RUN_KIND=setup
# shellcheck source-path=SCRIPTDIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
[[ $# == 1 ]] || fail "Usage: $0 /absolute/dedicated/work-directory"
if ! command -v realpath >/dev/null || ! command -v flock >/dev/null; then
    fail 'Requires Linux realpath and flock.'
fi
initialize_work_root "$1"
check_environment

if [[ ! -e ${HDK_REPO} ]]; then
    # Fetch the exact object, not a moving branch. A partial failed setup remains
    # visible for diagnosis; use a new work root if it cannot be verified below.
    mkdir -- "${HDK_REPO}"
    git -C "${HDK_REPO}" init
    git -C "${HDK_REPO}" remote add origin "${AWS_FPGA_URL}"
    git -C "${HDK_REPO}" fetch --depth 1 origin "${AWS_FPGA_REV}" 2>&1 | tee "${LOG_DIR}/git-fetch.log"
    GIT_LFS_SKIP_SMUDGE=1 git -C "${HDK_REPO}" checkout --detach "${AWS_FPGA_REV}"
fi
verify_checkout

GIT_LFS_SKIP_SMUDGE=1 git -C "${HDK_REPO}" submodule update --init --checkout \
    hdk/common/ip hdk/common/shell_stable/hlx 2>&1 | tee "${LOG_DIR}/submodule-update.log"
for relative in hdk/common/ip hdk/common/shell_stable/hlx; do
    git -C "${HDK_REPO}/${relative}" lfs install --local --skip-smudge
    git -C "${HDK_REPO}/${relative}" lfs pull 2>&1 | tee -a "${LOG_DIR}/lfs-pull.log"
done
verify_resources 2>&1 | tee "${LOG_DIR}/resource-verification.log"
activate_hdk
printf 'SETUP READY: pinned AWS example XSIM environment. No simulation has been run.\n' | tee "${LOG_DIR}/result.txt"
