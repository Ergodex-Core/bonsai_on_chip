"""Build and run the complete local ERG-103 first-slice regression from source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
RTL = [
    'hdl/verilog/first_slice/' + name + '.sv' for name in
    ('weight_store', 'dot128', 'first_slice_top', 'f2_memory_bridge')
]


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def verify_suite_report(path, suite, binary, manifest):
    """A successful child exit is insufficient without its completed evidence."""
    data = json.loads(path.read_text())
    expected = 396 if suite == 'native' else 6
    cases = data.get('cases')
    if (data.get('schema') != 'coralnpu.first_slice.run.v1'
            or data.get('suite') != suite or data.get('status') != 'PASS'
            or data.get('passed') != expected or data.get('failed') != 0
            or data.get('fpga_executed') is not False
            or data.get('rejected_cases') != (0 if suite == 'native' else 2)
            or not isinstance(cases, list) or len(cases) != expected):
        raise RuntimeError(
            suite + ' subreport lacks the complete passing suite'
        )
    if any(case.get('passed') is not True or case.get('completion_observed')
           is not True or case.get('submitted') is not True for case in cases):
        raise RuntimeError(suite + ' subreport contains incomplete cases')
    ids = [case.get('id') for case in cases]
    if any(type(value) is not str
           for value in ids) or len(set(ids)) != expected:
        raise RuntimeError(
            suite + ' subreport repeats or omits case identities'
        )
    if (data.get('transport_sha256') != digest(binary)
            or data.get('build_manifest') != manifest
            or data.get('transport', {}).get('backend') != 'verilator'
            or data.get('final_transport', {}).get('backend') != 'verilator'):
        raise RuntimeError(
            suite + ' subreport is not bound to the built simulator'
        )
    return {
        'status': 'PASS',
        'passed': expected,
        'report_sha256': digest(path)
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gguf', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        'schema': 'coralnpu.first_slice.validation.v1',
        'status': 'incomplete',
        'fpga_executed': False,
        'steps': [],
        'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    }
    path = output / 'validation.json'

    def save():
        path.write_text(json.dumps(report, indent=2) + '\n')

    def step(name, command, env=None):
        item = {
            'name': name,
            'command': [str(x) for x in command],
            'status': 'running'
        }
        report['steps'].append(item)
        save()
        print('Running ' + name, flush=True)
        started = time.monotonic()
        with (output / (name + '.log')).open('w') as log:
            result = subprocess.run(
                command,
                cwd=REPO,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                check=False
            )
        item.update(
            exit_code=result.returncode,
            elapsed_seconds=round(time.monotonic() - started, 3),
            status='PASS' if result.returncode == 0 else 'FAIL'
        )
        save()
        if result.returncode:
            raise RuntimeError(name + ' failed; inspect its log')

    def build(name, sources, timing=False):
        before = {source: digest(REPO / source) for source in sources}
        build_dir = output / (name + '-obj')
        command = [verilator, '--binary' if timing else '--cc']
        if timing:
            command += ['--timing', '--assert', '-Wno-TIMESCALEMOD']
        else:
            command += [
                '--exe', '--build', '-Wall', '-CFLAGS', '-O3 -std=c++17'
            ]
        command += [
            '-j',
            str(args.jobs), '--top-module', name, '--Mdir',
            str(build_dir)
        ]
        command += [str(REPO / source) for source in sources]
        step('build-' + name, command)
        if before != {source: digest(REPO / source) for source in sources}:
            raise RuntimeError('source changed during compile: ' + name)
        binary = build_dir / ('V' + name)
        manifest = {
            'schema': 1,
            'backend': 'verilator',
            'tool_version': version,
            'binary_sha256': digest(binary),
            'source_sha256': before,
            'compile_command': command
        }
        Path(str(binary) + '.manifest.json'
             ).write_text(json.dumps(manifest, indent=2) + '\n')
        return binary

    save()
    try:
        verilator = shutil.which('verilator')
        if not verilator:
            raise RuntimeError('Verilator is required on PATH')
        version = subprocess.check_output([verilator, '--version'],
                                          text=True).strip()
        report['tool_version'] = version
        report['python_version'] = sys.version
        report['git_base'] = subprocess.check_output([
            'git', 'rev-parse', 'HEAD'
        ],
                                                     cwd=REPO,
                                                     text=True).strip()
        tracked = set(RTL)
        for directory in ('tests/first_slice', 'utils/first_slice',
                          'utils/weightstore', 'fpga/aws_f2/first_slice'):
            tracked.update(
                str(p.relative_to(REPO))
                for p in (REPO / directory).rglob('*')
                if p.is_file() and '__pycache__' not in p.parts
            )
        report['source_sha256'] = {
            name: digest(REPO / name)
            for name in sorted(tracked)
        }
        py = sys.executable
        step(
            'fixture-unit',
            [py, 'utils/first_slice/test_prepare_fixture.py', '-v']
        )
        step(
            'runner-failure-unit', [py, 'utils/first_slice/test_run.py', '-v']
        )
        step(
            'prepare-fixture', [
                py, 'utils/first_slice/prepare_fixture.py', '--gguf',
                str(args.gguf.resolve()), '--native',
                str(output / 'native'), '--out',
                str(output / 'fixture')
            ]
        )
        fixture = json.loads((output / 'fixture/fixture.json').read_text())
        actual = output / 'actual-q1-block.bin'
        actual.write_bytes(
            bytes.fromhex(fixture['cases'][0]['native_q1_0_hex'])
        )
        dot = build('dot128', [RTL[1], 'tests/first_slice/dot128_tb.cpp'])
        step(
            'dot128',
            [str(dot), '--q1-block',
             str(actual), '--offset', '4095']
        )
        store = build(
            'weight_store', [RTL[0], 'tests/first_slice/weight_store_tb.cpp']
        )
        step('weight-store', [str(store)])
        fault_test = build(
            'first_slice_top',
            RTL[:3] + ['tests/first_slice/mailbox_fault_tb.cpp']
        )
        step('mailbox-faults', [str(fault_test)])
        bridge = build(
            'f2_memory_bridge_tb',
            [RTL[3], 'fpga/aws_f2/first_slice/f2_memory_bridge_tb.sv'],
            timing=True
        )
        step('memory-bridge', [str(bridge)])
        server = build(
            'first_slice_sim_top', RTL + [
                'tests/first_slice/first_slice_sim_top.sv',
                'tests/first_slice/model_server.cpp'
            ]
        )
        environment = dict(os.environ, FIRST_SLICE_MODEL_SERVER=str(server))
        step(
            'mailbox', [py, 'tests/first_slice/test_mailbox.py', '-v'],
            env=environment
        )
        step(
            'transport-protocol',
            [py, 'tests/first_slice/test_transport_protocol.py', '-v'],
            env=environment
        )
        server_manifest = json.loads(
            Path(str(server) + '.manifest.json').read_text()
        )
        report['suite_results'] = {}
        for suite, image in (('native', output / 'native/weights.bin'),
                             ('synthetic',
                              output / 'fixture/synthetic-ternary2.bin')):
            step(
                suite, [
                    py, 'utils/first_slice/run.py', '--fixture',
                    str(output / 'fixture/fixture.json'), '--image',
                    str(image), '--transport',
                    str(server), '--suite', suite, '--out',
                    str(output / (suite + '-run'))
                ]
            )
            report['suite_results'][suite] = verify_suite_report(
                output / (suite + '-run') / 'report.json', suite, server,
                server_manifest
            )
            save()
        if report['source_sha256'] != {name: digest(REPO / name)
                                       for name in sorted(tracked)}:
            raise RuntimeError(
                'source changed during validation; rerun from a stable checkout'
            )
        report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        report['finished_utc'] = time.strftime(
            '%Y-%m-%dT%H:%M:%SZ', time.gmtime()
        )
        save()
    print(
        json.dumps({
            'status': 'PASS',
            'report': str(path),
            'fpga_executed': False
        })
    )


if __name__ == '__main__':
    main()
