#!/usr/bin/env python3
"""Run the complete public native-PQ2 matrix on a GitHub hosted Linux runner."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from native_reference import write_fixtures
from generate_adapter import wrapper, adapter
from compare import SOURCE_REVISIONS, RTL_HASHES, VARIANTS, PROFILES, TIMING, HARNESS_PATHS


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log):
    start = time.monotonic()
    print('START ' + str(log), flush=True)
    with log.open('w') as stream:
        try:
            subprocess.run([str(x) for x in command],
                           stdout=stream,
                           stderr=subprocess.STDOUT,
                           check=True,
                           timeout=900)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            stream.flush()
            print(log.read_text(), file=sys.stderr, flush=True)
            raise
    print(f'DONE {log} {time.monotonic() - start:.3f}s', flush=True)


def export_llvm(binpath, build):
    # Arcilator's early-stop modes print MLIR even with --emit-llvm.
    run([
        binpath / 'mlir-translate', '--mlir-to-llvmir', build / 'llvm.mlir',
        '-o', build / 'model.ll'
    ], build / 'translate.log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--backend', choices=('verilator', 'arcilator'), required=True
    )
    parser.add_argument('--snapshots', type=Path, required=True)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or platform.system(
    ) != 'Linux' or platform.machine(
    ) != 'x86_64' or sys.byteorder != 'little':
        parser.error(
            'Requires an authorized GitHub hosted Linux x86-64 runner'
        )
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    tools = args.tools.resolve()
    sources = {}
    for kind, revision in SOURCE_REVISIONS.items():
        checkout = (args.snapshots / kind).resolve()
        actual = subprocess.check_output([
            'git', '-C', str(checkout), 'rev-parse', 'HEAD'
        ],
                                         text=True).strip()
        if actual != revision:
            raise RuntimeError('Snapshot revision mismatch: ' + kind)
        folder = {
            'baseline': 'baseline',
            'fetch': 'fetch',
            'spatial': 'spatial'
        }[kind]
        source = checkout / 'experiments/native_pq2' / folder / 'coral_weight_axi.sv'
        if digest(source) != RTL_HASHES[kind]:
            raise RuntimeError('Snapshot RTL hash mismatch: ' + kind)
        sources[kind] = source
    fixture_path = out / 'fixtures.bin'
    fixture = write_fixtures(fixture_path, seed=7193, random_cases=32)
    (out /
     'fixture-manifest.json').write_text(json.dumps(fixture, indent=2) + '\n')
    manifest = {
        'schema': 1,
        'harness': {
            key: digest(path)
            for key, path in HARNESS_PATHS.items()
        },
        'variants': {
            name: {
                'rtl_sha256':
                RTL_HASHES['baseline' if name == 'E1' else 'fetch' if name.
                           startswith('E1-fetch') else 'spatial'],
                'parameters':
                params
            }
            for name, params in VARIANTS.items()
        }
    }
    (out /
     'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    evidence = {
        'schema': 1,
        'status': 'RUNNING',
        'backend': args.backend,
        'ci_head': os.environ['GITHUB_SHA'],
        'source_revisions': SOURCE_REVISIONS,
        'fixture': fixture,
        'source_manifest': manifest,
        'profiles': PROFILES,
        'repeats': 5,
        'timing': TIMING,
        'cpu_overlap_variants': [name for name in VARIANTS if name != 'E1'],
        'environment': {
            'system':
            platform.system(),
            'machine':
            platform.machine(),
            'cpu_count':
            os.cpu_count(),
            'affinity':
            len(os.sched_getaffinity(0)),
            'cpu_model':
            next((
                line.split(':', 1)[1].strip()
                for line in Path('/proc/cpuinfo').read_text().splitlines()
                if line.startswith('model name')
            ), 'unknown'),
            'physical_memory_bytes':
            os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES'),
            'optimization':
            '-O2',
            'compiler':
            subprocess.check_output(['clang++-18', '--version'],
                                    text=True).splitlines()[0]
        },
        'arc_pipeline':
        'pinned LLHD process-loop unroller; official structural LLHD passes; Arc allocated state; official bufferized-array LLVM lowering; mlir-to-llvmir',
        'runs': []
    }
    rows = []

    try:
        for name, parameters in VARIANTS.items():
            kind = 'baseline' if name == 'E1' else 'fetch' if name.startswith(
                'E1-fetch'
            ) else 'spatial'
            build = out / ('build-' + name)
            build.mkdir()
            wrapper(build / 'coral_weight_ci_top.sv', parameters)
            start = time.monotonic()
            if args.backend == 'verilator':
                run([
                    tools / 'verilator/bin/verilator', '--Wall', '--cc',
                    '--exe', '--build', '-j', '2', '--compiler', 'clang',
                    '--top-module', 'coral_weight_ci_top', '--prefix',
                    'Vcoral_weight_axi', '--unroll-count', '4096',
                    '--unroll-stmts', '100000', '--Mdir', build / 'obj',
                    '-CFLAGS', '-std=c++17 -O2', '-MAKEFLAGS',
                    'CXX=clang++-18 OPT_FAST=-O2 OPT_SLOW=-O2 OPT_GLOBAL=-O2',
                    sources[kind], build / 'coral_weight_ci_top.sv',
                    HERE.parent / 'tests/test_engine.cpp'
                ], build / 'compile.log')
                binary = build / 'obj/Vcoral_weight_axi'
            else:
                binpath = tools / 'circt/bin'
                runtime = tools / 'circt/runtime'
                run([
                    binpath / 'circt-verilog', '--ir-hw', '--sroa',
                    '--single-unit', '--top=coral_weight_ci_top',
                    sources[kind], build / 'coral_weight_ci_top.sv', '-o',
                    build / 'design.mlir'
                ], build / 'frontend.log')
                run([
                    binpath / 'native-pq2-unroll', build / 'design.mlir',
                    build / 'unrolled.mlir'
                ], build / 'unroll.log')
                run([
                    binpath / 'circt-opt',
                    '--pass-pipeline=builtin.module(hw.module(llhd-deseq,llhd-lower-processes,cse,canonicalize,llhd-unroll-loops,cse,canonicalize,llhd-remove-control-flow,cse,canonicalize,llhd-combine-drives,llhd-sig2reg,cse,canonicalize))',
                    build / 'unrolled.mlir', '-o', build / 'structural.mlir'
                ], build / 'structural.log')
                structural = (build / 'structural.mlir').read_text()
                if any(('llhd.' + name) in structural
                       for name in ('process', 'drv', 'prb', 'wait', 'sig ')):
                    raise RuntimeError(
                        'Structural lowering retained event-driven LLHD operations'
                    )
                run([
                    binpath / 'arcilator', '--no-runtime',
                    '--no-generate-driver', '--until-before=llvm-lowering',
                    '--emit-mlir', '--state-file=' + str(build / 'state.json'),
                    build / 'structural.mlir', '-o', build / 'allocated.mlir'
                ], build / 'arc.log')
                run([
                    binpath / 'circt-opt',
                    '--pass-pipeline=builtin.module(hw-convert-bitcasts{allow-partial-conversion=false},arc-lower-arrays,arc-infer-context,lower-arc-to-llvm,cse,arc-canonicalizer)',
                    build / 'allocated.mlir', '-o', build / 'llvm.mlir'
                ], build / 'lowering.log')
                export_llvm(binpath, build)
                run([
                    binpath / 'opt', '--strip-debug', '-O2', '-S',
                    build / 'model.ll', '-o', build / 'optimized.ll'
                ], build / 'opt.log')
                run([
                    binpath / 'llc', '-O2', '-filetype=obj',
                    build / 'optimized.ll', '-o', build / 'model.o'
                ], build / 'llc.log')
                with (build / 'coral_weight_ci_top.h').open('w') as header:
                    subprocess.run([
                        'python3',
                        str(runtime / 'arcilator-header-cpp.py'),
                        str(build / 'state.json')
                    ],
                                   stdout=header,
                                   check=True)
                adapter(build / 'state.json', build)
                binary = build / 'model'
                run([
                    'clang++-18', '-O2', '-std=c++17', '-no-pie',
                    '-I' + str(build), '-I' + str(runtime), HERE.parent /
                    'tests/test_engine.cpp', build / 'model.o', '-o', binary
                ], build / 'compile.log')
            build_seconds = time.monotonic() - start
            for profile in PROFILES:
                for trial in range(5):
                    log = build / (
                        profile['name'] + '-' + str(trial) + '.jsonl'
                    )
                    command = [
                        binary, '--fixtures', fixture_path, '--seed', '7193',
                        '--latency-jitter', '0', '--mmio-stall-max', '3',
                        '--cpu-overlap',
                        str(int(name != 'E1'))
                    ]
                    for key in ('latency', 'beat_ii', 'capacity',
                                'stall_percent'):
                        command += [
                            '--' + key.replace('_', '-'),
                            str(profile[key])
                        ]
                    run(command, log)
                    records = [
                        json.loads(line)
                        for line in log.read_text().splitlines()
                        if line.startswith('{')
                    ]
                    cases = [
                        record for record in records
                        if record.get('kind') == 'case'
                    ]
                    summaries = [
                        record for record in records
                        if record.get('kind') == 'summary'
                    ]
                    if len(cases) != 192 or len(summaries) != 1 or summaries[
                            0]['status'] != 'PASS':
                        raise RuntimeError('Incomplete correctness evidence')
                    evidence['runs'].append({
                        'variant': name,
                        'profile': profile['name'],
                        'trial': trial,
                        'build_wall_seconds': build_seconds,
                        'summary': summaries[0],
                        'totals': {
                            key: sum(case[key]
                                     for case in cases)
                            for key in (
                                'busy_cycles', 'axi_testbench_command_cycles',
                                'storage_requests', 'storage_bytes',
                                'native_products'
                            )
                        }
                    })
                    rows.extend({
                        'variant': name,
                        'profile': profile['name'],
                        'trial': trial,
                        **{
                            key: value
                            for key, value in profile.items() if key != 'name'
                        },
                        **TIMING,
                        **case
                    }
                                for case in cases)
                    print(name, profile['name'], trial, 'PASS', flush=True)
        evidence['status'] = 'PASS'
    except Exception as error:
        evidence['status'] = 'FAIL'
        evidence['failure'] = str(error)
        raise
    finally:
        (out /
         'benchmark.json').write_text(json.dumps(evidence, indent=2) + '\n')
        if rows:
            with (out / 'cases.csv').open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
