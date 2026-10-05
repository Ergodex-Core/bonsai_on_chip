#!/usr/bin/env python3
"""Compare completed DDR/HBM RTL runs and their pinned reference evidence.

This reads real validation artifacts; it never simulates arithmetic or promotes
simulation evidence to a physical FPGA pass. --self-test mutates copies of the
actual reports to verify that incomplete or corrupted evidence is rejected.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics
import struct
import sys
import time

REPO = Path(__file__).resolve().parents[2]
RTL = tuple(
    'hdl/verilog/first_slice/' + name + '.sv' for name in
    ('weight_store', 'dot128', 'first_slice_top', 'f2_memory_bridge')
)
REFERENCE_SOURCES = (
    'utils/first_slice/prepare_fixture.py', 'utils/first_slice/run.py',
    'utils/weightstore/pack_image.py'
)
SOURCE_PIN = {
    'model': 'prism-ml/Bonsai-1.7B-gguf',
    'revision': '210a9e99f79cb184909d49595906526eb2b3dd9a',
    'file': 'Bonsai-1.7B-Q1_0.gguf',
    'bytes': 248302272,
    'sha256':
    '3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3'
}
IMAGE_PINS = {
    'native': (
        242357184,
        'ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99'
    ),
    'synthetic':
    (192, '966088f726e00ad969b7c7bc5ec64e9a2eee1e6734660f47fb2e222fb121ae36')
}
BINARIES = {
    'ddr': 'first_slice_sim_top-obj/Vfirst_slice_sim_top',
    'hbm': 'obj/Vhbm_rom_sim_top'
}
MANIFEST_PIN = '21c664d0c029dc7f12440ad93a7fa6688c2b9e78b2143f6e9c179956793a7391'
SYNTHETIC_IDS = (
    'synthetic_zero', 'synthetic_positive_min_input',
    'synthetic_negative_min_input', 'synthetic_mixed',
    'synthetic_invalid_first', 'synthetic_invalid_last'
)
EXACT_FIELDS = (
    'id', 'offset', 'status', 'expected_dot_i32', 'actual_dot_i32',
    'actual_scale_bits', 'host_scaled_fp32_le_hex', 'read_requests'
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def load_evidence(root, backend):
    """Hash actual input bytes, including both complete image readbacks."""
    evidence = {'root': str(root), 'artifacts': {}, 'suites': {}}

    def record(relative, parse=False):
        path = root / relative
        if parse:
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            size = len(raw)
        else:
            sha, size = digest(path), path.stat().st_size
        evidence['artifacts'][relative] = {'sha256': sha, 'bytes': size}
        return json.loads(raw) if parse else None

    evidence['validation'] = record('validation.json', True)
    evidence['fixture'] = record('fixture/fixture.json', True)
    record('native/manifest.json')
    record(BINARIES[backend])
    for suite in IMAGE_PINS:
        evidence['suites'][suite] = record(suite + '-run/report.json', True)
        for name in ('readback.bin', 'readback-final.bin'):
            record(suite + '-run/' + name)
    return evidence


def check_sources(observed, expected, label):
    require(isinstance(observed, dict), label + ': missing source hashes')
    for name, sha in expected.items():
        require(
            observed.get(name) == sha,
            label + ': source provenance differs: ' + name
        )


def integer(value, label, minimum=0):
    require(
        type(value) is int and value >= minimum,
        label + ': invalid integer counter'
    )
    return value


def validate_evidence(data, backend, current_sources):
    validation, fixture = data['validation'], data['fixture']
    require(
        validation.get('status') == 'PASS'
        and validation.get('fpga_executed') is False,
        backend + ': validation is not a completed simulation PASS'
    )
    require(
        validation.get('memory_backend', 'ddr') == backend,
        backend + ': validation memory identity differs'
    )
    check_sources(validation.get('source_sha256'), current_sources, backend)
    require(
        fixture.get('schema') == 'coralnpu.first_slice.fixture.v1'
        and fixture.get('source') == SOURCE_PIN,
        backend + ': fixture checkpoint pin differs'
    )
    require(
        fixture.get('numerics', {}).get('version') ==
        'q1-sign-int8-dot128-host-fp32-scale.v1',
        backend + ': numerical reference version differs'
    )
    require(
        fixture.get('tool_sha256') ==
        current_sources['utils/first_slice/prepare_fixture.py'],
        backend + ': reference generator hash differs'
    )
    require(
        data['artifacts']['native/manifest.json']['sha256'] == MANIFEST_PIN
        and fixture.get('canonical_image', {}).get('manifest_sha256')
        == MANIFEST_PIN, backend + ': canonical tensor manifest differs'
    )
    fixture_hash = data['artifacts']['fixture/fixture.json']['sha256']
    compiled_hashes = None
    for suite, (image_bytes, image_hash) in IMAGE_PINS.items():
        label = backend + '/' + suite
        report = data['suites'][suite]
        expected_ids = ([f'q1_{index:03d}' for index in range(396)]
                        if suite == 'native' else list(SYNTHETIC_IDS))
        cases = report.get('cases')
        require(
            report.get('schema') == 'coralnpu.first_slice.run.v1'
            and report.get('suite') == suite and report.get('status') == 'PASS'
            and report.get('passed') == len(expected_ids)
            and report.get('failed') == 0
            and report.get('fpga_executed') is False and
            report.get('rejected_cases') == (0 if suite == 'native' else 2),
            label + ': incomplete passing suite'
        )
        require(
            isinstance(cases, list) and len(cases) == len(expected_ids),
            label + ': missing case inventory'
        )
        require(
            all(isinstance(case, dict) for case in cases)
            and [case.get('id') for case in cases] == expected_ids,
            label + ': duplicate, missing or reordered case identities'
        )
        summary = validation.get('suite_results', {}).get(suite, {})
        require(
            summary.get('status') == 'PASS'
            and summary.get('passed') == len(expected_ids)
            and summary.get('report_sha256')
            == data['artifacts'][suite + '-run/report.json']['sha256'],
            label + ': suite report hash/count differs from validation'
        )
        require(
            report.get('memory_backend') == backend,
            label + ': reported memory identity differs'
        )
        identity = {
            'store_abi': 0x10000,
            'memory_backend_id': 2 if backend == 'ddr' else 3,
            'mailbox_id': 0x444f5431,
            'mailbox_abi': 0x10000
        }
        require(
            report.get('hardware_identity') == identity
            and report.get('final_hardware_identity') == identity,
            label + ': hardware identity differs or changed'
        )
        for key in ('transport', 'final_transport', 'build_manifest'):
            transport = report.get(key, {})
            require(
                transport.get('backend') == 'verilator'
                and transport.get('memory_backend', 'ddr') == backend,
                label + ': execution/memory identity differs in ' + key
            )
        require(
            report.get('fixture_sha256') == fixture_hash,
            label + ': fixture hash differs'
        )
        require(
            report.get('image_bytes') == image_bytes and all(
                report.get(key) == image_hash for key in
                ('image_sha256', 'readback_sha256', 'final_readback_sha256')
            ), label + ': full image hash/size differs'
        )
        descriptor = fixture['canonical_image' if suite ==
                             'native' else 'synthetic_image']
        require(
            descriptor.get('sha256') == image_hash
            and descriptor.get('bytes') == image_bytes,
            label + ': fixture image pin differs'
        )
        for name in ('readback.bin', 'readback-final.bin'):
            require(
                data['artifacts'][suite + '-run/' + name] == {
                    'bytes': image_bytes,
                    'sha256': image_hash
                }, label + ': actual full readback differs: ' + name
            )
        require(
            report.get('sealed_write_rejected') is True
            and report.get('fpga_scale_stage') is False,
            label + ': seal/host scale evidence missing'
        )
        integer(report.get('epoch'), label + '/epoch', 1)
        check_sources(
            report.get('source_sha256'), {
                name: sha
                for name, sha in current_sources.items()
                if name != 'utils/weightstore/pack_image.py'
            }, label
        )
        build = report['build_manifest']
        check_sources(
            build.get('source_sha256'),
            {name: current_sources[name]
             for name in RTL}, label + '/compiled'
        )
        require(
            report.get('transport_sha256') == build.get('binary_sha256')
            and build.get('binary_sha256')
            == data['artifacts'][BINARIES[backend]]['sha256'],
            label + ': binary build identity differs'
        )
        if compiled_hashes is None:
            compiled_hashes = build['source_sha256']
        require(
            build['source_sha256'] == compiled_hashes,
            label + ': native/synthetic compiled sources differ'
        )
        reference = fixture['cases' if suite ==
                            'native' else 'synthetic_cases']
        require([case.get('id') for case in reference] == expected_ids,
                label + ': reference case inventory differs')
        for case, expected in zip(cases, reference):
            case_label = label + '/' + case['id']
            require(
                all(
                    case.get(key) is True
                    for key in ('submitted', 'completion_observed', 'passed')
                ), case_label + ': completion evidence missing'
            )
            offset = expected['logical_image_offset' if suite ==
                              'native' else 'image_offset']
            dot = expected['expected_dot_i32']
            valid = dot is not None
            scale = (
                int.from_bytes(
                    bytes.fromhex(expected['scale_fp16_le_hex']), 'little'
                ) if suite == 'native' else (0x3c00 if valid else 0)
            )
            fields = {
                'offset':
                offset,
                'expected_dot_i32':
                dot,
                'actual_dot_i32':
                dot if valid else 0,
                'actual_scale_bits':
                scale,
                'status':
                0 if valid else 7,
                'read_requests':
                1 + (offset % 64 + (18 if suite == 'native' else 32) > 64)
            }
            if valid:
                fields['host_scaled_fp32_le_hex'] = (
                    expected['expected_host_scaled_fp32_le_hex']
                    if suite == 'native' else struct.pack('<f', dot).hex()
                )
            else:
                require(
                    'host_scaled_fp32_le_hex' not in case,
                    case_label + ': invalid result was host scaled'
                )
            for key, value in fields.items():
                require(
                    key in case and type(case[key]) is type(value)
                    and case[key] == value,
                    case_label + ': ' + key + ' differs from fixture'
                )
            cycles = integer(case.get('cycles'), case_label + '/cycles', 1)
            stalls = integer(
                case.get('memory_stall_cycles'), case_label + '/stalls'
            )
            require(
                stalls <= cycles,
                case_label + ': stalls exceed operation cycles'
            )
    return compiled_hashes


def performance(report):
    result = {}
    for key, unit in (('cycles', 'core clock cycles'), ('memory_stall_cycles',
                                                        'core clock cycles'),
                      ('read_requests', 'logical 64-byte line requests')):
        values = [case[key] for case in report['cases']]
        result[key] = {
            'unit': unit,
            'count': len(values),
            'total': sum(values),
            'min': min(values),
            'max': max(values),
            'mean': statistics.mean(values),
            'median': statistics.median(values)
        }
    return result


def compare(evidence, current_sources):
    compiled = {
        backend: validate_evidence(data, backend, current_sources)
        for backend, data in evidence.items()
    }
    require(
        evidence['ddr']['artifacts']['fixture/fixture.json'] == evidence['hbm']
        ['artifacts']['fixture/fixture.json'], 'DDR/HBM fixtures differ'
    )
    for sources in compiled.values():
        for relative, sha in sources.items():
            path = (REPO / relative).resolve()
            path.relative_to(REPO)
            require(
                digest(path) == sha, 'compiled source changed: ' + relative
            )
    suites = {}
    for suite in IMAGE_PINS:
        reports = {
            backend: data['suites'][suite]
            for backend, data in evidence.items()
        }
        for ddr, hbm in zip(reports['ddr']['cases'], reports['hbm']['cases']):
            require(
                all((key in ddr) == (key in hbm)
                    and ddr.get(key) == hbm.get(key) for key in EXACT_FIELDS),
                suite + '/' + ddr['id'] + ': DDR/HBM result differs'
            )
        suites[suite] = {
            'cases_compared': len(reports['ddr']['cases']),
            'exact_fields': list(EXACT_FIELDS),
            'performance': {
                backend: performance(report)
                for backend, report in reports.items()
            }
        }
    return suites


def mutation_tests(evidence, current_sources):
    """Negative tests use real captured results, never generated positive cases."""
    mutations = (
        (
            'missing_case', ('suites', 'native', 'cases'), 'pop', None,
            'missing case inventory'
        ),
        (
            'duplicate_case', ('suites', 'native', 'cases', 1, 'id'), 'set',
            'q1_000', 'case identities'
        ),
        (
            'raw_scale', ('suites', 'native', 'cases', 0, 'actual_scale_bits'),
            'set', 0, 'actual_scale_bits differs'
        ),
        (
            'host_fp32',
            ('suites', 'native', 'cases', 0, 'host_scaled_fp32_le_hex'), 'set',
            '00000000', 'host_scaled_fp32_le_hex differs'
        ),
        (
            'hardware_identity',
            ('suites', 'native', 'hardware_identity',
             'memory_backend_id'), 'set', 2, 'hardware identity differs'
        ),
        (
            'final_identity', (
                'suites', 'native', 'final_hardware_identity',
                'memory_backend_id'
            ), 'set', 2, 'hardware identity differs'
        ),
        (
            'full_image_hash', ('suites', 'native', 'final_readback_sha256'),
            'set', '0' * 64, 'full image hash/size differs'
        ),
        (
            'actual_readback_hash',
            ('artifacts', 'native-run/readback-final.bin',
             'sha256'), 'set', '0' * 64, 'actual full readback differs'
        ),
        (
            'read_requests', ('suites', 'native', 'cases', 0, 'read_requests'),
            'set', 0, 'read_requests differs'
        ),
        (
            'shared_source', ('validation', 'source_sha256', RTL[0]), 'set',
            '0' * 64, 'source provenance differs'
        ),
    )
    results = []
    for name, keys, operation, value, diagnostic in mutations:
        changed = deepcopy(evidence['hbm'])
        target = changed
        for key in keys[:-1]:
            target = target[key]
        if operation == 'pop':
            target[keys[-1]].pop()
        else:
            target[keys[-1]] = value
        try:
            validate_evidence(changed, 'hbm', current_sources)
        except ValueError as error:
            require(
                diagnostic in str(error),
                name + ': wrong rejection: ' + str(error)
            )
            results.append({
                'name': name,
                'status': 'PASS',
                'rejection': str(error)
            })
        else:
            raise ValueError(name + ': corrupted evidence was accepted')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ddr', type=Path, required=True)
    parser.add_argument('--hbm', type=Path, required=True)
    parser.add_argument(
        '--out', type=Path, required=True, help='New comparison JSON file'
    )
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    report = {
        'schema': 'coralnpu.hbm_rom.backend_comparison.v1',
        'status': 'incomplete',
        'fpga_executed': False,
        'scope':
        'DDR/HBM RTL DOT128 equivalence; native binary Q1_0 and separate synthetic ternary cases; no full model or physical FPGA execution',
        'performance_scope':
        'Mailbox operation counters only; simulated latency is not physical timing or throughput',
        'command': [sys.executable] + sys.argv,
        'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'comparator_sha256': digest(__file__),
        'reference_checkpoint': SOURCE_PIN
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as output:
        try:
            current = {
                name: digest(REPO / name)
                for name in RTL + REFERENCE_SOURCES
            }
            report['shared_source_sha256'] = current
            evidence = {}
            report['inputs'] = {}
            for backend in ('ddr', 'hbm'):
                data = load_evidence(getattr(args, backend).resolve(), backend)
                evidence[backend] = data
                report['inputs'][backend] = {
                    'directory':
                    data['root'],
                    'artifacts':
                    data['artifacts'],
                    'compiled_source_sha256':
                    data['suites']['native']['build_manifest']['source_sha256']
                }
            report['suites'] = compare(evidence, current)
            if args.self_test:
                report['mutation_tests'] = mutation_tests(evidence, current)
            require(
                current == {name: digest(REPO / name)
                            for name in current},
                'Shared sources changed during comparison'
            )
            report.update(
                status='PASS',
                cases_compared=402,
                mutation_checks=len(report.get('mutation_tests', []))
            )
        except Exception as error:
            report.update(
                status='FAIL', error=f'{type(error).__name__}: {error}'
            )
        finally:
            report['finished_utc'] = time.strftime(
                '%Y-%m-%dT%H:%M:%SZ', time.gmtime()
            )
            output.write(json.dumps(report, indent=2) + '\n')
    print(
        json.dumps({
            'status': report['status'],
            'report': str(args.out),
            'cases_compared': report.get('cases_compared', 0),
            'mutation_checks': report.get('mutation_checks', 0)
        })
    )
    if report['status'] != 'PASS':
        raise SystemExit(report['error'])


if __name__ == '__main__':
    main()
