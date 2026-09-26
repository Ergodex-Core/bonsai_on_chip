#!/usr/bin/env bash
# Usage: run_parity.sh verilator|arcilator|compare REPO BUILD_DIR [TOOLCHAIN]
set -euo pipefail
mode=${1:?mode required}
repo_arg=${2:?repository path required}
build_arg=${3:?build directory required}
absolute() { python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$1"; }
# Resolve every caller-relative path before any build command changes directory.
script=$(absolute "$0")
driver="$(dirname "${script}")/handshake_parity.cpp"
repo=$(absolute "${repo_arg}")
build=$(absolute "${build_arg}")
toolchain=""
rtl="${repo}/hdl/verilog/rvv/common"
case "${mode}" in
  verilator|compare) ;;
  arcilator) toolchain=$(absolute "${4:?toolchain path required}") ;;
  *) echo "unknown mode: ${mode}" >&2; exit 2 ;;
esac
mkdir -p "${build}"

manifest() {
  python3 - "$1" "${mode}" "${repo}" "${driver}" "${script}" "${build}" "${toolchain}" <<'PY'
import csv, hashlib, json, pathlib, platform, subprocess, sys
operation, backend, repo_arg, driver_arg, script_arg, build_arg, toolchain_arg = sys.argv[1:]
repo, driver, script, build = map(pathlib.Path, (repo_arg, driver_arg, script_arg, build_arg))
configurations = [(1, 0), (1, 1), (3, 0), (3, 1)]
source_files = {
    'hdl/verilog/rvv/common/cdffr.sv': repo / 'hdl/verilog/rvv/common/cdffr.sv',
    'hdl/verilog/rvv/common/handshake_multistage_ctrl.sv': repo / 'hdl/verilog/rvv/common/handshake_multistage_ctrl.sv',
    'tests/arcilator/handshake_parity.cpp': driver,
    'tests/arcilator/run_parity.sh': script,
}
def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()
def save(path, value):
    temporary = path.with_suffix('.tmp.json')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)
def checked(condition, message):
    if not condition:
        raise RuntimeError(message)
def current_sources():
    return {name: digest(path) for name, path in source_files.items()}
def run_version(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()
def read_manifest(name):
    value = json.loads((build / f'{name}-inputs.json').read_text())
    checked(value.get('schema_version') == 1, f'{name}: unknown manifest schema')
    checked(value.get('status') == 'complete', f'{name}: backend run is not complete')
    checked(value.get('source_sha256') == current_sources(),
            f'{name}: stale manifest; current DUT, driver, or runner hashes differ')
    return value
try:
    path = build / f'{backend}-inputs.json'
    if operation == 'start':
        # Invalidate the aggregate result first, before source/tool checks. A
        # failed or interrupted rerun cannot leave the previous comparison PASS.
        save(build / 'results.json', {'status': 'incomplete', 'scope': 'handshake control pilot',
             'reason': f'{backend} run started; a successful comparison is required'})
        save(path, {'schema_version': 1, 'status': 'running'})
        value = {'schema_version': 1, 'status': 'running', 'backend': backend,
                 'source_sha256': current_sources(), 'host_platform': platform.platform()}
        try:
            git = ['git', '-c', 'safe.directory=' + str(repo), '-C', str(repo)]
            value['repository_revision'] = run_version(git + ['rev-parse', 'HEAD'])
            value['dut_source_changes'] = run_version(git + ['status', '--porcelain', '--',
                'hdl/verilog/rvv/common/cdffr.sv', 'hdl/verilog/rvv/common/handshake_multistage_ctrl.sv'])
        except (OSError, subprocess.CalledProcessError):
            value['repository_revision'] = 'unavailable; source hashes identify tested inputs'
        if backend == 'arcilator':
            toolchain = pathlib.Path(toolchain_arg)
            names = ['bin/arcilator', 'bin/circt-verilog', 'bin/opt', 'bin/llc',
                     'runtime/arcilator-header-cpp.py', 'runtime/arcilator-runtime.h',
                     'runtime/arcilator-uvm-runtime.h', 'lib/libCIRCTArcRuntime.a',
                     'lib/libCIRCTSupport.a', 'lib/libLLVMSupport.a', 'lib/libLLVMDemangle.a']
            value['toolchain_sha256'] = {name: digest(toolchain / name) for name in names}
            value['versions'] = {name: run_version([str(toolchain / 'bin' / name), '--version'])
                                 for name in ('arcilator', 'circt-verilog', 'opt', 'llc')}
            value['versions']['clang++'] = run_version(['clang++', '--version'])
        else:
            value['versions'] = {name: run_version([name, '--version']) for name in ('verilator', 'c++')}
        save(path, value)
    elif operation == 'finish':
        value = json.loads(path.read_text())
        checked(value.get('status') == 'running', 'missing in-progress manifest')
        checked(value.get('source_sha256') == current_sources(), 'sources changed during backend run')
        value['trace_sha256'] = {}
        value['result_lines'] = {}
        for stages, bubbles in configurations:
            key = f'n{stages}_b{bubbles}'
            result = (build / key / f'{backend}-result.txt').read_text().strip()
            checked(result.startswith(f'PASS stages={stages} remove_bubbles={bubbles} '),
                    f'{key}: missing successful oracle result')
            value['trace_sha256'][key] = digest(build / key / f'{backend}.csv')
            value['result_lines'][key] = result
        value['status'] = 'complete'
        save(path, value)
    elif operation == 'compare':
        save(build / 'results.json', {'status': 'verifying', 'scope': 'handshake control pilot'})
        manifests = {name: read_manifest(name) for name in ('verilator', 'arcilator')}
        summaries = []
        for stages, bubbles in configurations:
            key = f'n{stages}_b{bubbles}'
            traces = {}
            for name, value in manifests.items():
                trace = build / key / f'{name}.csv'
                checked(value.get('trace_sha256', {}).get(key) == digest(trace),
                        f'{name}/{key}: trace differs from its completed-run manifest')
                traces[name] = trace.read_bytes()
            checked(traces['verilator'] == traces['arcilator'], f'{key}: full trace mismatch')
            rows = list(csv.DictReader(traces['arcilator'].decode().splitlines()))
            checked(bool(rows), f'{key}: empty trace')
            checked(all(int(row['stages']) == stages and int(row['remove_bubbles']) == bubbles
                        for row in rows), f'{key}: trace parameter mismatch')
            rising = sum(row['clock'] == '1' and (i == 0 or rows[i-1]['clock'] == '0')
                         for i, row in enumerate(rows))
            summaries.append({'stages': stages, 'remove_bubbles': bubbles,
                              'verilator': 'PASS', 'arcilator': 'PASS', 'trace_parity': 'PASS',
                              'samples': len(rows), 'rising_edges': rising,
                              'checked_outputs_per_backend': len(rows) * 5,
                              'occupied_masks_observed': sorted({int(row['valids']) for row in rows}),
                              'trace_sha256': hashlib.sha256(traces['arcilator']).hexdigest()})
            print(f'PASS full trace parity {key}')
        results = {'status': 'complete', 'scope': 'handshake_multistage_ctrl parity pilot only; not the repository test suite',
                   'repository_revision': manifests['verilator']['repository_revision'],
                   'source_sha256': current_sources(),
                   'toolchain_sha256': manifests['arcilator']['toolchain_sha256'],
                   'versions': {name: m['versions'] for name, m in manifests.items()},
                   'configurations': summaries, 'passed': 4, 'failed': 0, 'skipped': 0,
                   'total_samples_per_backend': sum(r['samples'] for r in summaries),
                   'total_checked_outputs_per_backend': sum(r['checked_outputs_per_backend'] for r in summaries)}
        save(build / 'results.json', results)
    else:
        raise RuntimeError('unknown manifest operation')
except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
    print('FAIL: ' + str(error), file=sys.stderr)
    sys.exit(1)
PY
}

