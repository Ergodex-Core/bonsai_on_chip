"""Public-bus negative and lifecycle checks against the integrated RTL transport."""
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'utils/first_slice'))
SPEC = importlib.util.spec_from_file_location(
    'first_slice_runtime', ROOT / 'utils/first_slice/run.py'
)
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)
MB = 0x1000


class MailboxTests(unittest.TestCase):

    def setUp(self):
        executable = os.environ.get('FIRST_SLICE_MODEL_SERVER')
        if not executable:
            raise RuntimeError(
                'FIRST_SLICE_MODEL_SERVER is required; do not silently skip RTL tests'
            )
        self.temp = tempfile.TemporaryDirectory(prefix='erg103-mailbox-')
        self.root = Path(self.temp.name)
        self.log = (self.root / 'transport.log').open('w')
        self.bus = RUNTIME.Bus(Path(executable), self.log, 10)
        expected = os.environ.get('FIRST_SLICE_EXPECTED_BACKEND', 'verilator')
        self.assertIn(expected, ('verilator', 'arcilator'))
        self.assertEqual(self.bus.request('INFO')['backend'], expected)

    def tearDown(self):
        self.bus.close()
        self.log.close()
        self.temp.cleanup()

    def command(self, length=128, opcode=2, offset=0, epoch=1, cookie=103):
        for addr, value in ((0x18, cookie), (0x1c, opcode), (0x20, length),
                            (0x24, offset), (0x28, offset >> 32), (0x2c,
                                                                   epoch)):
            self.bus.write(MB + addr, value)
        self.bus.write(MB + 0x10, 1)

    def completion(self, status, value=0, cookie=103):
        self.bus.wait(MB + 0xc, lambda word: word & 2)
        self.assertEqual(self.bus.read(MB + 0x14), status)
        self.assertEqual(self.bus.read(MB + 0x30), value & 0xffffffff)
        self.assertEqual(self.bus.read(MB + 0x38), cookie)

    def load_seal(self):
        # One actual byte-addressed memory image; ternary2 +1 in every lane.
        image = b'\x55' * 64
        path = self.root / 'image.bin'
        path.write_bytes(image)
        digest = hashlib.sha256(image).digest()
        for addr, value in ((0x110, 0), (0x114, 0), (0x118, 64), (0x11c, 0)):
            self.bus.write(addr, value)
        for index, word in enumerate(struct.unpack('<8I', digest)):
            self.bus.write(0x120 + 4 * index, word)
        self.bus.write(0x100, 1)
        self.bus.request('LOAD ' + str(path))
        self.bus.write(0x100, 2)
        self.bus.wait(0x104, lambda value: value & 1)
        readback = self.root / 'readback.bin'
        self.bus.request('DUMP ' + str(readback) + ' 64')
        self.assertEqual(readback.read_bytes(), image)
        for index, word in enumerate(struct.unpack('<8I', digest)):
            self.bus.write(0x140 + 4 * index, word)
        self.bus.write(0x100, 3)
        self.assertEqual(self.bus.read(4) & 0x307, 0x303)
        for index in range(32):
            self.bus.write(MB + 0x80 + index * 4, 0x80808080)

    def test_not_ready_and_invalid_descriptors_complete_without_reads(self):
        self.command()
        self.completion(6)
        self.assertEqual(self.bus.read(MB + 0x50), 0)
        self.bus.write(MB + 0x10, 2)
        for length, opcode, offset in ((127, 2, 0), (128, 3, 0), (128, 2,
                                                                  1 << 48)):
            self.command(length=length, opcode=opcode, offset=offset)
            self.completion(7)
            self.assertEqual(self.bus.read(MB + 0x50), 0)
            self.bus.write(MB + 0x10, 2)

    def test_busy_mutation_and_unacknowledged_resubmit_preserve_job(self):
        self.load_seal()
        self.command()
        for addr, value in ((MB + 0x10, 1), (MB + 0x18, 999), (MB + 0x80, 0),
                            (MB + 0x10, 2)):
            result = self.bus.request(f'WRITE {addr} {value}')
            self.assertEqual(result['resp'], 2)
        self.completion(0, -16384)
        self.assertEqual(self.bus.read(MB + 0x34), 0x3c00)
        for _ in range(3):
            result = self.bus.request(f'WRITE {MB + 0x10} 1')
            self.assertEqual(result['resp'], 2)
            self.completion(0, -16384)
        self.bus.write(MB + 0x10, 2)
        self.assertFalse(self.bus.read(MB + 0xc) & 2)
        self.command(cookie=104)
        self.completion(0, -16384, cookie=104)

    def test_read_errors_and_sealed_descriptor_rejection(self):
        self.load_seal()
        self.command(epoch=2)
        self.completion(3)
        self.bus.write(MB + 0x10, 2)
        self.command(offset=64)
        self.completion(2)
        self.bus.write(MB + 0x10, 2)
        rejected = self.bus.read(0x60)
        for addr, value in ((0x110, 64), (0x118, 128), (0x120, 0), (0x100, 1)):
            self.assertEqual(
                self.bus.request(f'WRITE {addr} {value}')['resp'], 2
            )
        self.assertEqual(self.bus.read(0x60), rejected + 4)
        self.assertEqual(self.bus.read(0x110), 0)
        self.assertEqual(self.bus.read(0x118), 64)
        self.command()
        self.completion(0, -16384)
        self.assertEqual(self.bus.request('READ 8192')['resp'], 3)
        self.assertEqual(self.bus.request('READ 4097')['resp'], 2)


if __name__ == '__main__':
    unittest.main()
