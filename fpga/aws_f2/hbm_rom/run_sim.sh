#!/usr/bin/env bash
# Run a fresh prepared custom CL; this does not create/load an AFI or operate EC2.
set -euo pipefail
[[ $# == 2 && $2 =~ ^[0-9]+$ && $2 -ge 60 && $2 -le 21600 ]] || {
    echo 'Usage: run_sim.sh /absolute/cl_bonsai_hbm_rom MAX_SECONDS(60..21600)' >&2
    exit 2
}
cl=$(realpath -- "$1")
mode=sim
budget=$2
[[ ${cl} =~ ^/[A-Za-z0-9_./-]+$ && ${cl##*/} == cl_bonsai_hbm_rom ]] || exit 2
verify_sources() {
    python3 - "${cl}" << 'PY'
import hashlib, json, pathlib, subprocess, sys
cl = pathlib.Path(sys.argv[1])
m = json.loads((cl / 'source-manifest.json').read_text())
for relative, expected in m['source_sha256'].items():
    if hashlib.sha256((cl / relative).read_bytes()).hexdigest() != expected:
        raise SystemExit('Prepared source changed: ' + relative)
for relative, expected in {'': m['hdk_revision'], **m['resource_pins']}.items():
    path = str(pathlib.Path(m['hdk']) / relative)
    actual = subprocess.check_output(['git', '-C', path, 'rev-parse', 'HEAD'], text=True).strip()
    if actual != expected:
        raise SystemExit('HDK source pin changed: ' + relative)
    subprocess.run(['git', '-C', path, 'diff', '--exit-code', '--quiet', 'HEAD'], check=True)
print(m['hdk'])
PY
}
hdk=$(verify_sources)
mkdir -p "${cl}/logs"
exec 9> "${cl}/.run.lock"
flock -n 9 || {
    echo 'Another custom-CL run holds this workroot.' >&2
    exit 1
}
# The pinned HDK generates shared IP models; use the existing setup lock too.
exec 8> "${hdk}/../.f2-simulation.lock"
flock -n 8 || {
    echo 'Another HDK setup/simulation holds the shared resource lock.' >&2
    exit 1
}
log=$(mktemp -d "${cl}/logs/${mode}-XXXXXXXX")
record_exit() {
    local rc=$?
    printf '%s\n' "${rc}" > "${log}/exit-code.txt"
    return "${rc}"
}
trap record_exit EXIT
command -v vivado > /dev/null
vivado -version > "${log}/vivado-version.txt"
grep -Eiq '^vivado v2025\.2([[:space:]]|$)' "${log}/vivado-version.txt"
cp "${cl}/source-manifest.json" "${log}/source-manifest.json"
printf '%s\n' "${budget}" > "${log}/budget-seconds.txt"
sha256sum "$0" > "${log}/runner-sha256.txt"
export CL_DIR=${cl}
cd "${hdk}"
set +u
# shellcheck disable=SC1091
source ./hdk_setup.sh -s > "${log}/hdk-setup.log" 2>&1
set -u
grep -Fq 'AWS HDK setup PASSED.' "${log}/hdk-setup.log"
export CL_DIR=${cl}
if [[ ${mode} == sim ]]; then
    [[ ! -d ${cl}/verif/sim/xsim/test_hbm_rom_sv ]] || {
        echo 'Use a fresh prepare output for a simulation replay.' >&2
        exit 1
    }
    [[ -f ${cl}/verif/tests/first_slice_fixture.svh ]] || { echo "Prepare with --fixture and --image" >&2; exit 1; }
    cd "${cl}/verif/scripts"
    unset MAKEFLAGS MFLAGS TEST C_TEST VCS QUESTA IES SIMULATOR COMPLIB_DIR COMPILE_CL_IP_DIR PLUSARGS
    # AWS compile_cl_ips.py discovers IP inputs through Git from the caller's
    # directory. Our fresh CL is external to that checkout; bind discovery only
    # for this make invocation without modifying the pinned vendor scripts.
    hdk_git_dir=$(git -C "${hdk}" rev-parse --absolute-git-dir)
    GIT_DIR=${hdk_git_dir} GIT_WORK_TREE=${hdk} \
        timeout --signal=TERM --kill-after=30 "${budget}" make test_hbm_rom VCS=0 QUESTA=0 IES=0 SHELL=/bin/bash '.SHELLFLAGS=-o pipefail -c' 2>&1 | tee "${log}/console.log"
    sim_log="${cl}/verif/sim/xsim/test_hbm_rom_sv/test_hbm_rom.log"
    [[ -f ${sim_log} ]]
    cp "${sim_log}" "${log}/test_hbm_rom.log"
    grep -E 'HBM_ROM_TEST_PASS assertions=[1-9][0-9]*' "${sim_log}" > "${log}/pass-marker.txt"
    if grep -Eiq '(ERROR:|FATAL:|Fatal:|FAIL |\*\*\*ERROR\*\*\*)' "${sim_log}"; then exit 1; fi
fi
cmp "${cl}/source-manifest.json" "${log}/source-manifest.json"
verify_sources > "${log}/post-run-hdk.txt"
printf 'Evidence: %s\n' "${log}"
