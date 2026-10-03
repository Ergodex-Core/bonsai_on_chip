"""Bind completed traces to the current sources; fail closed on stale evidence."""
import hashlib
import json
import pathlib
import subprocess
import sys

SOURCES = [
    'hdl/verilog/rvv/common/adder.sv',
    'hdl/verilog/rvv/common/barrel_shifter.sv',
    'hdl/verilog/rvv/common/compressor_3to2.sv',
    'tests/ci/arithmetic_top.sv',
    'tests/ci/arithmetic.cpp',
    'tests/ci/run_smoke.sh',
    'tests/ci/evidence.py',
    'tests/ci/install_tools.sh',
    'tests/ci/test_evidence.py',
]
SAMPLES = 151552


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    operation, backend, repo_arg, out_arg, tools_arg = sys.argv[1:]
    repo, output, tools = map(pathlib.Path, (repo_arg, out_arg, tools_arg))
    current = {name: digest(repo / name) for name in SOURCES}
    manifest = output / 'manifest.json'
    if operation == 'start':
        value = {
            'status':
            'running',
            'backend':
            backend,
            'source_sha256':
            current,
            'revision':
            subprocess.check_output([
                'git', '-c', 'safe.directory=' + str(repo), '-C',
                str(repo), 'rev-parse', 'HEAD'
            ],
                                    text=True).strip()
        }
        save(manifest, value)
        binaries = ([
            'verilator/bin/verilator', 'verilator/bin/verilator_bin'
        ] if backend == 'verilator' else [
            'circt/bin/arcilator', 'circt/bin/circt-verilog', 'circt/bin/opt',
            'circt/bin/llc', 'circt/runtime/arcilator-header-cpp.py',
            'circt/runtime/arcilator-runtime.h'
        ])
        value['tool_sha256'] = {
            name: digest(tools / name)
            for name in binaries
        }
        save(manifest, value)
    elif operation == 'finish':
        value = json.loads(manifest.read_text())
        require(value['status'] == 'running', 'missing in-progress run')
        require(
            value['source_sha256'] == current, 'sources changed during run'
        )
        require((output / 'result.txt').read_text().strip() ==
                f'PASS samples={SAMPLES} checked_outputs={SAMPLES * 7}',
                'oracle result incomplete')
        require(
            sum(1 for _ in (output / 'trace.csv').open()) == SAMPLES + 1,
            'trace incomplete'
        )
        if backend == 'verilator':
            handshake = json.loads(
                (output / 'handshake/verilator-inputs.json').read_text()
            )
            require(
                handshake['status'] == 'complete',
                'handshake regression incomplete'
            )
        value.update(
            status='complete',
            samples=SAMPLES,
            checked_outputs=SAMPLES * 7,
            trace_sha256=digest(output / 'trace.csv')
        )
        save(manifest, value)
    elif operation == 'compare':
        result = output / 'results.json'
        save(result, {'status': 'incomplete'})
        runs = {}
        for name in ('verilator', 'arcilator'):
            folder = output / name
            value = json.loads((folder / 'manifest.json').read_text())
            require(
                value['status'] == 'complete' and value['backend'] == name,
                name + ': incomplete backend'
            )
            require(
                value['source_sha256'] == current, name + ': stale sources'
            )
            require(
                value['trace_sha256'] == digest(folder / 'trace.csv'),
                name + ': changed trace'
            )
            require(value['samples'] == SAMPLES, name + ': incomplete samples')
            runs[name] = value
        require(
            runs['verilator']['revision'] == runs['arcilator']['revision'],
            'revision mismatch'
        )
        require(
            runs['verilator']['trace_sha256'] == runs['arcilator']
            ['trace_sha256'], 'differential trace mismatch'
        )
        save(
            result, {
                'status': 'complete',
                'scope':
                'production adder (8/32 bit), barrel shifter (32 bit), compressor (32 bit); Verilator handshake separately',
                'samples_per_backend': SAMPLES,
                'checked_outputs_per_backend': SAMPLES * 7,
                'backends': runs,
                'passed': 2,
                'failed': 0,
                'skipped': 0
            }
        )
        print(
            f'PASS full trace parity: {SAMPLES} samples, {SAMPLES * 7} outputs per backend'
        )
    else:
        raise RuntimeError('unknown operation')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError,
            subprocess.CalledProcessError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
