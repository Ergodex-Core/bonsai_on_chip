"""Fatal malformed-request regressions against the integrated RTL transport.

Each test uses a fresh simulator process and a small synthetic image. These are
host transport safety checks, not actual-weight or physical FPGA evidence.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest


class TransportProtocolTests(unittest.TestCase):

    def setUp(self):
        executable = os.environ.get('FIRST_SLICE_MODEL_SERVER')
        if not executable:
            raise RuntimeError('FIRST_SLICE_MODEL_SERVER is required')
        self.executable = str(Path(executable).resolve())
        self.temp = tempfile.TemporaryDirectory(prefix='erg103-protocol-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'first image.bin'
        self.image.write_bytes(bytes(range(64)))

    def setup_commands(self):
        digest = hashlib.sha256(self.image.read_bytes()).digest()
        commands = ['INFO', 'WRITE 280 64']
        commands += [
            f'WRITE {0x120 + 4 * index} {word}'
            for index, word in enumerate(struct.unpack('<8I', digest))
        ]
        commands += [
            'WRITE 256 1', 'LOAD ' + json.dumps(str(self.image)), 'WRITE 256 2'
        ]
        return commands

    def require_fatal(self, commands, reason):
        result = subprocess.run([self.executable],
                                input='\n'.join(commands) + '\n',
                                text=True,
                                capture_output=True,
                                timeout=20,
                                check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), len(commands), result.stdout)
        expected = os.environ.get('FIRST_SLICE_EXPECTED_BACKEND', 'verilator')
        self.assertIn(expected, ('verilator', 'arcilator'))
        self.assertEqual(replies[0]['backend'], expected)
        for reply in replies[:-1]:
            self.assertIs(reply.get('ok'), True, reply)
            if 'resp' in reply:
                self.assertEqual(reply['resp'], 0, reply)
        self.assertIs(replies[-1].get('ok'), False)
        self.assertIn(reason, result.stderr)
        self.assertNotIn('TRANSPORT_COMPLETE', result.stderr)

    def test_unaligned_and_out_of_image_peek_fail_before_lane_access(self):
        for address in (63, 64):
            with self.subTest(address=address):
                self.require_fatal(
                    self.setup_commands() + [f'PEEK {address}'],
                    'invalid PEEK dimensions'
                )

    def test_unaligned_and_out_of_image_poke_fail_before_lane_access(self):
        for address in (63, 64):
            with self.subTest(address=address):
                self.require_fatal(
                    self.setup_commands() + [f'POKE {address} 1'],
                    'invalid POKE dimensions'
                )

    def test_second_load_refused_before_memory_resize_or_bus_write(self):
        replacement = self.root / 'replacement.bin'
        replacement.write_bytes(b'\xaa' * 128)
        # Distinct lengths would resize the modeled DDR before an RTL rejection
        # without the one-load guard. Require that earlier guard to be the cause.
        self.require_fatal(
            self.setup_commands() + ['LOAD ' + json.dumps(str(replacement))],
            'only one LOAD is permitted per cold simulation'
        )

    def test_dump_refuses_existing_file_without_modifying_it(self):
        existing = self.root / 'existing readback.bin'
        original = b'existing evidence must survive'
        existing.write_bytes(original)
        self.require_fatal(
            self.setup_commands() +
            ['DUMP ' + json.dumps(str(existing)) + ' 64'],
            'cannot exclusively create readback file'
        )
        self.assertEqual(existing.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
