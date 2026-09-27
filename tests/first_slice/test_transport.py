#!/usr/bin/env python3
"""Test the public transport against a mock SDK; never execute an FPGA.

Run on Linux x86_64:
  python3 tests/first_slice/test_transport.py --sdk /path/to/aws-fpga --out /tmp/mock-run

--sdk also accepts sdk/userspace/include directly. The three external public
headers are verified against their pinned hashes; no SDK libraries are linked.
Only synthetic bytes are loaded into sdk_mock.cpp's in-process memory. Omit
--out to clean all generated binaries, logs and bytes after the run. Assertions
use explicit exceptions so Python's -O cannot disable validation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[2]
EXPECTED_CHECKS = 41
SDK_REVISION = 'b603a81f65666e0cf7a67ee5cf18b148eb6b08c3'
HEADER_SHA256 = {
    'fpga_mgmt.h':
    '5d553a7f9ac8a035e5fac96027631e0619c67f9936b70e275fb5af960de913a4',
    'fpga_pci.h':
    '81e9c168b10d1508c775b01ab5c369d90ea82eb5adb2a7c24b942432b4f1cc8d',
    'hal/fpga_common.h':
    '026c8c9d095c8c270c2fd1570169576dd7941b15c772f8853fab9331f88010c3',
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def header_directory(sdk):
    for candidate in (sdk / 'sdk/userspace/include', sdk / 'userspace/include',
                      sdk):
        if (candidate / 'fpga_mgmt.h').is_file():
            for relative, expected in HEADER_SHA256.items():
                require(
                    digest(candidate / relative) == expected,
                    'public SDK header does not match pinned revision: ' +
                    relative
                )
            return candidate
    raise RuntimeError(
        '--sdk must identify an aws-fpga checkout or its public include directory'
    )


def execute(args, output):
    report = {
        'schema': 'coralnpu.first_slice.sdk_mock.v1',
        'status': 'incomplete',
        'scope': 'Mock public SDK only; no physical FPGA or RTL execution',
        'fpga_executed': False,
        'checks': [],
        'sdk_revision': SDK_REVISION
    }
    report_path = output / 'report.json'

    def save():
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    save()
    try:
        require(
            platform.system() == 'Linux'
            and platform.machine() in ('x86_64', 'amd64'),
            'the public transport requires Linux x86_64'
        )
        include = header_directory(args.sdk.resolve())
        compiler = shutil.which(args.cxx)
        require(compiler is not None, 'C++ compiler not found: ' + args.cxx)
        sources = [
            'fpga/aws_f2/first_slice/transport.cpp',
            'tests/first_slice/sdk_mock.cpp',
            'tests/first_slice/test_transport.py'
        ]
        report['source_sha256'] = {
            path: digest(REPO / path)
            for path in sources
        }
        report['sdk_header_sha256'] = HEADER_SHA256
        report['compiler'] = subprocess.check_output([compiler, '--version'],
                                                     text=True).splitlines()[0]
        binary = output / 'transport-mock'
        command = [
            compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-I',
            str(include),
            str(REPO / sources[0]),
            str(REPO / sources[1]), '-o',
            str(binary)
        ]
        report['compile_command'] = command
        with (output / 'build.log').open('w') as log:
            compiled = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=120
            )
        require(
            compiled.returncode == 0, 'mock build failed; inspect build.log'
        )
        report['binary_sha256'] = digest(binary)
        source = output / 'input image.bin'
        source.write_bytes(bytes(range(128)) * 65)
        destination = output / 'output image.bin'
        final_destination = output / 'final image.bin'
        third_destination = output / 'third image.bin'
        environment = dict(
            os.environ, FIRST_SLICE_EXPECTED_AGFI='agfi-00000000000000103'
        )
        environment.pop('MOCK_FAILURE', None)

        def run(
            name,
            commands,
            failure=None,
            expected_ok=False,
            expected_error=None
        ):
            item = {
                'name': name,
                'status': 'running',
                'injected_failure': failure
            }
            report['checks'].append(item)
            save()
            env = dict(environment)
            if failure:
                env['MOCK_FAILURE'] = failure
            result = subprocess.run([str(binary)],
                                    input='\n'.join(commands) + '\n',
                                    text=True,
                                    capture_output=True,
                                    env=env,
                                    timeout=20,
                                    check=False)
            (output / (name + '.log')
             ).write_text(result.stdout + '\nSTDERR:\n' + result.stderr)
            item['exit_code'] = result.returncode
            messages = [
                json.loads(line) for line in result.stdout.splitlines()
            ]
            require((result.returncode == 0) == expected_ok,
                    name + ': unexpected exit status')
            require(
                bool(messages)
                and all(isinstance(message, dict) for message in messages),
                name + ': missing/malformed transport replies'
            )
            require(
                all(message.get('ok') is True for message in messages)
                if expected_ok else messages[-1].get('ok') is False,
                name + ': response success/failure disagrees with exit'
            )
            if expected_ok:
                require(
                    len(messages) == len(commands),
                    name + ': missing command response'
                )
            if expected_error is not None:
                require(
                    expected_error in result.stderr,
                    name + ': missing expected diagnostic'
                )
            item['status'] = 'PASS'
            save()
            return messages

        load = [
            'WRITE 280 8320', 'WRITE 256 1', 'LOAD ' + json.dumps(str(source))
        ]
        dump = [
            'WRITE 256 2', 'DUMP ' + json.dumps(str(destination)) + ' 8320'
        ]
        final_dump = 'DUMP ' + json.dumps(str(final_destination)) + ' 8320'
        roundtrip_commands = ['INFO', 'READ 0'] + load + dump + [
            'WRITE 256 3', 'POKE 0 4294967295', 'PEEK 0', final_dump, 'INFO',
            'QUIT'
        ]
        messages = run('roundtrip', roundtrip_commands, expected_ok=True)
        replies = dict(zip(roundtrip_commands, messages))
        require(
            destination.read_bytes() == source.read_bytes(),
            'whole-image mock readback mismatch'
        )
        require(
            final_destination.read_bytes() == source.read_bytes(),
            'final mock image changed'
        )
        require(
            replies[final_dump]['bytes'] == 8320
            and replies[final_dump]['completed_dumps'] == 2,
            'final DUMP accounting mismatch'
        )
        require(
            messages[0]['backend'] == 'aws_f2'
            and messages[0]['clock_measured'] is False
            and replies['PEEK 0']['value'] == 0x03020100,
            'mock identity/unchanged sealed word mismatch'
        )
        require(
            messages[1]['resp'] == 0 and messages[1]['resp_source']
            == 'sdk_completion_not_axi_response',
            'SDK completion semantics missing'
        )
        require(
            replies['POKE 0 4294967295']['posted'] is True
            and 'resp' not in replies['POKE 0 4294967295'],
            'posted POKE falsely claims an AXI response'
        )
        require(
            messages[-2]['loaded_bytes'] == 8320
            and messages[-2]['readback_bytes'] == 16640,
            'bulk byte counters do not match roundtrip'
        )
        for failure in ('init', 'describe', 'agfi', 'pci', 'abi', 'not_cold',
                        'attach', 'peek', 'final_identity', 'detach', 'close'):
            run('sdk-' + failure, ['QUIT'], failure)
        health_fields = (
            'int_status', 'dma_pcis_timeout_count', 'ocl_slave_timeout_count',
            'pcim_axi_protocol_error_status', 'pcim_axi_protocol_error_count',
            'pcim_range_error_count'
        )
        for message in (messages[0], messages[-2]):
            require(
                all(
                    type(message.get('shell_health', {}).get(field)) is int
                    and message['shell_health'][field] == 0
                    for field in health_fields
                ), 'INFO omits checked shell health metrics'
            )
        for failure, field in (('metrics-int-status',
                                'int_status'), ('metrics-pcis-timeout',
                                                'dma_pcis_timeout_count'),
                               ('metrics-ocl-timeout',
                                'ocl_slave_timeout_count'),
                               ('metrics-pcim-status',
                                'pcim_axi_protocol_error_status'),
                               ('metrics-pcim-count',
                                'pcim_axi_protocol_error_count'),
                               ('metrics-range-count',
                                'pcim_range_error_count'),
                               ('metrics-late-pcis-timeout',
                                'dma_pcis_timeout_count'),
                               ('metrics-late-ocl-timeout',
                                'ocl_slave_timeout_count')):
            run(
                failure, ['QUIT'],
                failure,
                expected_error='shell hardware error ' + field + '=1'
            )
        for commands, failure in ((['WRITE 280 8320'], 'poke'),
                                  (load, 'burst'), (load, 'burst_partial'),
                                  (load, 'drain'), (load + dump, 'map'),
                                  (load + dump, 'map_partial')):
            destination.unlink(missing_ok=True)
            run('sdk-' + failure, commands, failure)
        destination.unlink(missing_ok=True)
        run(
            'dump-epoch-change',
            load + dump,
            'epoch-change',
            expected_error='DUMP image identity changed'
        )
        invalid = [
            ('second-verifying-dump', load + dump + [final_dump]),
            (
                'third-dump', load + dump + [
                    'WRITE 256 3', final_dump,
                    'DUMP ' + json.dumps(str(third_destination)) + ' 8320'
                ]
            ),
            ('negative-address', ['READ -1']),
            ('read-range', ['READ 8192']),
            ('unaligned-write', ['WRITE 3 1']),
            ('oversized-value', ['WRITE 0 4294967296']),
            ('extra-argument', ['READ 0 trailing']),
            ('unquoted-path', ['LOAD ' + str(source)]),
            ('load-before-loading', ['LOAD ' + json.dumps(str(source))]),
            ('second-load', load + ['LOAD ' + json.dumps(str(source))]),
            ('peek-range', load + ['PEEK 8320']),
            ('poke-before-seal', load + ['POKE 0 1']),
            (
                'short-dump', load + [
                    'WRITE 256 2',
                    'DUMP ' + json.dumps(str(destination)) + ' 64'
                ]
            ),
        ]
        for name, commands in invalid:
            destination.unlink(missing_ok=True)
            final_destination.unlink(missing_ok=True)
            third_destination.unlink(missing_ok=True)
            run(name, commands)
        destination.write_bytes(b'preserve existing')
        run('exclusive-output', load + dump)
        require(
            destination.read_bytes() == b'preserve existing',
            'existing output was modified'
        )
        require(
            len(report['checks']) == EXPECTED_CHECKS,
            'unexpected mock test inventory'
        )
        require(
            report['source_sha256'] == {
                path: digest(REPO / path)
                for path in sources
            }, 'source changed during test'
        )
        report.update(status='PASS', passed=EXPECTED_CHECKS)
    except Exception as error:
        if report['checks'] and report['checks'][-1]['status'] == 'running':
            report['checks'][-1]['status'] = 'FAIL'
        report.update(status='FAIL', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        save()
    print(
        json.dumps({
            'status': 'PASS',
            'checks': EXPECTED_CHECKS,
            'fpga_executed': False,
            'scope': report['scope']
        })
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--cxx', default='c++')
    args = parser.parse_args()
    if args.out is not None:
        output = args.out.resolve()
        output.mkdir(parents=True, exist_ok=False)
        execute(args, output)
    else:
        with tempfile.TemporaryDirectory(prefix='first-slice-sdk-mock-'
                                         ) as directory:
            execute(args, Path(directory))


if __name__ == '__main__':
    main()
