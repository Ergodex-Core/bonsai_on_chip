"""Failure-injection tests; mocked transports are never hardware evidence."""
import argparse
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

import prepare_fixture
import run as runner

REAL_BUS = runner.Bus


@contextmanager
def mock_bus(bus):
    with patch.object(runner, 'Bus', return_value=bus) as constructor:
        constructor.value = REAL_BUS.value
        yield constructor


class SyntheticBus:
    """Minimal control model used only to inject host-runner failures."""

    def __init__(
        self,
        image,
        cases,
        fail_case=None,
        fail_counter=False,
        bad_invalid_result=False,
        fail_close=False,
        corrupt_final_dump=False,
        change_final_backend=False,
        wrong_read_count=False,
        change_final_epoch=False,
        lose_final_seal=False,
        memory_backend=None,
        hardware_backend_id=2,
        change_final_memory_backend=False,
        change_final_identity=None
    ):
        self.image = bytes(image)
        self.cases = cases
        self.fail_case = fail_case
        self.fail_counter = fail_counter
        self.bad_invalid_result = bad_invalid_result
        self.fail_close = fail_close
        self.corrupt_final_dump = corrupt_final_dump
        self.change_final_backend = change_final_backend
        self.wrong_read_count = wrong_read_count
        self.change_final_epoch = change_final_epoch
        self.lose_final_seal = lose_final_seal
        self.memory_backend = memory_backend
        self.hardware_backend_id = hardware_backend_id
        self.change_final_memory_backend = change_final_memory_backend
        self.change_final_identity = change_final_identity
        self.ran = False
        self.info_count = 0
        self.dump_count = 0
        self.state = 0
        self.registers = {}
        self.cookie = 0
        self.rejected = 0
        self.count = 0
        self.closed = False

    def request(self, command, timeout=None):
        self.count += 1
        operation, _, payload = command.partition(' ')
        if operation == 'INFO':
            self.info_count += 1
            info = {
                'ok':
                True,
                'backend': (
                    'verilator' if self.change_final_backend
                    and self.info_count > 1 else 'aws_f2'
                ),
                'agfi':
                'agfi-MOCK-UNIT-TEST-ONLY'
            }
            if self.memory_backend is not None:
                info['memory_backend'] = self.memory_backend
            if self.change_final_memory_backend and self.info_count > 1:
                info['memory_backend'] = (
                    'ddr' if self.memory_backend == 'hbm' else 'hbm'
                )
            return info
        if operation == 'LOAD':
            if Path(json.loads(payload)).read_bytes() != self.image:
                raise RuntimeError('mock received wrong input image')
            return {'ok': True, 'bytes': len(self.image)}
        if operation == 'DUMP':
            path, used = json.JSONDecoder().raw_decode(payload)
            size = int(payload[used:])
            if size != len(self.image):
                raise RuntimeError('mock received wrong readback size')
            self.dump_count += 1
            data = bytearray(self.image)
            if self.corrupt_final_dump and self.dump_count > 1:
                data[-1] ^= 1
            Path(path).write_bytes(data)
            return {'ok': True, 'bytes': size}
        if operation == 'POKE':
            self.rejected += 1
            return {'ok': True, 'resp': None, 'posted': True}
        if operation == 'PEEK':
            return {
                'ok': True,
                'resp': 0,
                'value': int.from_bytes(self.image[:4], 'little')
            }
        raise RuntimeError('unknown mock request ' + operation)

    def write(self, address, value):
        self.count += 1
        self.registers[address] = value
        if address == 0x100:
            self.state = {1: 1, 2: 2, 3: 3, 4: 4, 5: 3}[value]
            if value == 4:
                self.ran = True
        if address == runner.MAILBOX + 0x10 and value == 1:
            self.cookie = self.registers[runner.MAILBOX + 0x18]

    def read(self, address):
        self.count += 1
        identity = {
            0: 0x10000,
            8: self.hardware_backend_id,
            runner.MAILBOX: 0x444f5431,
            runner.MAILBOX + 4: 0x10000
        }
        if address in identity:
            changed = (
                self.change_final_identity == address and self.ran
                and self.state == 3
            )
            return identity[address] ^ int(changed)
        if address == 4:
            if self.lose_final_seal and self.ran and self.state == 3:
                return self.state
            return self.state | ((1 << 8) |
                                 (1 << 9) if self.state in (3, 4) else 0)
        if address == 0x104:
            return 1
        if address == 0xc:
            return 2 if self.change_final_epoch and self.ran and self.state == 3 else 1
        if address == 0x60:
            return self.rejected
        if address == 0x40:
            return 0
        if address == runner.MAILBOX + 0xc:
            return 2
        if self.cookie == self.fail_case and address == runner.MAILBOX + 0x14:
            raise RuntimeError(
                'injected transport failure during next completion'
            )
        if self.fail_counter and address == runner.MAILBOX + 0x40:
            raise RuntimeError(
                'injected counter-read failure after completion'
            )
        case = self.cases[self.cookie - 1]
        valid = case['expected_valid']
        if address == runner.MAILBOX + 0x14:
            return 0 if valid else 7
        if address == runner.MAILBOX + 0x30:
            if not valid:
                return 1 if self.bad_invalid_result else 0
            return case['expected_dot_i32'] & 0xffffffff
        if address == runner.MAILBOX + 0x34:
            return 0x3c00 if valid else 0
        if address == runner.MAILBOX + 0x38:
            return self.cookie
        if address == runner.MAILBOX + 0x40:
            return 17
        if address == runner.MAILBOX + 0x50:
            return 2 if self.wrong_read_count else 1
        if address in (runner.MAILBOX + 0x44, runner.MAILBOX + 0x48,
                       runner.MAILBOX + 0x4c, runner.MAILBOX + 0x54):
            return 0
        raise RuntimeError('unexpected mock register ' + hex(address))

    def wait(self, address, predicate, timeout=None):
        value = self.read(address)
        if not predicate(value):
            raise RuntimeError('unexpected mock polling condition')
        return value

    def close(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError('injected cleanup failure')


class RunFailureTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cases, self.image = prepare_fixture.synthetic_cases()
        self.fixture = {
            'schema': prepare_fixture.SCHEMA,
            'status': 'prepared_not_executed',
            'source': {
                'model': prepare_fixture.pack_image.MODEL_ID,
                'revision': prepare_fixture.pack_image.MODEL_REVISION,
                'file': 'Bonsai-1.7B-Q1_0.gguf',
                'bytes': prepare_fixture.pack_image.MODEL_BYTES,
                'sha256': prepare_fixture.pack_image.MODEL_SHA256,
            },
            'canonical_image': {
                'sha256': prepare_fixture.IMAGE_SHA256,
                'bytes': prepare_fixture.pack_image.IMAGE_BYTES
            },
            'numerics': {
                'version': prepare_fixture.NUMERICS
            },
            'synthetic_image': {
                'file': 'synthetic-ternary2.bin',
                'sha256': hashlib.sha256(self.image).hexdigest(),
                'bytes': len(self.image)
            },
            'cases': [],
            'synthetic_cases': deepcopy(self.cases),
        }
        self.args = argparse.Namespace(
            fixture=self.root / 'fixture.json',
            image=self.root / 'image.bin',
            transport=self.root / 'inert-transport',
            out=self.root / 'result',
            suite='synthetic',
            timeout=0.1,
            bulk_timeout=0.1
        )
        self.args.image.write_bytes(self.image)
        self.args.transport.write_text(
            'MOCK UNIT TEST ONLY; not executable hardware'
        )
        self.save_fixture()
        # These tests isolate run-time failure reporting, not build attestation.
        self.build_manifest = {
            'schema': 1,
            'backend': 'aws_f2',
            'agfi': 'agfi-MOCK-UNIT-TEST-ONLY',
            'binary_sha256': runner.sha256(self.args.transport),
            'source_sha256': {}
        }
        manifest_check = patch.object(
            runner,
            'verify_build_manifest',
            create=True,
            return_value=self.build_manifest
        )
        manifest_check.start()
        self.addCleanup(manifest_check.stop)
        # RTL is not executed in these mock transport tests. BuildManifestTests
        # independently exercises real source/binary checks with temporary files.
        source_snapshot = patch.object(
            runner, 'snapshot_sources', return_value={}
        )
        source_snapshot.start()
        self.addCleanup(source_snapshot.stop)

    def save_fixture(self):
        self.args.fixture.write_text(json.dumps(self.fixture))

    def report(self):
        return json.loads((self.args.out / 'report.json').read_text())

    def assert_preflight_failure(self):
        with patch.object(runner, 'Bus') as constructor:
            with self.assertRaises(Exception):
                runner.run(self.args)
            constructor.assert_not_called()
        self.assertEqual(self.report()['status'], 'FAIL')
        self.assertFalse(self.report()['fpga_executed'])

    def test_corrupt_image_fails_before_transport(self):
        corrupted = bytearray(self.image)
        corrupted[5] ^= 1
        self.args.image.write_bytes(corrupted)
        self.assert_preflight_failure()

    def test_wrong_case_count_fails_before_transport(self):
        self.fixture['synthetic_cases'].pop()
        self.save_fixture()
        self.assert_preflight_failure()

    def test_duplicate_case_ids_fail_before_transport(self):
        self.fixture['synthetic_cases'][1]['id'] = self.fixture[
            'synthetic_cases'][0]['id']
        self.save_fixture()
        self.assert_preflight_failure()

    def test_repeated_payload_with_unique_ids_fails_before_transport(self):
        original = deepcopy(self.fixture['synthetic_cases'][0])
        for index, case in enumerate(self.fixture['synthetic_cases']):
            duplicate = deepcopy(original)
            duplicate['id'] = case['id']
            self.fixture['synthetic_cases'][index] = duplicate
        self.save_fixture()
        self.assert_preflight_failure()

    def test_relabelled_valid_case_fails_before_transport(self):
        self.fixture['synthetic_cases'][0]['expected_valid'] = False
        self.save_fixture()
        self.assert_preflight_failure()

    def test_invalid_case_nonnull_oracle_fails_before_transport(self):
        self.fixture['synthetic_cases'][4]['expected_dot_i32'] = 123
        self.save_fixture()
        self.assert_preflight_failure()

    def test_interruption_retains_completed_case_without_pass(self):
        bus = SyntheticBus(self.image, self.cases, fail_case=2)
        with mock_bus(bus):
            with self.assertRaisesRegex(RuntimeError,
                                        'injected transport failure'):
                runner.run(self.args)
        report = self.report()
        self.assertEqual(report['status'], 'FAIL')
        completed = [case for case in report['cases'] if case.get('passed')]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]['id'], self.cases[0]['id'])
        self.assertTrue(report['fpga_executed'])
        self.assertTrue(bus.closed)

    def test_error_completion_with_nonzero_payload_fails(self):
        bus = SyntheticBus(self.image, self.cases, bad_invalid_result=True)
        with mock_bus(bus):
            with self.assertRaisesRegex(RuntimeError, 'fail closed'):
                runner.run(self.args)
        report = self.report()
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(
            len([case for case in report['cases'] if case.get('passed')]), 4
        )
        self.assertEqual(report['cases'][-1]['actual_dot_i32'], 1)
        self.assertFalse(report['cases'][-1].get('passed', False))

    def test_counter_failure_retains_observed_completion(self):
        bus = SyntheticBus(self.image, self.cases, fail_counter=True)
        with mock_bus(bus):
            with self.assertRaisesRegex(RuntimeError, 'counter-read failure'):
                runner.run(self.args)
        report = self.report()
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(report['fpga_executed'])
        self.assertTrue(report['cases'], 'observed completion was discarded')
        self.assertEqual(report['cases'][0]['actual_dot_i32'], 0)
        self.assertFalse(report['cases'][0].get('passed', False))

    def test_cleanup_failure_cannot_erase_failure_report(self):
        bus = SyntheticBus(
            self.image, self.cases, fail_case=2, fail_close=True
        )
        with mock_bus(bus):
            with self.assertRaises(Exception):
                runner.run(self.args)
        report = self.report()
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(
            len([case for case in report['cases'] if case.get('passed')]), 1
        )
        self.assertIn('finished_utc', report)

    def test_cleanup_failure_after_all_cases_cannot_return_pass(self):
        bus = SyntheticBus(self.image, self.cases, fail_close=True)
        with mock_bus(bus):
            with self.assertRaises(RuntimeError):
                runner.run(self.args)
        report = self.report()
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(
            len([case for case in report['cases'] if case.get('passed')]), 6
        )
        self.assertIn('cleanup_errors', report)

    def test_final_image_mutation_prevents_pass(self):
        bus = SyntheticBus(self.image, self.cases, corrupt_final_dump=True)
        with mock_bus(bus):
            with self.assertRaisesRegex(RuntimeError, 'image changed'):
                runner.run(self.args)
        report = self.report()
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(
            len([case for case in report['cases'] if case.get('passed')]), 6
        )

    def test_final_backend_change_prevents_pass(self):
        bus = SyntheticBus(self.image, self.cases, change_final_backend=True)
        with mock_bus(bus):
            with self.assertRaises(RuntimeError):
                runner.run(self.args)
        self.assertEqual(self.report()['status'], 'FAIL')

    def test_legacy_ddr_identity_remains_accepted(self):
        bus = SyntheticBus(self.image, self.cases)
        with mock_bus(bus):
            report = runner.run(self.args)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['memory_backend'], 'ddr')
        self.assertEqual(report['hardware_identity']['memory_backend_id'], 2)
        self.assertEqual(
            report['final_hardware_identity'], report['hardware_identity']
        )

    def test_hbm_requires_explicit_matching_transport_identity(self):
        self.build_manifest['memory_backend'] = 'hbm'
        # A transport without the discriminator is legacy DDR, even if a
        # different register claims HBM. Reject before issuing any writes.
        bus = SyntheticBus(self.image, self.cases, hardware_backend_id=3)
        with mock_bus(bus):
            with self.assertRaisesRegex(ValueError,
                                        'transport memory backend differs'):
                runner.run(self.args)
        self.assertEqual(self.report()['status'], 'FAIL')
        self.assertFalse(bus.registers)

    def test_hbm_transport_cannot_satisfy_ddr_manifest(self):
        bus = SyntheticBus(
            self.image,
            self.cases,
            memory_backend='hbm',
            hardware_backend_id=3
        )
        with mock_bus(bus):
            with self.assertRaisesRegex(ValueError,
                                        'transport memory backend differs'):
                runner.run(self.args)
        self.assertFalse(bus.registers)

    def test_wrong_hardware_memory_backend_fails_before_loading(self):
        for memory_backend, wrong_id in (('ddr', 3), ('hbm', 2), ('hbm', 0)):
            with self.subTest(memory_backend=memory_backend,
                              wrong_id=wrong_id):
                self.args.out = self.root / f'{memory_backend}-{wrong_id}'
                self.build_manifest['memory_backend'] = memory_backend
                bus = SyntheticBus(
                    self.image,
                    self.cases,
                    memory_backend=memory_backend,
                    hardware_backend_id=wrong_id
                )
                with mock_bus(bus):
                    with self.assertRaisesRegex(
                            ValueError, 'hardware memory backend differs'):
                        runner.run(self.args)
                self.assertEqual(self.report()['status'], 'FAIL')
                self.assertFalse(bus.registers)

    def test_matching_hbm_identity_has_hbm_scope(self):
        self.build_manifest['memory_backend'] = 'hbm'
        bus = SyntheticBus(
            self.image,
            self.cases,
            memory_backend='hbm',
            hardware_backend_id=3
        )
        with mock_bus(bus):
            report = runner.run(self.args)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['memory_backend'], 'hbm')
        self.assertEqual(report['hardware_identity']['memory_backend_id'], 3)
        self.assertEqual(
            report['final_hardware_identity'], report['hardware_identity']
        )
        self.assertIn('sealed HBM image', report['scope'])
        self.assertNotIn('DDR', report['scope'])

    def test_final_transport_memory_backend_change_prevents_pass(self):
        self.build_manifest['memory_backend'] = 'hbm'
        bus = SyntheticBus(
            self.image,
            self.cases,
            memory_backend='hbm',
            hardware_backend_id=3,
            change_final_memory_backend=True
        )
        with mock_bus(bus):
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                runner.run(self.args)
        self.assertEqual(self.report()['status'], 'FAIL')
        self.assertEqual(len(self.report()['cases']), 6)

    def test_final_hardware_identity_change_prevents_pass(self):
        for address in (0, 8, runner.MAILBOX, runner.MAILBOX + 4):
            with self.subTest(address=address):
                self.args.out = self.root / f'identity-{address}'
                bus = SyntheticBus(
                    self.image, self.cases, change_final_identity=address
                )
                with mock_bus(bus):
                    with self.assertRaisesRegex(RuntimeError,
                                                'hardware identity changed'):
                        runner.run(self.args)
                self.assertEqual(self.report()['status'], 'FAIL')

    def test_final_epoch_change_prevents_pass(self):
        bus = SyntheticBus(self.image, self.cases, change_final_epoch=True)
        with mock_bus(bus):
            with self.assertRaisesRegex(RuntimeError,
                                        'state or epoch changed'):
                runner.run(self.args)
        self.assertEqual(self.report()['status'], 'FAIL')

    def test_final_seal_loss_prevents_pass(self):
        bus = SyntheticBus(self.image, self.cases, lose_final_seal=True)
        with mock_bus(bus):
            with self.assertRaisesRegex(RuntimeError,
                                        'state or epoch changed'):
                runner.run(self.args)
        self.assertEqual(self.report()['status'], 'FAIL')

    def test_wrong_line_read_count_prevents_pass(self):
        bus = SyntheticBus(self.image, self.cases, wrong_read_count=True)
        with mock_bus(bus):
            with self.assertRaises(RuntimeError):
                runner.run(self.args)
        self.assertEqual(self.report()['status'], 'FAIL')


