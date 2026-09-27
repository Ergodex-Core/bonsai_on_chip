#!/usr/bin/env python3
"""Run one sealed-image DOT128 suite through an RTL or physical bus transport."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import struct
import subprocess
import time

import prepare_fixture

MAILBOX = 0x1000
PINNED_IMAGE_SHA256 = 'ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class Bus:

    def __init__(self, executable, log, timeout):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
        self.timeout = timeout
        self.log = log
        self.process = subprocess.Popen([str(executable)],
                                        stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE,
                                        stderr=log,
                                        bufsize=0)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.count = 0

    def request(self, command, timeout=None):
        duration = self.timeout if timeout is None else timeout
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('timeout must be positive and finite')
        if '\n' in command or '\r' in command:
            raise ValueError('transport command must be a single line')
        if self.process.poll() is not None:
            raise RuntimeError('bus transport exited before request')
        self.process.stdin.write((command + '\n').encode())
        self.process.stdin.flush()
        deadline = time.monotonic() + duration
        buffer = bytearray()
        while b'\n' not in buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError(
                    'bus transport request timed out: ' + command.split()[0]
                )
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('bus transport closed its response stream')
            buffer.extend(chunk)
            if len(buffer) > 65536:
                raise RuntimeError('oversized bus transport response')
        line, extra = bytes(buffer).split(b'\n', 1)
        if extra.strip():
            raise RuntimeError('unsolicited extra transport response')
        result = json.loads(line)
        self.count += 1
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise RuntimeError('bus transport failed: ' + str(result))
        return result

    @staticmethod
    def value(result):
        if type(result.get('resp')) is not int or result['resp'] != 0:
            raise RuntimeError('missing or unsuccessful read response')
        value = result.get('value')
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            raise RuntimeError('invalid register read value')
        return value

    def read(self, address):
        return self.value(self.request(f'READ {address}'))

    def write(self, address, value):
        response = self.request(f'WRITE {address} {value & 0xffffffff}')
        if type(response.get('resp')) is not int or response['resp'] != 0:
            raise RuntimeError(f'CSR write failed at {address:#x}: {response}')

    def wait(self, address, predicate, timeout=None):
        deadline = time.monotonic() + (
            self.timeout if timeout is None else timeout
        )
        while time.monotonic() < deadline:
            value = self.read(address)
            if predicate(value):
                return value
        raise TimeoutError(f'register polling timed out at {address:#x}')

    def close(self):
        self.selector.close()
        try:
            if self.process.poll() is None:
                try:
                    self.process.stdin.write(b'QUIT\n')
                    self.process.stdin.flush()
                    self.process.wait(timeout=5)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    self.process.kill()
                    self.process.wait(timeout=5)
        finally:
            self.process.stdin.close()
            self.process.stdout.close()
        if self.process.returncode != 0:
            raise RuntimeError(
                f'bus transport exited with status {self.process.returncode}'
            )


def reference(raw, inputs, native):
    values = [value if value < 128 else value - 256 for value in inputs]
    if native:
        scale_bits = raw[:2]
        scale = struct.unpack('<e', scale_bits)[0]
        if not math.isfinite(scale):
            return None, 0
        signs = [
            1 if raw[2 + lane // 8] & (1 << (lane % 8)) else -1
            for lane in range(128)
        ]
    else:
        scale_bits = b'\x00\x3c'
        codes = [(raw[lane // 4] >> (2 * (lane % 4))) & 3
                 for lane in range(128)]
        if 3 in codes:
            return None, 0
        signs = [{0: 0, 1: 1, 2: -1}[code] for code in codes]
    return sum(value * sign for value, sign in zip(values, signs)
               ), int.from_bytes(scale_bits, 'little')


def snapshot_sources(repo):
    paths = list((repo / 'hdl/verilog/first_slice').glob('*.sv'))
    paths += [
        Path(__file__), repo / 'utils/first_slice/prepare_fixture.py',
        repo / 'tests/first_slice/model_server.cpp',
        repo / 'tests/first_slice/first_slice_sim_top.sv'
    ]
    return {
        str(path.relative_to(repo)): sha256(path)
        for path in sorted(paths)
    }


def validate_cases(fixture, image, native):
    cases = fixture['cases' if native else 'synthetic_cases']
    if len(cases) != (396 if native else 6):
        raise ValueError('unexpected fixture case count')
    ids = [case.get('id') for case in cases]
    if any(type(value) is not str or not value
           for value in ids) or len(set(ids)) != len(ids):
        raise ValueError('duplicate or invalid fixture case IDs')
    if native:
        manifest_path = image.parent / 'manifest.json'
        if sha256(
                manifest_path
        ) != '21c664d0c029dc7f12440ad93a7fa6688c2b9e78b2143f6e9c179956793a7391':
            raise ValueError('canonical tensor manifest changed')
        manifest = json.loads(manifest_path.read_text())
        tensors = [
            tensor for tensor in manifest['tensors']
            if tensor['format'] == 'Q1_0'
        ]
        if len(tensors) != 197:
            raise ValueError('canonical tensor inventory changed')
        expected = []
        for tensor in tensors:
            expected.extend([
                (tensor, 0, 'first'),
                (tensor, tensor['payload_bytes'] // 18 - 1, 'last')
            ])
        query = next(
            tensor for tensor in tensors
            if tensor['name'] == 'blk.0.attn_q.weight'
        )
        for boundary in (64, 4096):
            block = next(
                i for i in range(query['payload_bytes'] // 18)
                if (query['offset'] + 18 * i) % boundary + 18 > boundary
            )
            expected.append((query, block, f'first_crossing_{boundary}'))
        for index, (case, (tensor, block,
                           selection)) in enumerate(zip(cases, expected)):
            target = {
                'id': f'q1_{index:03d}',
                'tensor': tensor['name'],
                'selection': selection,
                'row': block // (tensor['row_bytes'] // 18),
                'group': block % (tensor['row_bytes'] // 18),
                'logical_image_offset': tensor['offset'] + 18 * block
            }
            if any(case.get(key) != value for key, value in target.items()):
                raise ValueError(
                    'native case does not match frozen tensor/selection inventory'
                )
    else:
        expected_cases, expected_image = prepare_fixture.synthetic_cases()
        if cases != expected_cases or image.read_bytes() != expected_image:
            raise ValueError(
                'synthetic inventory differs from frozen six directed cases'
            )
    with image.open('rb') as stream:
        for index, case in enumerate(cases):
            offset = case['logical_image_offset' if native else 'image_offset']
            inputs = bytes.fromhex(case['inputs_int8_hex'])
            if type(offset) is not int or offset < 0 or len(
                    inputs) != 128 or offset + (18 if native else
                                                32) > image.stat().st_size:
                raise ValueError('invalid fixture dimensions/address')
            stream.seek(offset)
            raw = stream.read(18 if native else 32)
            oracle, scale_bits = reference(raw, inputs, native)
            if case.get('expected_valid', True) is not (oracle is not None):
                raise ValueError(
                    'fixture validity disagrees with image-derived oracle'
                )
            expected_dot = case.get('expected_dot_i32')
            if expected_dot != oracle or (oracle is not None
                                          and type(expected_dot) is not int):
                raise ValueError(
                    'fixture dot disagrees with image-derived oracle'
                )
            if native:
                if inputs != prepare_fixture.activations(index):
                    raise ValueError(
                        'native activations differ from frozen seeded inputs'
                    )
                if case.get('native_q1_0_hex') != raw.hex() or case.get(
                        'native_block_sha256') != hashlib.sha256(raw
                                                                 ).hexdigest():
                    raise ValueError(
                        'native fixture payload differs from canonical image'
                    )
                if case.get('scale_fp16_le_hex') != raw[:2].hex():
                    raise ValueError(
                        'native fixture scale differs from canonical image'
                    )
                scaled = struct.pack(
                    '<f',
                    struct.unpack('<e', raw[:2])[0] * oracle
                ).hex()
                if case.get('expected_host_scaled_fp32_le_hex') != scaled:
                    raise ValueError(
                        'native fixture scale reference differs from image oracle'
                    )
    return cases


def verify_build_manifest(executable, manifest_path, repo):
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != 1 or manifest.get('backend') not in (
            'verilator', 'aws_f2'):
        raise ValueError('unsupported transport build manifest')
    if manifest.get('binary_sha256') != sha256(executable):
        raise ValueError('transport binary differs from build manifest')
    required = {
        f'hdl/verilog/first_slice/{name}.sv'
        for name in
        ('weight_store', 'first_slice_top', 'dot128', 'f2_memory_bridge')
    }
    if manifest['backend'] == 'verilator':
        required.update((
            'tests/first_slice/model_server.cpp',
            'tests/first_slice/first_slice_sim_top.sv'
        ))
    else:
        required.update((
            'fpga/aws_f2/first_slice/transport.cpp',
            'fpga/aws_f2/first_slice/cl_bonsai_first_slice.sv',
            'fpga/aws_f2/first_slice/cl_id_defines.vh'
        ))
        if not manifest.get('agfi') or not manifest.get('dcp_sha256'):
            raise ValueError('physical manifest must bind AGFI and checkpoint')
    sources = manifest.get('source_sha256', {})
    if not required.issubset(sources):
        raise ValueError('transport build manifest misses compiled inputs')
    for relative, digest in sources.items():
        source = (repo / relative).resolve()
        source.relative_to(repo.resolve())
        if sha256(source) != digest:
            raise ValueError(
                'compiled source differs from current checkout: ' + relative
            )
    if not manifest.get('tool_version'):
        raise ValueError('transport build tool version missing')
    return manifest


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {
        'schema': 'coralnpu.first_slice.run.v1',
        'status': 'incomplete',
        'fpga_executed': False,
        'suite': args.suite,
        'cases': [],
        'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    }
    report_path = out / 'report.json'
    bus = None
    log = None

    def save():
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    save()
    try:
        fixture = json.loads(args.fixture.read_text())
        if fixture['schema'] != 'coralnpu.first_slice.fixture.v1':
            raise ValueError('unsupported fixture schema')
        native = args.suite == 'native'
        descriptor = fixture['canonical_image' if native else 'synthetic_image'
                             ]
        cases = fixture['cases' if native else 'synthetic_cases']
        image = args.image.resolve()
        image_hash = sha256(image)
        image_bytes = image.stat().st_size
        if image_hash != descriptor['sha256'] or image_bytes != descriptor[
                'bytes']:
            raise ValueError('image does not match fixture hash/size')
        if native and image_hash != PINNED_IMAGE_SHA256:
            raise ValueError('canonical checkpoint image changed')
        if image_bytes % 64 or image_bytes == 0:
            raise ValueError('image must contain complete 64-byte lines')
        cases = validate_cases(fixture, image, native)
        build_path = getattr(args, 'build_manifest', None
                             ) or Path(str(args.transport) + '.manifest.json')
        build = verify_build_manifest(
            args.transport, build_path,
            Path(__file__).resolve().parents[2]
        )
        report['build_manifest'] = build
        report.update(
            image_sha256=image_hash,
            image_bytes=image_bytes,
            fixture_sha256=sha256(args.fixture),
            source_sha256=snapshot_sources(
                Path(__file__).resolve().parents[2]
            ),
            transport_sha256=sha256(args.transport)
        )
        log = (out / 'transport.log').open('w')
        bus = Bus(args.transport.resolve(), log, args.timeout)
        info = bus.request('INFO')
        if info.get('backend') not in ('verilator', 'aws_f2'):
            raise ValueError('unsupported or undisclosed execution backend')
        if info['backend'] == 'aws_f2' and not info.get('agfi'):
            raise ValueError(
                'physical transport must identify its loaded AGFI'
            )
        if info['backend'] != build['backend'] or info.get(
                'agfi') != build.get('agfi'):
            raise ValueError(
                'running transport/image differs from build manifest'
            )
        report['transport'] = info
        if bus.read(0) != 0x10000 or bus.read(
                MAILBOX) != 0x444f5431 or bus.read(MAILBOX + 4) != 0x10000:
            raise ValueError('hardware identity/ABI mismatch')
        if bus.read(4) & 7:
            raise RuntimeError(
                'store is not EMPTY; coordinated cold reset/reload required'
            )
        for offset, value in ((0x110, 0), (0x114, 0), (0x118, image_bytes),
                              (0x11c, image_bytes >> 32)):
            bus.write(offset, value)
        hash_words = struct.unpack('<8I', bytes.fromhex(image_hash))
        for index, word in enumerate(hash_words):
            bus.write(0x120 + index * 4, word)
        bus.write(0x100, 1)
        bus.wait(4, lambda status: status & 7 == 1)
        for path in (image, out):
            if any(ord(char) < 32 for char in str(path)):
                raise ValueError(
                    'control characters in transport paths are unsupported'
                )
        report['load'] = bus.request(
            'LOAD ' + json.dumps(str(image), ensure_ascii=False),
            args.bulk_timeout
        )
        bus.write(0x100, 2)
        bus.wait(0x104, lambda value: value & 1)
        readback = out / 'readback.bin'
        report['readback'] = bus.request(
            'DUMP ' + json.dumps(str(readback), ensure_ascii=False) +
            f' {image_bytes}', args.bulk_timeout
        )
        if readback.stat().st_size != image_bytes or sha256(readback
                                                            ) != image_hash:
            raise RuntimeError('whole-image DDR readback failed')
        report['readback_sha256'] = sha256(readback)
        for index, word in enumerate(hash_words):
            bus.write(0x140 + index * 4, word)
        bus.write(0x100, 3)
        status = bus.wait(4, lambda value: value & (1 << 8))
        if status & 7 != 3 or not status & (1 << 9):
            raise RuntimeError('image did not become SEALED and LOCKED')
        epoch = bus.read(0xc)
        report['epoch'] = epoch
        rejected_before = bus.read(0x60)
        with readback.open('rb') as first_bytes:
            first_word = int.from_bytes(first_bytes.read(4), 'little')
        poke = bus.request(f'POKE 0 {first_word ^ 0xffffffff}')
        if info['backend'] == 'verilator' and poke.get('resp') not in (2, 3):
            raise RuntimeError(
                'simulated sealed write did not return an AXI error'
            )
        if info['backend'] == 'aws_f2' and poke.get('posted') is not True:
            raise RuntimeError('physical posted-write semantics not disclosed')
        bus.wait(0x60, lambda value: value > rejected_before)
        check = Bus.value(bus.request('PEEK 0'))
        if check != first_word:
            raise RuntimeError('BAR4 write changed the sealed image')
        report['sealed_write_rejected'] = True
        bus.write(0x100, 4)
        bus.wait(4, lambda value: value & 7 == 4)
        with image.open('rb') as stream:
            for index, case in enumerate(cases):
                offset = case[
                    'logical_image_offset' if native else 'image_offset']
                inputs = bytes.fromhex(case['inputs_int8_hex'])
                if len(inputs) != 128 or offset < 0 or offset + (
                        18 if native else 32) > image_bytes:
                    raise ValueError('invalid fixture dimensions/address')
                stream.seek(offset)
                raw = stream.read(18 if native else 32)
                oracle, scale_bits = reference(raw, inputs, native)
                if oracle is not None and oracle != case['expected_dot_i32']:
                    raise ValueError(
                        'fixture integer reference differs from image-derived oracle'
                    )
                if oracle is None and case.get('expected_valid', True):
                    raise ValueError(
                        'fixture validity differs from image-derived oracle'
                    )
                for lane, value in enumerate(struct.unpack('<32I', inputs)):
                    bus.write(MAILBOX + 0x80 + lane * 4, value)
                for address, value in ((0x18, index + 1), (0x1c,
                                                           1 if native else 2),
                                       (0x20, 128), (0x24, offset),
                                       (0x28, offset >> 32), (0x2c, epoch)):
                    bus.write(MAILBOX + address, value)
                result = {
                    'id': case['id'],
                    'offset': offset,
                    'expected_dot_i32': oracle,
                    'submitted': False,
                    'completion_observed': False,
                    'passed': False
                }
                report['cases'].append(result)
                bus.write(MAILBOX + 0x10, 1)
                result['submitted'] = True
                save()
                bus.wait(MAILBOX + 0xc, lambda value: value & 2)
                result['completion_observed'] = True
                if info['backend'] == 'aws_f2':
                    report['fpga_executed'] = True
                save()
                actual_status = bus.read(MAILBOX + 0x14)
                actual_word = bus.read(MAILBOX + 0x30)
                actual = actual_word if actual_word < 0x80000000 else actual_word - 0x100000000
                actual_scale = bus.read(MAILBOX + 0x34)
                if bus.read(MAILBOX + 0x38) != index + 1:
                    raise RuntimeError('stale completion cookie')

                def counter(address):
                    return bus.read(MAILBOX + address) | (
                        bus.read(MAILBOX + address + 4) << 32
                    )

                result.update({
                    'status': actual_status,
                    'actual_dot_i32': actual,
                    'expected_dot_i32': oracle,
                    'actual_scale_bits': actual_scale,
                })
                save()
                for name, address in (('cycles', 0x40), ('memory_stall_cycles',
                                                         0x48),
                                      ('read_requests', 0x50)):
                    result[name] = counter(address)
                if oracle is None:
                    if actual_status != 7 or actual != 0 or actual_scale != 0:
                        raise RuntimeError(
                            'invalid ternary encoding did not fail closed'
                        )
                else:
                    if actual_status != 0 or actual != oracle or actual_scale != scale_bits:
                        raise RuntimeError(
                            'FPGA/RTL dot result differs from independent integer oracle'
                        )
                    scale = struct.unpack(
                        '<e', scale_bits.to_bytes(2, 'little')
                    )[0]
                    result['host_scaled_fp32_le_hex'] = struct.pack(
                        '<f', scale * actual
                    ).hex()
                    if native and result['host_scaled_fp32_le_hex'] != case[
                            'expected_host_scaled_fp32_le_hex']:
                        raise RuntimeError(
                            'host FP32 scaling differs from frozen numeric reference'
                        )
                expected_reads = 1 + (
                    offset % 64 + (18 if native else 32) > 64
                )
                if result['cycles'] == 0 or result['read_requests'
                                                   ] != expected_reads:
                    raise RuntimeError(
                        'operation counters do not show a memory-backed hardware operation'
                    )
                # Completion and result must remain stable until explicit ACK.
                if bus.read(MAILBOX + 0x30) != actual_word or bus.read(
                        MAILBOX + 0x14) != actual_status:
                    raise RuntimeError(
                        'completion changed before acknowledgement'
                    )
                result['passed'] = True
                bus.write(MAILBOX + 0x10, 2)
                save()
        bus.write(0x100, 5)
        bus.wait(4, lambda value: value & 7 == 3 and not value & (1 << 10))
        if bus.read(0x40) != 0:
            raise RuntimeError('store recorded a protocol/backend fault')
        final_readback = out / 'readback-final.bin'
        bus.request(
            'DUMP ' + json.dumps(str(final_readback), ensure_ascii=False) +
            f' {image_bytes}', args.bulk_timeout
        )
        if final_readback.stat().st_size != image_bytes or sha256(
                final_readback) != image_hash:
            raise RuntimeError('image changed during execution')
        report['final_readback_sha256'] = sha256(final_readback)
        final_info = bus.request('INFO')
        if any(final_info.get(key) != info.get(key)
               for key in ('backend', 'agfi')):
            raise RuntimeError(
                'loaded image identity changed during execution'
            )
        final_status = bus.read(4)
        if final_status & 7 != 3 or final_status & 0x300 != 0x300 or bus.read(
                0xc) != epoch:
            raise RuntimeError(
                'sealed image state or epoch changed during execution'
            )
        report.update(
            status='PASS',
            passed=len(report['cases']),
            failed=0,
            rejected_cases=sum(
                case['status'] != 0 for case in report['cases']
            ),
            transactions=bus.count,
            final_transport=final_info,
            fpga_scale_stage=False,
            scope=
            'DOT128 group arithmetic and sealed DDR image; host scales result; no full model or CoralNPU firmware execution'
        )
    except Exception as error:
        report.update(
            status='FAIL', error=f'{type(error).__name__}: {error}', failed=1
        )
        raise
    finally:
        cleanup_errors = []
        for resource in (bus, log):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    cleanup_errors.append(f'{type(error).__name__}: {error}')
        if cleanup_errors:
            report.update(
                status='FAIL', failed=1, cleanup_errors=cleanup_errors
            )
        report['finished_utc'] = time.strftime(
            '%Y-%m-%dT%H:%M:%SZ', time.gmtime()
        )
        save()
    if report['status'] != 'PASS':
        raise RuntimeError('run did not complete cleanly; inspect report.json')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--transport', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--build-manifest', type=Path)
    parser.add_argument(
        '--suite', choices=('native', 'synthetic'), required=True
    )
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--bulk-timeout', type=float, default=1800)
    args = parser.parse_args()
    result = run(args)
    print(
        json.dumps({
            'status': result['status'],
            'passed': result['passed'],
            'fpga_executed': result['fpga_executed']
        })
    )


if __name__ == '__main__':
    main()
