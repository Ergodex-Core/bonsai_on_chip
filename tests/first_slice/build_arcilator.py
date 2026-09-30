"""Build the first-slice bus server with an externally supplied Arcilator toolchain."""
import argparse
import hashlib
import json
import math
import os
import signal
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
SOURCES = [
    'hdl/verilog/first_slice/' + n + '.sv'
    for n in ('weight_store', 'dot128', 'first_slice_top', 'f2_memory_bridge')
]
SOURCES += [
    'tests/first_slice/' + n for n in (
        'first_slice_sim_top.sv', 'model_server.cpp',
        'generate_arcilator_adapter.py', 'build_arcilator.py'
    )
]
TOOLS = ['bin/' + n for n in ('circt-verilog', 'arcilator', 'opt', 'llc')]
TOOLS += [
    'runtime/' + n for n in (
        'arcilator-header-cpp.py', 'arcilator-runtime.h',
        'arcilator-uvm-runtime.h'
    )
]
# Generated model headers conditionally reference this public runtime API.
TOOLS += [
    'include/circt/Dialect/Arc/Runtime/' + n
    for n in ('ArcRuntime.h', 'Common.h')
]
LIBRARIES = [
    'lib/' + n for n in (
        'libCIRCTArcRuntime.a', 'libCIRCTSupport.a', 'libLLVMSupport.a',
        'libLLVMDemangle.a'
    )
]


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--stage-timeout', type=float, default=600)
    args = parser.parse_args()
    if not math.isfinite(args.stage_timeout) or args.stage_timeout <= 0:
        parser.error('--stage-timeout must be positive')
    toolchain, out = args.toolchain.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {
        'schema': 1,
        'backend': 'arcilator',
        'status': 'incomplete',
        'host': platform.platform(),
        'steps': [],
        'fpga_executed': False
    }
    report_path = out / 'build.json'

    def save():
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    def run(name, command, stdout=None):
        command = [str(x) for x in command]
        step = {'name': name, 'command': command, 'status': 'running'}
        report['steps'].append(step)
        save()
        start = time.monotonic()
        with (out / (name + '.log')).open('w') as log:
            output = stdout.open('w') if stdout else log
            try:
                process = subprocess.Popen(
                    command,
                    cwd=REPO,
                    stdout=output,
                    stderr=log if stdout else subprocess.STDOUT,
                    start_new_session=True
                )
                try:
                    returncode = process.wait(timeout=args.stage_timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    step.update(
                        status='FAIL',
                        timed_out=True,
                        exit_code=None,
                        elapsed_seconds=round(time.monotonic() - start, 6)
                    )
                    save()
                    raise
            finally:
                if stdout:
                    output.close()
        step.update(
            exit_code=returncode,
            elapsed_seconds=round(time.monotonic() - start, 6),
            status='PASS' if returncode == 0 else 'FAIL'
        )
        save()
        if returncode:
            raise RuntimeError(name + ' failed; inspect log')

    save()
    try:
        if sys.byteorder != 'little':
            raise RuntimeError('adapter requires little-endian host')
        compiler = shutil.which('clang++')
        if not compiler:
            raise RuntimeError('clang++ missing')
        hashes = {p: digest(REPO / p) for p in SOURCES}
        tools = {p: digest(toolchain / p) for p in TOOLS + LIBRARIES}
        report.update(
            source_sha256=hashes,
            toolchain_sha256=tools,
            cxx_sha256=digest(Path(compiler).resolve())
        )
        versions = {}
        for name, executable in [
            ('circt-verilog', toolchain / 'bin/circt-verilog'),
            ('arcilator', toolchain / 'bin/arcilator'), ('clang++', compiler)
        ]:
            versions[name] = subprocess.check_output([
                str(executable), '--version'
            ],
                                                     text=True,
                                                     timeout=30).strip()
        for name in ('opt', 'llc'):
            versions[name] = subprocess.check_output([
                str(toolchain / 'bin' / name), '--version'
            ],
                                                     text=True,
                                                     timeout=30).strip()
        report['tool_version'] = versions
        save()
        run(
            'frontend', [
                toolchain / 'bin/circt-verilog', '--ir-hw', '--single-unit',
                '--timescale=1ns/1ps', '--top=first_slice_sim_top'
            ] + [REPO / s for s in SOURCES[:5]] + ['-o', out / 'design.mlir']
        )
        run(
            'arcilator', [
                toolchain / 'bin/arcilator', '--emit-llvm',
                '--state-file=' + str(out / 'state.json'), out / 'design.mlir',
                '-o', out / 'model.ll'
            ]
        )
        run(
            'opt', [
                toolchain / 'bin/opt', '--strip-debug', '-O2', '-S',
                out / 'model.ll', '-o', out / 'optimized.ll'
            ]
        )
        run(
            'llc', [
                toolchain / 'bin/llc', '-O2', '-filetype=obj',
                out / 'optimized.ll', '-o', out / 'model.o'
            ]
        )
        run(
            'header', [
                sys.executable, toolchain / 'runtime/arcilator-header-cpp.py',
                out / 'state.json'
            ],
            stdout=out / 'first_slice_sim_top.h'
        )
        run(
            'adapter', [
                sys.executable,
                REPO / 'tests/first_slice/generate_arcilator_adapter.py',
                out / 'state.json', out / 'arcilator_adapter.h'
            ]
        )
        binary = out / 'model_server'
        run(
            'link', [
                compiler, '-O2', '-std=c++17', '-DUSE_ARC', '-no-pie',
                '-I' + str(out), '-I' + str(toolchain / 'runtime'),
                '-I' + str(toolchain / 'include'),
                REPO / 'tests/first_slice/model_server.cpp', out / 'model.o'
            ] + [toolchain / s for s in LIBRARIES] + [
                '-lpthread', '-latomic', '-lz', '-ltinfo', '-lm', '-ldl', '-o',
                binary
            ]
        )
        if hashes != {p: digest(REPO / p) for p in SOURCES}:
            raise RuntimeError('source changed during build')
        if tools != {p: digest(toolchain / p) for p in TOOLS + LIBRARIES}:
            raise RuntimeError('toolchain changed during build')
        if report['cxx_sha256'] != digest(Path(compiler).resolve()):
            raise RuntimeError('C++ compiler changed during build')
        artifacts = {
            p.name: digest(p)
            for p in out.iterdir()
            if p.is_file() and p != report_path
        }
        report.update(
            status='PASS',
            binary_sha256=digest(binary),
            artifact_sha256=artifacts
        )
        save()
        Path(str(binary) +
             '.manifest.json').write_text(json.dumps(report, indent=2) + '\n')
        print('PASS Arcilator first-slice build: ' + str(binary))
    except Exception as error:
        if isinstance(error, subprocess.TimeoutExpired) and report['steps']:
            report['steps'][-1].update(
                status='FAIL', timed_out=True, exit_code=None
            )
        report.update(status='FAIL', error=str(error))
        save()
        raise


if __name__ == '__main__':
    main()