class BuildManifestTests(unittest.TestCase):

    HBM_SOURCES = (
        'hdl/verilog/first_slice/hbm_line_bridge.sv',
        'hdl/verilog/first_slice/hbm_cdc_mailbox.sv',
        'tests/hbm_rom/hbm_model_server.cpp',
        'tests/hbm_rom/hbm_rom_sim_top.sv'
    )

    HBM_PHYSICAL_SOURCES = (
        'fpga/aws_f2/first_slice/transport.cpp',
        'fpga/aws_f2/hbm_rom/cl_bonsai_hbm_rom.sv',
        'fpga/aws_f2/hbm_rom/hbm_rom_controller.sv',
        'fpga/aws_f2/hbm_rom/hbm_fixed_clock.sv',
        'fpga/aws_f2/hbm_rom/cl_id_defines.vh'
    )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executable = self.root / 'transport'
        self.executable.write_bytes(b'UNIT TEST ONLY')
        self.path = self.root / 'manifest.json'
        required = [
            'hdl/verilog/first_slice/' + name + '.sv' for name in
            ('weight_store', 'first_slice_top', 'dot128', 'f2_memory_bridge')
        ]
        required += [
            'tests/first_slice/model_server.cpp',
            'tests/first_slice/first_slice_sim_top.sv'
        ]
        sources = {}
        for name in required:
            source = self.root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('UNIT TEST ' + name)
            sources[name] = runner.sha256(source)
        self.manifest = {
            'schema': 1,
            'backend': 'verilator',
            'binary_sha256': runner.sha256(self.executable),
            'source_sha256': sources,
            'tool_version': 'UNIT TEST'
        }

    def verify(self):
        self.path.write_text(json.dumps(self.manifest))
        return runner.verify_build_manifest(
            self.executable, self.path, self.root
        )

    def add_sources(self, paths):
        for name in paths:
            source = self.root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('UNIT TEST ' + name)
            self.manifest['source_sha256'][name] = runner.sha256(source)

    def hbm_manifest(self):
        self.manifest['memory_backend'] = 'hbm'
        self.manifest['source_sha256'] = {
            name: digest
            for name, digest in self.manifest['source_sha256'].items()
            if name.startswith('hdl/')
        }
        self.add_sources(self.HBM_SOURCES)

    def assert_required_sources(self, paths):
        for name in paths:
            with self.subTest(name=name):
                digest = self.manifest['source_sha256'].pop(name)
                with self.assertRaisesRegex(ValueError,
                                            'misses compiled inputs'):
                    self.verify()
                self.manifest['source_sha256'][name] = digest

    def test_build_binding_rejects_changed_binary(self):
        self.verify()
        self.executable.write_bytes(b'DIFFERENT BUILD')
        with self.assertRaisesRegex(ValueError, 'binary differs'):
            self.verify()

    def test_build_binding_rejects_changed_rtl(self):
        self.verify()
        (self.root /
         'hdl/verilog/first_slice/dot128.sv').write_text('CHANGED RTL')
        with self.assertRaisesRegex(ValueError, 'source differs'):
            self.verify()

    def test_build_binding_requires_transport_and_wrapper_sources(self):
        self.assert_required_sources((
            'tests/first_slice/model_server.cpp',
            'tests/first_slice/first_slice_sim_top.sv'
        ))

    def test_legacy_and_explicit_ddr_manifests_are_accepted(self):
        self.verify()
        self.manifest['memory_backend'] = 'ddr'
        self.verify()

    def test_hbm_cannot_relabel_ddr_compiled_inputs(self):
        self.manifest['memory_backend'] = 'hbm'
        with self.assertRaisesRegex(ValueError, 'misses compiled inputs'):
            self.verify()

    def test_hbm_requires_bridge_mailbox_and_hbm_simulator_sources(self):
        self.hbm_manifest()
        self.verify()
        self.assert_required_sources(self.HBM_SOURCES)

    def test_hbm_source_changes_invalidate_manifest(self):
        self.hbm_manifest()
        self.verify()
        (self.root / self.HBM_SOURCES[0]).write_text('CHANGED HBM BRIDGE')
        with self.assertRaisesRegex(ValueError, 'source differs'):
            self.verify()

    def test_hbm_physical_manifest_requires_actual_attachment_sources(self):
        self.hbm_manifest()
        self.manifest.update(
            backend='aws_f2',
            agfi='agfi-UNIT-TEST',
            dcp_sha256='UNIT TEST CHECKPOINT'
        )
        self.manifest['source_sha256'] = {
            name: digest
            for name, digest in self.manifest['source_sha256'].items()
            if name.startswith('hdl/')
        }
        self.add_sources(self.HBM_PHYSICAL_SOURCES)
        self.verify()
        self.assert_required_sources(self.HBM_PHYSICAL_SOURCES)

    def test_unknown_memory_backend_is_rejected(self):
        for value in ('native_hbm', '', None, 3):
            with self.subTest(value=value):
                self.manifest['memory_backend'] = value
                with self.assertRaisesRegex(ValueError,
                                            'unsupported memory backend'):
                    self.verify()


