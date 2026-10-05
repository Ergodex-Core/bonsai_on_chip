#!/usr/bin/env bash
# Local bounded build only; never starts/stops EC2, submits or loads an AFI.
set -euo pipefail
[[ $# == 2 && $2 =~ ^[0-9]+$ && $2 -ge 60 && $2 -le 21600 ]] || {
    echo 'Usage: run_build.sh /absolute/cl_bonsai_hbm_rom MAX_SECONDS(60..21600)' >&2
    exit 2
}
cl=$(realpath -e -- "$1")
budget=$2
[[ ${cl} =~ ^/[A-Za-z0-9_./-]+$ && ${cl##*/} == cl_bonsai_hbm_rom ]] || exit 2
verify_sources() {
    python3 - "${cl}" <<'PY'
import hashlib, json, pathlib, subprocess, sys
cl = pathlib.Path(sys.argv[1])
m = json.loads((cl / 'source-manifest.json').read_text())
for relative, expected in m['source_sha256'].items():
    if hashlib.sha256((cl / relative).read_bytes()).hexdigest() != expected:
        raise SystemExit('Prepared source changed: ' + relative)
for relative, expected in m['vendor_config_sha256'].items():
    if hashlib.sha256((pathlib.Path(m['hdk']) / relative).read_bytes()).hexdigest() != expected:
        raise SystemExit('Vendor configuration changed: ' + relative)
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
exec 9> "${cl}/.run.lock"
flock -n 9 || { echo 'Another CL run holds this workroot.' >&2; exit 1; }
exec 8> "${hdk}/../.f2-simulation.lock"
flock -n 8 || { echo 'Another HDK job holds the shared resource lock.' >&2; exit 1; }
mkdir -p "${cl}/logs"
log=$(mktemp -d "${cl}/logs/build-XXXXXXXX")
record_exit() { local rc=$?; printf '%s\n' "${rc}" > "${log}/exit-code.txt"; return "${rc}"; }
trap record_exit EXIT
vivado -version > "${log}/vivado-version.txt"
grep -Eiq '^vivado v2025\.2([[:space:]]|$)' "${log}/vivado-version.txt"
cp "${cl}/source-manifest.json" "${log}/source-manifest.json"
sha256sum "$0" > "${log}/runner-sha256.txt"
printf '%s\n' "${budget}" > "${log}/budget-seconds.txt"
export CL_DIR=${cl}
cd "${hdk}"
set +u
# shellcheck disable=SC1091
source ./hdk_setup.sh -s > "${log}/hdk-setup.log" 2>&1
set -u
grep -Fq 'AWS HDK setup PASSED.' "${log}/hdk-setup.log"
export CL_DIR=${cl}
if find "${cl}/build/checkpoints" -type f -print -quit | grep -q .; then
    echo 'Checkpoints exist; use a fresh prepare output.' >&2
    exit 1
fi
cd "${cl}/build/scripts"
timeout --signal=TERM --kill-after=30 "${budget}" \
    python3 ./aws_build_dcp_from_cl.py --cl cl_bonsai_hbm_rom --mode small_shell \
    --aws_clk_gen --clock_recipe_a A0 --clock_recipe_hbm H2 --no-encrypt 2>&1 | tee "${log}/console.log"
python3 - "${cl}" "${log}" <<'PY'
import hashlib, json, pathlib, subprocess, sys
cl, log = map(pathlib.Path, sys.argv[1:])
checkpoints = cl / 'build/checkpoints'
if list(checkpoints.glob('*.VIOLATED.dcp')):
    raise SystemExit('Route timing failed; AFI creation prohibited')
dcps = list(checkpoints.glob('*.post_route.dcp'))
tars = list(checkpoints.glob('*.Developer_CL.tar'))
if len(dcps) != 1 or len(tars) != 1:
    raise SystemExit('Missing/ambiguous routed DCP or tarball')
with (log / 'dcp-audit-console.log').open('w') as stream:
    subprocess.run(['vivado', '-mode', 'batch', '-source', str(cl / 'build/scripts/audit_dcp.tcl'),
                    '-tclargs', str(dcps[0]), str(log)], stdout=stream,
                   stderr=subprocess.STDOUT, check=True, timeout=900)
if 'HBM_ROM_DCP_TIMING_PASS_CDC_REVIEW_REQUIRED' not in (log / 'dcp-audit-console.log').read_text():
    raise SystemExit('DCP audit did not complete')
artifacts = dcps + tars + list(log.glob('*.rpt'))
manifest = {'status': 'DCP timing checked; independent CDC/DRC review and hardware execution pending',
            'memory_backend': 'hbm', 'artifacts': {str(p.relative_to(cl)): {
                'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in artifacts}}
(log / 'build-result.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
verify_sources > "${log}/post-run-hdk.txt"
printf 'Evidence: %s\n' "${log}"
