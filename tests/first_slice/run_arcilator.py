"""Run the integrated first-slice Arcilator workflow, preserving complete evidence."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--native-image', type=Path, required=True)
    parser.add_argument('--synthetic-image', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--stage-timeout', type=float, default=600)
    parser.add_argument('--bulk-timeout', type=float, default=1800)
    args = parser.parse_args()
    if any(not math.isfinite(value) or value <= 0
           for value in (args.stage_timeout, args.bulk_timeout)):
        parser.error('timeouts must be positive')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {
        'schema': 'coralnpu.first_slice.arcilator.v1',
        'status': 'incomplete',
        'backend': 'arcilator',
        'fpga_executed': False,
        'host_platform': platform.platform(),
        'host_machine': platform.machine(),
        'python_version': sys.version,
        'scope':
        'integrated first-slice bus workflow; not full CoralNPU or standalone RTL suites',
        'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'steps': []
    }
    path = out / 'validation.json'

    def save():
        path.write_text(json.dumps(report, indent=2) + '\n')

    def step(name, command, timeout, env=None):
        command = [str(x) for x in command]
        item = {'name': name, 'command': command, 'status': 'running'}
        report['steps'].append(item)
        save()
        print('Running ' + name, flush=True)
        start = time.monotonic()
        try:
            with (out / (name + '.log')).open('w') as log:
                process = subprocess.Popen(
                    command,
                    cwd=REPO,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True
                )
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
        except subprocess.TimeoutExpired:
            item.update(
                status='FAIL',
                timed_out=True,
                exit_code=None,
                elapsed_seconds=round(time.monotonic() - start, 6)
            )
            save()
            raise
        item.update(
            exit_code=returncode,
            elapsed_seconds=round(time.monotonic() - start, 6),
            status='PASS' if returncode == 0 else 'FAIL'
        )
        save()
        if returncode:
            raise RuntimeError(name + ' failed; inspect its log')
        return item

    save()
    try:
        sources = list((REPO / 'hdl/verilog/first_slice').glob('*.sv'))
        sources += [
            REPO / 'tests/first_slice' / n for n in (
                'first_slice_sim_top.sv', 'model_server.cpp',
                'build_arcilator.py', 'generate_arcilator_adapter.py',
                'run_arcilator.py', 'test_mailbox.py',
                'test_transport_protocol.py'
            )
        ]
        sources += [
            REPO / 'utils/first_slice' / n
            for n in ('run.py', 'prepare_fixture.py')
        ]
        before = {str(p.relative_to(REPO)): digest(p) for p in sources}
        report['source_sha256'] = before
        fixtures = [
            args.fixture.resolve(),
            args.native_image.resolve(),
            args.native_image.resolve().parent / 'manifest.json',
            args.synthetic_image.resolve()
        ]
        fixture_hashes = {str(p): digest(p) for p in fixtures}
        report['fixture_sha256'] = fixture_hashes
        save()
        build_dir = out / 'build'
        # Compiler/version stages own bounded process-group cleanup.
        # Do not externally kill their parent while a nested group is active.
        step(
            'build', [
                sys.executable, REPO / 'tests/first_slice/build_arcilator.py',
                '--toolchain',
                args.toolchain.resolve(), '--out', build_dir,
                '--stage-timeout', args.stage_timeout
            ], None
        )
        binary = build_dir / 'model_server'
        manifest_path = Path(str(binary) + '.manifest.json')
        manifest_bytes = manifest_path.read_bytes()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        manifest = json.loads(manifest_bytes)
        if manifest.get('status') != 'PASS' or manifest.get('backend'
                                                            ) != 'arcilator':
            raise RuntimeError(
                'build manifest does not prove a successful Arcilator build'
            )
        env = dict(
            os.environ,
            FIRST_SLICE_MODEL_SERVER=str(binary),
            FIRST_SLICE_EXPECTED_BACKEND='arcilator'
        )
        for name, filename, count in [('mailbox', 'test_mailbox.py', 3),
                                      ('transport-protocol',
                                       'test_transport_protocol.py', 4)]:
            step(
                name,
                [sys.executable, REPO / 'tests/first_slice' / filename, '-v'],
                args.stage_timeout, env
            )
            log = (out / (name + '.log')).read_text()
            if not re.search(r'Ran ' + str(count) + r' tests? in ',
                             log) or not log.rstrip().endswith('OK'):
                raise RuntimeError(
                    name + ' did not execute the complete expected test count'
                )
        suites = {}
        for suite, image, count in [('synthetic', args.synthetic_image, 6),
                                    ('native', args.native_image, 396)]:
            suite_dir = out / suite
            item = step(
                suite, [
                    sys.executable, REPO / 'utils/first_slice/run.py',
                    '--fixture',
                    args.fixture.resolve(), '--image',
                    image.resolve(), '--transport', binary, '--suite', suite,
                    '--out', suite_dir, '--bulk-timeout', args.bulk_timeout
                ], args.bulk_timeout * 3 + args.stage_timeout
            )
            result_path = suite_dir / 'report.json'
            data = json.loads(result_path.read_text())
            cases = data.get('cases', [])
            if (data.get('schema') != 'coralnpu.first_slice.run.v1'
                    or data.get('status') != 'PASS'
                    or data.get('suite') != suite
                    or data.get('passed') != count or data.get('failed') != 0
                    or data.get('rejected_cases')
                    != (2 if suite == 'synthetic' else 0)
                    or data.get('fpga_executed') is not False
                    or len(cases) != count or any(
                        c.get('passed') is not True or c.get('submitted')
                        is not True or c.get('completion_observed') is not True
                        for c in cases) or len({c.get('id')
                                                for c in cases}) != count
                    or data.get('build_manifest') != manifest
                    or data.get('transport_sha256') != digest(binary)
                    or data.get('transport', {}).get('backend') != 'arcilator'
                    or data.get('final_transport',
                                {}).get('backend') != 'arcilator'):
                raise RuntimeError(
                    suite +
                    ' report lacks complete source-bound passing evidence'
                )
            final = data['final_transport']
            suites[suite] = {
                'status':
                'PASS',
                'passed':
                count,
                'report_sha256':
                digest(result_path),
                'final_cycles':
                final['cycles'],
                'final_ddr_reads':
                final['ddr_reads'],
                'final_ddr_writes':
                final['ddr_writes'],
                'workflow_wall_seconds':
                item['elapsed_seconds'],
                'cycles_per_workflow_second':
                final['cycles'] / item['elapsed_seconds'],
                'rate_scope':
                'end-to-end host workflow including load, two readbacks, hashes and commands; not core-only rate'
            }
        if before != {str(p.relative_to(REPO)): digest(p) for p in sources}:
            raise RuntimeError('source changed during execution')
        if fixture_hashes != {str(p): digest(p) for p in fixtures}:
            raise RuntimeError('fixtures changed during execution')
        if digest(binary) != manifest['binary_sha256']:
            raise RuntimeError('simulator changed during execution')
        if digest(manifest_path) != manifest_hash:
            raise RuntimeError('build manifest changed during execution')
        report.update(
            finished_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            status='PASS',
            suites=suites,
            build_manifest_sha256=manifest_hash
        )
        save()
        print(
            json.dumps({
                'status': 'PASS',
                'native_passed': 396,
                'synthetic_passed': 6,
                'mailbox_passed': 3,
                'transport_protocol_passed': 4,
                'fpga_executed': False
            })
        )
    except Exception as error:
        report.update(
            status='FAIL',
            error=str(error),
            finished_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        )
        save()
        raise


if __name__ == '__main__':
    main()
