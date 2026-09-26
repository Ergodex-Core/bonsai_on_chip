#!/usr/bin/env bash
# Shared implementation for the F2 Developer AMI simulation scripts.

readonly AWS_FPGA_URL=https://github.com/aws/aws-fpga.git
readonly AWS_FPGA_REV=b603a81f65666e0cf7a67ee5cf18b148eb6b08c3
readonly AWS_FPGA_IP_REV=6d32be972e6da854e61a8d3d6ec0466ab491c1b3
readonly AWS_FPGA_HLX_REV=2383c2b64572c75163b1b60fbd0abea482c637e6
readonly EXAMPLE_REL=hdk/cl/examples/cl_demo/cl_axil_reg_access

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

record_exit() {
    local rc=$?
    printf '%s\n' "${rc}" > "${LOG_DIR}/exit-code.txt"
    printf 'Evidence: %s (exit %s)\n' "${LOG_DIR}" "${rc}" >&2
    return "${rc}"
}

initialize_work_root() {
    [[ $# == 1 && $1 == /* ]] || fail 'Supply exactly one absolute work directory.'
    # Upstream HDK Makefiles contain unquoted paths; restrict this explicitly.
    [[ $1 =~ ^/[A-Za-z0-9_./-]+$ ]] || fail 'Work directory must not contain whitespace or shell metacharacters.'
    WORK_ROOT=$(realpath -m -- "$1")
    [[ ${WORK_ROOT} != / && ${WORK_ROOT} != "${HOME:-}" ]] || fail 'Use a dedicated work directory, not / or your home directory.'
    mkdir -p -- "${WORK_ROOT}/logs"
    [[ -w ${WORK_ROOT} ]] || fail 'Work directory is not writable.'
    HDK_REPO="${WORK_ROOT}/aws-fpga"
    : "${RUN_KIND:?Caller must set RUN_KIND before initialization.}"
    local run_timestamp
    run_timestamp=$(date -u +%Y%m%dT%H%M%SZ)
    LOG_DIR=$(mktemp -d "${WORK_ROOT}/logs/${RUN_KIND}-${run_timestamp}-XXXXXX")
    readonly WORK_ROOT HDK_REPO LOG_DIR
    # Prevent concurrent setup/simulations from sharing generated IP libraries.
    exec 9>"${WORK_ROOT}/.f2-simulation.lock"
    flock -n 9 || fail 'Another setup or simulation holds this work directory lock.'
    trap record_exit EXIT
    printf 'Work directory: %s\nEvidence directory: %s\n' "${WORK_ROOT}" "${LOG_DIR}"
}

check_environment() {
    local os_name arch_name
    os_name=$(uname -s)
    arch_name=$(uname -m)
    [[ ${os_name} == Linux && ${arch_name} == x86_64 ]] || fail 'Requires Linux x86_64; the AMD tools do not run on macOS/Arm.'
    [[ -r /etc/os-release ]] || fail 'Cannot read /etc/os-release.'
    # shellcheck disable=SC1091
    source /etc/os-release
    [[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || fail 'This pinned environment requires Ubuntu 24.04.'
    (( BASH_VERSINFO[0] >= 4 )) || fail 'Requires Bash 4 or newer.'
    local tool
    for tool in git git-lfs make python3 perl gcc g++ sha256sum realpath flock tee vivado xvlog xelab xsim xsc; do
        command -v "${tool}" >/dev/null || fail "Missing prerequisite: ${tool}. Install/enable it before running; this script does not install system packages."
    done
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail 'Python 3.10 or newer is required.'
    vivado -version > "${LOG_DIR}/vivado-version.txt" 2>&1
    grep -Eiq '^Vivado v2025\.2([[:space:]]|$)' "${LOG_DIR}/vivado-version.txt" || fail 'Enable the Vivado 2025.2 environment before running.'
    [[ -n ${XILINX_VIVADO:-} && -f ${XILINX_VIVADO}/data/xsim/xsim.ini ]] || fail 'Source the installed Vivado 2025.2 settings64.sh first; XILINX_VIVADO is missing or invalid.'
    {
        date -u +%Y-%m-%dT%H:%M:%SZ
        uname -a
        cat /etc/os-release
        git --version
        git lfs version
        make --version
        python3 --version
        gcc --version
        cat "${LOG_DIR}/vivado-version.txt"
        printf 'XILINX_VIVADO=%s\n' "${XILINX_VIVADO}"
        df -h "${WORK_ROOT}"
        free -h
    } > "${LOG_DIR}/environment.txt"
}

verify_checkout() {
    [[ -d ${HDK_REPO}/.git ]] || fail 'HDK checkout missing; run setup.sh first.'
    local actual_origin actual_revision
    actual_origin=$(git -C "${HDK_REPO}" remote get-url origin)
    actual_revision=$(git -C "${HDK_REPO}" rev-parse HEAD)
    [[ ${actual_origin} == "${AWS_FPGA_URL}" ]] || fail 'Existing HDK origin does not match the pinned upstream.'
    [[ ${actual_revision} == "${AWS_FPGA_REV}" ]] || fail 'HDK revision differs from the pin; use a new work directory.'
    git -C "${HDK_REPO}" diff --quiet --ignore-submodules=all HEAD || fail 'HDK has tracked changes; use a clean dedicated work directory.'
}

verify_resources() {
    local relative expected actual ip_version
    for relative in hdk/common/ip hdk/common/shell_stable/hlx; do
        case "${relative}" in
            hdk/common/ip) expected=${AWS_FPGA_IP_REV} ;;
            *) expected=${AWS_FPGA_HLX_REV} ;;
        esac
        [[ -f ${HDK_REPO}/${relative}/.git ]] || fail "Missing pinned submodule: ${relative}"
        actual=$(git -C "${HDK_REPO}/${relative}" rev-parse HEAD)
        [[ ${actual} == "${expected}" ]] || fail "Unexpected submodule revision: ${relative} ${actual}"
        git -C "${HDK_REPO}/${relative}" diff --quiet HEAD || fail "Tracked changes in ${relative}"
        git -C "${HDK_REPO}/${relative}" lfs fsck || fail "Missing/corrupt LFS objects in ${relative}; rerun setup or use a clean work directory."
    done
    ip_version=$(tr -d '[:space:]' < "${HDK_REPO}/hdk/common/ip/VIVADO_VERSION")
    [[ ${ip_version} == 2025.2 ]] || fail 'Pinned IP version does not match Vivado 2025.2.'
}

activate_hdk() {
    cd "${HDK_REPO}" || fail 'Cannot enter the HDK checkout.'
    export CL_DIR="${HDK_REPO}/${EXAMPLE_REL}"
    [[ -f ${CL_DIR}/verif/scripts/Makefile ]] || fail 'Pinned cl_axil_reg_access simulation target is missing.'
    # Normal hdk_setup.sh follows moving branches and may sudo-install git-lfs.
    # Preflight requires git-lfs; -s uses our pinned, already downloaded resources.
    # Upstream setup is not nounset-safe. Preserve our strict mode afterwards.
    set +u
    # shellcheck disable=SC1091
    source ./hdk_setup.sh -s > "${LOG_DIR}/hdk-setup.log" 2>&1
    set -u
    cat "${LOG_DIR}/hdk-setup.log"
    : "${HDK_SHELL_DIR:?Upstream HDK setup did not set its shell directory.}"
    : "${SCRIPT_DIR:?Caller must set its script directory.}"
    [[ ${VIVADO_TOOL_VERSION:-} == 2025.2 && -n ${HDK_COMMON_DIR:-} ]] || fail 'HDK environment activation did not complete.'
    grep -Fq 'AWS HDK setup PASSED.' "${LOG_DIR}/hdk-setup.log" || fail 'Missing HDK setup success marker.'
    {
        printf 'aws_fpga_url=%s\naws_fpga_revision=%s\n' "${AWS_FPGA_URL}" "${AWS_FPGA_REV}"
        printf 'execution_boundary=AWS example RTL under XSIM and AWS shell bus-functional models\n'
        printf 'example=%s\nvivado_version=%s\n' "${EXAMPLE_REL}" "${VIVADO_TOOL_VERSION}"
        printf 'cl_ip_revision=%s\nhlx_revision=%s\n' "${AWS_FPGA_IP_REV}" "${AWS_FPGA_HLX_REV}"
        printf 'shell_version_file:\n'
        cat "${HDK_SHELL_DIR}/shell_version.txt"
        git submodule status
    } > "${LOG_DIR}/source-manifest.txt"
    sha256sum "${SCRIPT_DIR}"/*.sh "${SCRIPT_DIR}"/README.md > "${LOG_DIR}/runner-sha256.txt"
}
