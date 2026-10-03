"""Negative regression checks using actual successful simulator evidence."""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

from evidence import SOURCES

repo, evidence = map(pathlib.Path, sys.argv[1:])
with tempfile.TemporaryDirectory(prefix='bonsai-evidence-') as temporary:
    root = pathlib.Path(temporary)
    source = root / 'source'
    output = root / 'output'
    for name in SOURCES:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, target)
    for backend in ('verilator', 'arcilator'):
        target = output / backend
        target.mkdir(parents=True)
        for name in ('manifest.json', 'trace.csv'):
            shutil.copyfile(evidence / backend / name, target / name)

    def check(label, expected_pass):
        result = subprocess.run([sys.executable, str(repo / 'tests/ci/evidence.py'), 'compare',
                                 'both', str(source), str(output), str(root / 'tools')],
                                capture_output=True, text=True)
        if (result.returncode == 0) != expected_pass:
            raise RuntimeError(label + ': unexpected status\n' + result.stdout + result.stderr)
        if not expected_pass and json.loads((output / 'results.json').read_text())['status'] == 'complete':
            raise RuntimeError(label + ': failure retained a passing report')
        print('PASS evidence regression: ' + label)

    check('accept completed matched evidence', True)
    trace = output / 'arcilator/trace.csv'
    with trace.open('a') as stream:
        stream.write('corrupted\n')
    check('reject altered trace and invalidate prior PASS', False)
    shutil.copyfile(evidence / 'arcilator/trace.csv', trace)
    manifest = output / 'arcilator/manifest.json'
    value = json.loads(manifest.read_text())
    value['status'] = 'running'
    manifest.write_text(json.dumps(value))
    check('reject incomplete backend', False)
    shutil.copyfile(evidence / 'arcilator/manifest.json', manifest)
    changed = source / 'hdl/verilog/rvv/common/adder.sv'
    changed.write_text(changed.read_text() + '\n// changed after test\n')
    check('reject stale source', False)