if [[ "${mode}" == compare ]]; then
  manifest compare
  exit 0
fi
manifest start
for config in 1:0 1:1 3:0 3:1; do
  stages=${config%:*}
  bubbles=${config#*:}
  target="${build}/n${stages}_b${bubbles}"
  mkdir -p "${target}"
  case "${mode}" in
    verilator)
      verilator --cc --exe --build -j 2 --top-module handshake_multistage_ctrl \
        --prefix Vdut --Mdir "${target}/verilator" \
        -GNUM_PIPE_REGS="${stages}" -GREMV_PIPE_BUBBLE="${bubbles}" \
        -CFLAGS "-std=c++17 -DPIPE_STAGES=${stages} -DREMOVE_BUBBLES=${bubbles}" \
        "${rtl}/cdffr.sv" "${rtl}/handshake_multistage_ctrl.sv" "${driver}" \
        > "${target}/verilator-build.log" 2>&1
      "${target}/verilator/Vdut" "${target}/verilator.csv" | tee "${target}/verilator-result.txt"
      ;;
    arcilator)
      arc="${target}/arcilator"
      mkdir -p "${arc}"
      "${toolchain}/bin/circt-verilog" --ir-hw --single-unit --timescale=1ns/1ps \
        --top=handshake_multistage_ctrl -GNUM_PIPE_REGS="${stages}" -GREMV_PIPE_BUBBLE="${bubbles}" \
        "${rtl}/cdffr.sv" "${rtl}/handshake_multistage_ctrl.sv" -o "${arc}/design.mlir" \
        > "${arc}/frontend.log" 2>&1
      "${toolchain}/bin/arcilator" --emit-llvm --state-file="${arc}/state.json" \
        "${arc}/design.mlir" -o "${arc}/model.ll" > "${arc}/compile.log" 2>&1
      "${toolchain}/bin/opt" --strip-debug -O2 -S "${arc}/model.ll" -o "${arc}/optimized.ll"
      "${toolchain}/bin/llc" -O2 -filetype=obj "${arc}/optimized.ll" -o "${arc}/model.o"
      python3 "${toolchain}/runtime/arcilator-header-cpp.py" "${arc}/state.json" \
        > "${arc}/handshake_multistage_ctrl.h"
      clang++ -O2 -std=c++17 -DUSE_ARC -DPIPE_STAGES="${stages}" -DREMOVE_BUBBLES="${bubbles}" \
        -no-pie -I"${arc}" -I"${toolchain}/runtime" -I"${toolchain}/include" \
        "${driver}" "${arc}/model.o" \
        "${toolchain}/lib/libCIRCTArcRuntime.a" "${toolchain}/lib/libCIRCTSupport.a" \
        "${toolchain}/lib/libLLVMSupport.a" "${toolchain}/lib/libLLVMDemangle.a" \
        -lpthread -latomic -lz -ltinfo -lm -ldl -o "${arc}/handshake_parity" \
        > "${arc}/link.log" 2>&1
      "${arc}/handshake_parity" "${target}/arcilator.csv" | tee "${target}/arcilator-result.txt"
      ;;
    *) echo "unexpected backend: ${mode}" >&2; exit 2 ;;
  esac
done
manifest finish
