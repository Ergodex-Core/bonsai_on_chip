#!/usr/bin/env bash
# Run a fresh prepared custom CL; this does not create/load an AFI or operate EC2.
set -euo pipefail
[[ $# == 2 && ($2 == sim || $2 == build) ]] || {
    echo 'Usage: run.sh /absolute/cl_bonsai_first_slice sim|build' >&2
    exit 2
}
cl=$(realpath -- "$1")
mode=$2
[[ ${cl} =~ ^/[A-Za-z0-9_./-]+$ && ${cl##*/} == cl_bonsai_first_slice ]] || exit 2
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
    [[ ! -d ${cl}/verif/sim/xsim/test_first_slice_sv ]] || {
        echo 'Use a fresh prepare output for a simulation replay.' >&2
        exit 1
    }
    cd "${cl}/verif/scripts"
    unset MAKEFLAGS MFLAGS TEST C_TEST VCS QUESTA IES SIMULATOR COMPLIB_DIR COMPILE_CL_IP_DIR PLUSARGS
    make test_first_slice VCS=0 QUESTA=0 IES=0 SHELL=/bin/bash '.SHELLFLAGS=-o pipefail -c' 2>&1 | tee "${log}/console.log"
    sim_log="${cl}/verif/sim/xsim/test_first_slice_sv/test_first_slice.log"
    [[ -f ${sim_log} ]]
    cp "${sim_log}" "${log}/test_first_slice.log"
    grep -E 'FIRST_SLICE_TEST_PASS assertions=[1-9][0-9]*' "${sim_log}" > "${log}/pass-marker.txt"
    if grep -Eiq '(ERROR:|FATAL:|Fatal:|FAIL |\*\*\*ERROR\*\*\*)' "${sim_log}"; then exit 1; fi
else
    if find "${cl}/build/checkpoints" -type f -print -quit | grep -q .; then
        echo 'Build checkpoints already exist; use a fresh prepare output.' >&2
        exit 1
    fi
    cd "${cl}/build/scripts"
    python3 ./aws_build_dcp_from_cl.py --cl cl_bonsai_first_slice --mode small_shell --no-encrypt 2>&1 | tee "${log}/console.log"
    python3 - "${cl}" "${log}" << 'PY'
import hashlib, json, pathlib, re, subprocess, sys
cl, log = map(pathlib.Path, sys.argv[1:])
checkpoints = cl / 'build/checkpoints'
if list(checkpoints.glob('*.VIOLATED.dcp')):
    raise SystemExit('AWS route timing gate failed; do not create/load this AFI')
dcps = list(checkpoints.glob('*.post_route.dcp'))
tars = list(checkpoints.glob('*.Developer_CL.tar'))
reports = list((cl / 'build/reports').glob('*.post_route_timing.rpt'))
if len(dcps) != 1 or len(tars) != 1 or len(reports) != 1:
    raise SystemExit('Missing/ambiguous routed DCP, AFI tarball, or final timing report')
text = reports[0].read_text()
slacks = re.findall(r'Slack\s+\((MET|VIOLATED)\)\s*:\s*(-?[0-9.]+)ns', text)
if not slacks or any(status != 'MET' or float(value) < 0 for status, value in slacks):
    raise SystemExit('Final reported setup paths did not meet timing')
with (log / 'dcp-audit-console.log').open('w') as stream:
    subprocess.run(['vivado', '-mode', 'batch', '-source',
                    str(cl / 'build/scripts/audit_dcp.tcl'), '-tclargs',
                    str(dcps[0]), str(log)], stdout=stream, stderr=subprocess.STDOUT, check=True)
if 'FIRST_SLICE_DCP_TIMING_PASS' not in (log / 'dcp-audit-console.log').read_text():
    raise SystemExit('Routed DCP timing audit did not finish')
audit_reports = [log / name for name in ('timing_summary.rpt', 'utilization.rpt', 'clocks.rpt', 'drc.rpt')]
manifest = {'status': 'routed_DCP_ready_for_AFI_submission' , 'clock_hz': 250000000,
            'reported_setup_slacks_ns': [float(value) for _, value in slacks],
            'artifacts': {str(p.relative_to(cl)): {'bytes': p.stat().st_size,
                'sha256': hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()}
                for p in dcps + tars + reports + audit_reports}}
(log / 'build-result.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
fi
cmp "${cl}/source-manifest.json" "${log}/source-manifest.json"
verify_sources > "${log}/post-run-hdk.txt"
printf 'Evidence: %s\n' "${log}"
