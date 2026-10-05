"""HBM lifecycle/error checks through the integrated public mailbox and PCIS buses.

The inherited first-slice cases retain stale epochs, unsealed reads, out-of-range
reads, mutable-descriptor denial and mailbox backpressure coverage. Every test
uses fresh RTL and a 64-byte synthetic image; no case claims model-weight proof.
"""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/first_slice'))
import test_mailbox


class HbmTests(test_mailbox.MailboxTests):

    def test_hbm_identity_mapping_and_two_beat_transfer(self):
        self.load_seal()
        self.assertEqual(self.bus.read(8), 3)
        self.assertEqual(self.bus.request('PEEK 0')['value'], 0x55555555)
        for address in (0, 4, 28, 32, 60) * 4:
            self.assertEqual(
                self.bus.request(f'PEEK {address}')['value'], 0x55555555
            )
        info = self.bus.request('INFO')
        self.assertEqual(info['memory_backend'], 'hbm')
        self.assertEqual(info['pseudochannel'], 15)
        self.assertEqual(
            info['clock_periods'], {
                'core':
                2 * int(os.environ.get('HBM_SIM_CORE_HALF_PERIOD', '9')),
                'hbm': 2 * int(os.environ.get('HBM_SIM_HBM_HALF_PERIOD', '5'))
            }
        )
        self.assertEqual(info['hbm_read_beats'], 2 * info['hbm_reads'])
        self.assertEqual(info['hbm_write_beats'], 2 * info['hbm_writes'])
        self.assertGreater(
            info['hbm_write_stalls'] + info['hbm_read_stalls'], 0
        )
        self.assertEqual(info['hbm_fault'], 0)

    def test_sealed_pcis_write_cannot_change_either_half_line(self):
        self.load_seal()
        before = self.bus.request('INFO')['hbm_writes']
        rejected = self.bus.read(0x60)
        for address in (0, 28, 32, 60):
            self.assertEqual(
                self.bus.request(f'POKE {address} 3735928559')['resp'], 2
            )
            self.assertEqual(
                self.bus.request(f'PEEK {address}')['value'], 0x55555555
            )
        self.assertEqual(self.bus.read(0x60), rejected + 4)
        self.assertEqual(self.bus.request('INFO')['hbm_writes'], before)

    def test_reset_requires_reload_verify_and_reseal(self):
        self.load_seal()
        self.command()
        self.completion(0, -16384)
        self.assertTrue(self.bus.request('RESET')['volatile_contents_lost'])
        self.assertEqual(self.bus.read(4) & 0x107, 0)
        self.command()
        self.completion(6)
        self.bus.write(test_mailbox.MB + 0x10, 2)
        (self.root / 'readback.bin').unlink()
        self.load_seal()
        self.command(cookie=104)
        self.completion(0, -16384, cookie=104)

    def test_controller_ready_loss_is_sticky_and_requires_reset(self):
        self.load_seal()
        self.bus.request('READY 0')
        self.assertEqual(self.bus.read(4) & 7, 5)
        self.assertFalse(self.bus.read(4) & 0x100)
        self.bus.request('READY 1')
        self.assertEqual(self.bus.read(4) & 7, 5)
        self.assertEqual(self.bus.request('INFO')['hbm_ready'], 0)
        self.assertEqual(self.bus.request('WRITE 256 1')['resp'], 2)
        self.bus.request('RESET')
        self.assertEqual(self.bus.read(4) & 7, 0)
        self.assertEqual(self.bus.request('INFO')['hbm_ready'], 1)

    def test_controller_loss_with_stopped_hbm_clock_aborts_without_deadlock(
        self
    ):
        self.load_seal()
        self.bus.request('PAUSE 1')
        self.command()
        self.bus.request('READY 0')
        self.completion(4)
        self.assertEqual(self.bus.read(4) & 7, 5)
        self.assertEqual(self.bus.read(test_mailbox.MB + 0x34), 0)
        self.bus.request('PAUSE 0')
        self.bus.request('READY 1')
        self.assertEqual(self.bus.read(4) & 7, 5)
        self.bus.request('RESET')
        self.assertEqual(self.bus.read(4) & 7, 0)

    def test_every_hbm_read_error_fails_closed_with_zero_result(self):
        for fault in ('rresp_first', 'rresp_last', 'rid', 'early_rlast'):
            with self.subTest(fault=fault):
                self.load_seal()
                self.bus.request('FAULT ' + fault)
                self.command()
                self.completion(4)
                self.assertEqual(self.bus.read(test_mailbox.MB + 0x34), 0)
                self.assertEqual(self.bus.read(4) & 7, 5)
                self.assertFalse(self.bus.read(4) & 0x100)
                before = self.bus.request('INFO')['hbm_reads']
                self.bus.write(test_mailbox.MB + 0x10, 2)
                self.assertEqual(
                    self.bus.request(f'WRITE {test_mailbox.MB + 0x10} 1')
                    ['resp'], 2
                )
                self.assertEqual(self.bus.request('INFO')['hbm_reads'], before)
                self.bus.request('RESET')
                (self.root / 'readback.bin').unlink()


if __name__ == '__main__':
    unittest.main()
