"""Synthetic tests of the checker, not results from a Coral RTL simulation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_store_trace import validate


def trace(rows):
    return dict(
        schema='coralnpu.lsu_store_completion_trace.v1',
        signal='retirement.storeComplete',
        provenance='synthetic',
        cycles=rows
    )


class StoreTraceTest(unittest.TestCase):

    def setUp(self):
        self.good = trace([
            dict(
                cycle=0,
                dispatch=[dict(id=0, pc=0x1000),
                          dict(id=1, pc=0x1002)]
            ),
            dict(cycle=2, eligible=[0]),
            dict(cycle=3, observed=[dict(pc=0x1000)]),
            dict(cycle=5, eligible=[1]),
            dict(cycle=6, observed=[dict(pc=0x1002)]),
        ])

    def test_backpressure_delays_eligibility(self):
        self.assertEqual(validate(self.good)['completions'], 2)

    def test_adjacent_completions(self):
        self.good['cycles'][3]['cycle'] = 3
        self.good['cycles'][4]['cycle'] = 4
        self.good['cycles'][2]['eligible'] = [1]
        del self.good['cycles'][3]
        self.assertEqual(validate(self.good)['completions'], 2)

    def test_same_pc_in_loop_is_allowed(self):
        self.good['cycles'][0]['dispatch'][1]['pc'] = 0x1000
        self.good['cycles'][-1]['observed'][0]['pc'] = 0x1000
        self.assertEqual(validate(self.good)['completions'], 2)

    def test_fault_flush_cancels_old_store(self):
        self.good['cycles'][3] = dict(cycle=5, cancel=[1])
        self.good['cycles'][-1].pop('observed')
        self.assertEqual(validate(self.good)['canceled'], 1)

    def test_new_store_after_flush_keeps_its_pc(self):
        self.good['cycles'][3] = dict(
            cycle=5, cancel=[1], dispatch=[dict(id=2, pc=0x2000)]
        )
        self.good['cycles'][-1] = dict(
            cycle=6, eligible=[2], observed=[dict(pc=0x2000)]
        )
        self.assertEqual(validate(self.good)['completions'], 2)

    def test_wrong_pc_is_rejected(self):
        self.good['cycles'][2]['observed'][0]['pc'] = 0x1002
        with self.assertRaisesRegex(ValueError, 'PC/order'):
            validate(self.good)

    def test_duplicate_is_rejected(self):
        self.good['cycles'].insert(
            3, dict(cycle=4, observed=[dict(pc=0x1000)])
        )
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            validate(self.good)

    def test_missing_completion_is_rejected(self):
        self.good['cycles'][-1].pop('observed')
        with self.assertRaisesRegex(ValueError, 'missing completion'):
            validate(self.good)

    def test_premature_completion_is_rejected(self):
        self.good['cycles'][1] = dict(cycle=2, observed=[dict(pc=0x1000)])
        with self.assertRaisesRegex(ValueError, 'premature'):
            validate(self.good)

    def test_canceled_completion_is_rejected(self):
        self.good['cycles'][3] = dict(cycle=5, cancel=[1])
        with self.assertRaisesRegex(ValueError, 'canceled'):
            validate(self.good)

    def test_late_completion_is_rejected(self):
        self.good['cycles'][2]['cycle'] = 5
        self.good['cycles'][3]['cycle'] = 7
        self.good['cycles'][4]['cycle'] = 8
        with self.assertRaisesRegex(ValueError, 'late completion'):
            validate(self.good)

    def test_stale_slot_pc_after_flush_is_rejected(self):
        self.good['cycles'][3] = dict(
            cycle=5, cancel=[1], dispatch=[dict(id=2, pc=0x2000)]
        )
        self.good['cycles'][-1] = dict(
            cycle=6, eligible=[2], observed=[dict(pc=0x1002)]
        )
        with self.assertRaisesRegex(ValueError, 'PC/order'):
            validate(self.good)

    def test_duplicated_reference_event_is_rejected(self):
        self.good['cycles'][2]['eligible'] = [0]
        with self.assertRaisesRegex(ValueError, 'invalid eligibility'):
            validate(self.good)

    def test_unknown_flush_id_is_rejected(self):
        self.good['cycles'][1]['cancel'] = [99]
        with self.assertRaisesRegex(ValueError, 'unknown'):
            validate(self.good)

    def test_unfinished_backpressured_store_is_rejected(self):
        self.good['cycles'][3].pop('eligible')
        self.good['cycles'][-1].pop('observed')
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            validate(self.good)

    def test_boolean_pc_is_rejected(self):
        self.good['cycles'][0]['dispatch'][0]['pc'] = False
        with self.assertRaisesRegex(ValueError, 'integer'):
            validate(self.good)

    def test_no_stores_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'no store transactions'):
            validate(trace([dict(cycle=0)]))

    def test_all_canceled_is_not_success_coverage(self):
        with self.assertRaisesRegex(ValueError, 'no successful'):
            validate(
                trace([
                    dict(
                        cycle=0, dispatch=[dict(id=0, pc=0x1000)], cancel=[0]
                    )
                ])
            )

    def test_checker_pass_never_claims_execution_or_timing(self):
        result = validate(self.good)
        for field in ('physical_fpga', 'rtl_execution_verified',
                      'routed_timing_accepted'):
            self.assertIs(result[field], False)

    def run_cli(self, expected_sha):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.json'
            raw = json.dumps(self.good).encode()
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest(
            ) if expected_sha is None else expected_sha
            return subprocess.run([
                sys.executable,
                str(Path(__file__).with_name('check_store_trace.py')),
                '--trace',
                str(path), '--trace-sha256', digest
            ],
                                  capture_output=True,
                                  text=True,
                                  timeout=5)

    def test_cli_binds_exact_trace_hash(self):
        result = self.run_cli(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)['status'], 'PASS trace contract'
        )

    def test_cli_rejects_changed_trace(self):
        result = self.run_cli('0' * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('trace identity changed', result.stderr)


if __name__ == '__main__':
    unittest.main()