class BusResponseTests(unittest.TestCase):

    def test_register_reads_require_explicit_integer_response(self):
        responses = [
            {
                'ok': True,
                'resp': 0,
                'value': True
            },
            {
                'ok': True,
                'value': 0
            },
            {
                'ok': True,
                'resp': False,
                'value': 0
            },
            {
                'ok': True,
                'resp': 2,
                'value': 0
            },
            {
                'ok': True,
                'resp': 0,
                'value': -1
            },
            {
                'ok': True,
                'resp': 0,
                'value': 0x100000000
            },
        ]
        for response in responses:
            with self.subTest(response=response):
                bus = object.__new__(runner.Bus)
                bus.request = lambda command: response
                with self.assertRaises(Exception):
                    bus.read(0)

    def test_register_write_requires_explicit_response(self):
        bus = object.__new__(runner.Bus)
        bus.request = lambda command: {'ok': True}
        with self.assertRaises(Exception):
            bus.write(0x100, 1)

    def test_partial_json_response_honors_request_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = root / 'partial-response'
            transport.write_text(
                '#!/usr/bin/env python3\nimport sys,time\nfrom pathlib import Path\nPath(__file__).with_name("ready").write_text("ready")\nsys.stdin.readline()\nsys.stdout.write(\'{"ok":true\')\nsys.stdout.flush()\ntime.sleep(0.75)\n'
            )
            transport.chmod(0o700)
            with (root / 'stderr.log').open('w') as log:
                bus = runner.Bus(transport, log, 0.05)
                try:
                    deadline = time.monotonic() + 3
                    while not (root / 'ready').exists() and time.monotonic(
                    ) < deadline:
                        time.sleep(0.005)
                    self.assertTrue((root / 'ready').exists(),
                                    'transport failed to start')
                    start = time.monotonic()
                    with self.assertRaises(TimeoutError):
                        bus.request('INFO')
                    self.assertLess(time.monotonic() - start, 0.5)
                finally:
                    bus.process.wait(timeout=2)
                    bus.close()


if __name__ == '__main__':
    unittest.main()
