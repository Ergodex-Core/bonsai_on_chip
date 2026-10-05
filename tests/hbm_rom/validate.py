#!/usr/bin/env python3
"""Prepare pinned Q1_0 image, compile HBM RTL, and run sealed-image comparisons."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tests/first_slice'))
from validate import verify_suite_report

RTL = [
    'hdl/verilog/first_slice/' + name + '.sv' for name in (
        'weight_store', 'dot128', 'first_slice_top', 'f2_memory_bridge',
        'hbm_cdc_mailbox', 'hbm_line_bridge'
    )
]
SOURCES = RTL + [
    'tests/hbm_rom/hbm_rom_sim_top.sv', 'tests/hbm_rom/hbm_model_server.cpp'
]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gguf', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--verilator', default=shutil.which('verilator'))
    args = parser.parse_args()
    if not 1 <= args.jobs <= 4:
        parser.error('--jobs must be 1..4 for bounded CPU validation')
    if not args.verilator:
        parser.error('provide --verilator or add Verilator to PATH')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report_path = out / 'validation.json'
    report = {
        'schema':
        'coralnpu.hbm_rom.validation.v1',
        'status':
        'incomplete',
        'fpga_executed':
        False,
        'memory_backend':
        'hbm',
        'steps': [],
        'started_utc':
        time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'known_limits': [
            'CPU AXI model is not the vendor HBM PHY',
            'No synthesis, timing closure or physical FPGA run',
            'DOT128 is not full model inference'
        ]
    }

    def save():
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    def step(
        name,
        command,
        timeout=600,
        env=None,
        count_pattern=None,
        expected_count=None
    ):
        entry = {
            'name': name,
            'command': [str(v) for v in command],
            'timeout_seconds': timeout,
            'status': 'running'
        }
        if env is not None:
            entry['simulation_environment'] = {
                name: env[name]
                for name in (
                    'FIRST_SLICE_MODEL_SERVER', 'HBM_SIM_CORE_HALF_PERIOD',
                    'HBM_SIM_HBM_HALF_PERIOD', 'HBM_SIM_PHASE'
                )
                if name in env
            }
        report['steps'].append(entry)
        save()
        print('Running ' + name, flush=True)
        started = time.monotonic()
        with (out / (name + '.log')).open('w') as log:
            process = subprocess.Popen(
                command,
                cwd=REPO,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True
            )
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
                entry['timed_out'] = True
        entry.update(
            exit_code=code,
            elapsed_seconds=round(time.monotonic() - started, 3),
            log_sha256=digest(out / (name + '.log')),
            status='PASS' if code == 0 else 'FAIL'
        )
        if count_pattern is not None:
            match = re.search(
                count_pattern, (out / (name + '.log')).read_text()
            )
            entry['checks_observed'] = int(match.group(1)) if match else None
            entry['checks_expected'] = expected_count
            if entry['checks_observed'] != expected_count:
                entry['status'] = 'FAIL'
                save()
                raise RuntimeError(
                    name + ' did not run the required check inventory'
                )
        save()
        if code:
            raise RuntimeError(name + ' failed; inspect ' + name + '.log')

    save()
    try:
        tracked = SOURCES + [
            'tests/hbm_rom/validate.py', 'tests/hbm_rom/test_hbm.py',
            'tests/hbm_rom/hbm_line_bridge_tb.sv', 'utils/first_slice/run.py',
            'utils/first_slice/prepare_fixture.py',
            'utils/weightstore/pack_image.py',
            'tests/first_slice/weight_store_tb.cpp',
            'fpga/aws_f2/first_slice/f2_memory_bridge_tb.sv',
            'tests/first_slice/validate.py',
            'tests/first_slice/test_mailbox.py',
            'tests/first_slice/test_transport_protocol.py'
        ]
        report['source_sha256'] = {
            name: digest(REPO / name)
            for name in tracked
        }
        for name, expected_hash in report['source_sha256'].items():
            target = out / 'tested-source' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
            if digest(target) != expected_hash:
                raise RuntimeError(
                    'Source changed while copying snapshot: ' + name
                )
        report['tested_source_directory'] = 'tested-source'
        report['git_base'] = subprocess.check_output([
            'git', 'rev-parse', 'HEAD'
        ],
                                                     cwd=REPO,
                                                     text=True).strip()
        version = subprocess.check_output([args.verilator, '--version'],
                                          text=True).strip()
        report['tool_version'] = version
        report['python_version'] = sys.version
        report['gguf_version'] = importlib.metadata.version('gguf')
        report['compiler_version'] = subprocess.check_output(
            ['g++', '--version'], text=True
        ).splitlines()[0]
        report['verilator_wrapper_sha256'] = digest(args.verilator)
        tool_root = Path(
            os.environ.get(
                'VERILATOR_ROOT',
                str(
                    Path(args.verilator).resolve().parents[1] /
                    'share/verilator'
                )
            )
        )
        tool_binary = tool_root / 'bin/verilator_bin'
        if tool_binary.is_file():
            report['verilator_binary_sha256'] = digest(tool_binary)
        report['compiler_sha256'] = digest(shutil.which('g++'))
        py = sys.executable
        step(
            'prepare-fixture', [
                py, 'utils/first_slice/prepare_fixture.py', '--gguf',
                str(args.gguf.resolve()), '--native',
                str(out / 'native'), '--out',
                str(out / 'fixture')
            ]
        )
        store_obj = out / 'store-obj'
        step(
            'build-hbm-store', [
                args.verilator, '--cc', '--exe', '--build', '--assert',
                '-Wall', '-CFLAGS', '-O3 -std=c++17', '-j',
                str(args.jobs), '--top-module', 'weight_store',
                '-GBACKEND_ID=3', "-GPHYSICAL_BYTES=64'h20000000", '--Mdir',
                str(store_obj),
                str(REPO / RTL[0]),
                str(REPO / 'tests/first_slice/weight_store_tb.cpp')
            ]
        )
        step(
            'hbm-store', [str(store_obj / 'Vweight_store')],
            count_pattern=r'"scenarios":(\d+)',
            expected_count=11
        )
        bridge_obj = out / 'bridge-obj'
        step(
            'build-memory-bridge', [
                args.verilator, '--binary', '--timing', '--assert',
                '-Wno-TIMESCALEMOD', '-j',
                str(args.jobs
                    ), '--top-module', 'f2_memory_bridge_tb', '--Mdir',
                str(bridge_obj),
                str(REPO / 'hdl/verilog/first_slice/f2_memory_bridge.sv'),
                str(REPO / 'fpga/aws_f2/first_slice/f2_memory_bridge_tb.sv')
            ]
        )
        step(
            'memory-bridge', [str(bridge_obj / 'Vf2_memory_bridge_tb')],
            count_pattern=r'checks=(\d+)',
            expected_count=218
        )
        hbm_obj = out / 'hbm-bridge-obj'
        step(
            'build-hbm-bridge', [
                args.verilator, '--binary', '--timing', '--assert', '-Wall',
                '-Wno-TIMESCALEMOD', '-j',
                str(args.jobs), '--top-module', 'hbm_line_bridge_tb', '--Mdir',
                str(hbm_obj),
                str(REPO / 'hdl/verilog/first_slice/hbm_cdc_mailbox.sv'),
                str(REPO / 'hdl/verilog/first_slice/hbm_line_bridge.sv'),
                str(REPO / 'tests/hbm_rom/hbm_line_bridge_tb.sv')
            ]
        )
        step(
            'hbm-bridge', [str(hbm_obj / 'Vhbm_line_bridge_tb')],
            count_pattern=r'hbm_line_bridge_tb: (\d+) checks',
            expected_count=270
        )
        obj = out / 'obj'
        cmd = [
            args.verilator, '--cc', '--exe', '--build', '--assert', '-Wall',
            '-CFLAGS', '-O3 -std=c++17', '-j',
            str(args.jobs), '--top-module', 'hbm_rom_sim_top', '--Mdir',
            str(obj)
        ]
        cmd += [str(REPO / name) for name in SOURCES]
        step('build-hbm', cmd)
        for name in SOURCES:
            if digest(REPO / name) != report['source_sha256'][name]:
                raise RuntimeError(
                    'Source changed during compilation: ' + name
                )
        binary = obj / 'Vhbm_rom_sim_top'
        manifest = {
            'schema': 1,
            'backend': 'verilator',
            'memory_backend': 'hbm',
            'binary_sha256': digest(binary),
            'tool_version': version,
            'source_sha256': {
                name: digest(REPO / name)
                for name in SOURCES
            },
            'compile_command': cmd,
            'hbm_configuration': {
                'map_version': 1,
                'pseudochannel': 15,
                'physical_allocation_bytes': 536870912,
                'logical_aperture_bytes': 268435456,
                'physical_beat_bytes': 32,
                'logical_line_bytes': 64,
                'core_clock_period_units': 18,
                'hbm_clock_period_units': 10,
                'random_seed': 103
            }
        }
        Path(str(binary) + '.manifest.json'
             ).write_text(json.dumps(manifest, indent=2) + '\n')
        env = dict(
            os.environ,
            FIRST_SLICE_MODEL_SERVER=str(binary),
            HBM_SIM_CORE_HALF_PERIOD='9',
            HBM_SIM_HBM_HALF_PERIOD='5',
            HBM_SIM_PHASE='0'
        )
        step(
            'hbm-contract', [py, 'tests/hbm_rom/test_hbm.py', '-v'],
            env=env,
            count_pattern=r'Ran (\d+) tests',
            expected_count=9
        )
        stress_env = dict(
            env,
            HBM_SIM_CORE_HALF_PERIOD='2',
            HBM_SIM_HBM_HALF_PERIOD='5',
            HBM_SIM_PHASE='1'
        )
        step(
            'hbm-contract-reversed-clocks',
            [py, 'tests/hbm_rom/test_hbm.py', '-v'],
            env=stress_env,
            count_pattern=r'Ran (\d+) tests',
            expected_count=9
        )
        step(
            'transport-protocol',
            [py, 'tests/first_slice/test_transport_protocol.py', '-v'],
            env=env,
            count_pattern=r'Ran (\d+) tests',
            expected_count=4
        )
        report['suite_results'] = {}
        for suite, image in (('synthetic',
                              out / 'fixture/synthetic-ternary2.bin'),
                             ('native', out / 'native/weights.bin')):
            step(
                suite, [
                    py, 'utils/first_slice/run.py', '--fixture',
                    str(out / 'fixture/fixture.json'), '--image',
                    str(image), '--transport',
                    str(binary), '--suite', suite, '--out',
                    str(out / (suite + '-run')), '--bulk-timeout', '900'
                ],
                timeout=1800,
                env=env
            )
            report['suite_results'][suite] = verify_suite_report(
                out / (suite + '-run/report.json'), suite, binary, manifest
            )
            save()
        if report['source_sha256'] != {name: digest(REPO / name)
                                       for name in tracked}:
            raise RuntimeError(
                'Sources changed during validation; rerun from a stable checkout'
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
            'report': str(report_path),
            'fpga_executed': False
        })
    )


if __name__ == '__main__':
    main()
