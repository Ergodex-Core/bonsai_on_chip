#!/usr/bin/env bash
# Run each pinned AWS AXI-Lite demo test and preserve its individual evidence.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly SCRIPT_DIR RUN_KIND=validate
# shellcheck source-path=SCRIPTDIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
[[ $# == 1 ]] || fail "Usage: $0 /absolute/dedicated/work-directory"
if ! command -v realpath >/dev/null || ! command -v flock >/dev/null; then
    fail 'Requires Linux realpath and flock.'
fi
initialize_work_root "$1"
check_environment
verify_checkout
verify_resources 2>&1 | tee "${LOG_DIR}/resource-verification.log"
activate_hdk

readonly -a TESTS=(test_null test_adder test_arithmetic_operations test_axil_registers test_control_bits test_error_handling test_random test_reset test_stress_axil)
printf 'test\tmake_exit_code\tevidence_status\n' > "${LOG_DIR}/results.tsv"
failures=0
first_failure=0
cd "${CL_DIR}/verif/scripts"
unset MAKEFLAGS MFLAGS TEST C_TEST VCS QUESTA IES SIMULATOR COMPLIB_DIR COMPILE_CL_IP_DIR PLUSARGS

for test_name in "${TESTS[@]}"; do
    test_log_dir="${LOG_DIR}/${test_name}"
    mkdir -- "${test_log_dir}"
    # Refuse to reuse an old test log. Rename existing output into this run's
    # evidence directory before compilation so stale success cannot count.
    sim_dir="${CL_DIR}/verif/sim/xsim/${test_name}_sv"
    if [[ -e ${sim_dir} ]]; then
        mv -- "${sim_dir}" "${test_log_dir}/previous-output"
    fi
    printf 'make %s VCS=0 QUESTA=0 IES=0 SHELL=/bin/bash .SHELLFLAGS="-o pipefail -c"\n' "${test_name}" > "${test_log_dir}/command.txt"
    # Bash pipefail also applies inside make recipes. Upstream xsim output uses
    # a pipeline; make alone must not hide a failing simulator behind grep/sed.
    set +e
    make "${test_name}" VCS=0 QUESTA=0 IES=0 SHELL=/bin/bash '.SHELLFLAGS=-o pipefail -c' \
        2>&1 | tee "${test_log_dir}/make.log"
    pipeline_codes=("${PIPESTATUS[@]}")
    set -e
    make_rc=${pipeline_codes[0]}
    tee_rc=${pipeline_codes[1]}
    status=FAIL
    simulation_log="${sim_dir}/${test_name}.log"
    # The pinned common_base_test.svh emits these markers after checking both
    # error_count and shell protocol status. Require actual fresh simulation
    # evidence as well as successful process exit codes.
    if (( make_rc == 0 && tee_rc == 0 )) && [[ -f ${simulation_log} ]] \
        && grep -Fq '*** TEST PASSED ***' "${simulation_log}" \
        && grep -Eq 'Detected[[:space:]]+0 errors' "${simulation_log}" \
        && ! grep -Eiq '\*\*\* TEST FAILED \*\*\*|(^|[[:space:]])(ERROR:|FATAL:|Fatal:)|\*\*\*ERROR\*\*\*' "${simulation_log}"; then
        status=PASS
    else
        failures=$((failures + 1))
        if (( first_failure == 0 )); then
            if (( make_rc != 0 )); then first_failure=${make_rc}
            elif (( tee_rc != 0 )); then first_failure=${tee_rc}
            else first_failure=1
            fi
        fi
    fi
    printf '%s\t%s\t%s\n' "${test_name}" "${make_rc}" "${status}" | tee -a "${LOG_DIR}/results.tsv"
    printf '%s\n' "${tee_rc}" > "${test_log_dir}/tee-exit-code.txt"
    if [[ -d ${sim_dir} ]]; then
        # Keep raw XSIM transcripts and traces for this attempt without copying
        # multi-gigabyte compilation output. Old runs stay in previous-output.
        find "${sim_dir}" -maxdepth 1 -type f \( -name '*.log' -o -name '*.jou' -o -name '*.wdb' \) -print0 > "${test_log_dir}/raw-artifact-paths.bin"
        while IFS= read -r -d '' artifact; do
            cp -- "${artifact}" "${test_log_dir}/"
        done < "${test_log_dir}/raw-artifact-paths.bin"
    fi
done

printf 'AWS example RTL simulation: %s passed, %s failed, 0 skipped.\n' \
    "$((${#TESTS[@]} - failures))" "${failures}" | tee "${LOG_DIR}/result.txt"
printf 'Bonsai F2 CL integration: NOT RUN. Actual FPGA execution: NOT RUN.\n' | tee -a "${LOG_DIR}/result.txt"
find "${LOG_DIR}" -type f ! -name artifact-sha256.txt ! -path '*/previous-output/*' -print0 \
    | sort -z | xargs -0 sha256sum > "${LOG_DIR}/artifact-sha256.txt"
exit "${first_failure}"
