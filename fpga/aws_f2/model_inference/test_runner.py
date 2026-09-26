"""Evidence must retain completed hardware calls when a later operation fails."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run


class FailureEvidenceTest(unittest.TestCase):

    def test_failed_generation_retains_completed_calls(self):
        device = SimpleNamespace(
            calls=0,
            invoked_linears=0,
            verified_linears=0,
            uploaded_bytes=4096,
            close=lambda: None
        )
        projection = SimpleNamespace(invoked=False)

        def fail_after_call(*_args):
            device.calls = 1
            device.invoked_linears = 1
            device.verified_linears = 1
            projection.invoked = True
            raise RuntimeError("injected second projection failure")

        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "failed.json"
            argv = [
                "run.py", "--model", "unused.gguf", "--backend", "fpga",
                "--external-qwen35", "unused", "--report",
                str(report_path)
            ]
            with patch("sys.argv", argv), \
                    patch.object(run, "open_backend", return_value=(None, device, {})), \
                    patch.object(run, "load_model", return_value=(None, None, {})), \
                    patch.object(run, "install_linears", return_value={"test_projection": projection}), \
                    patch.object(run, "generate", side_effect=fail_after_call):
                with self.assertRaisesRegex(RuntimeError,
                                            "second projection failure"):
                    run.main()
            report = json.loads(report_path.read_text())
            self.assertEqual(report["status"], "failed")
            self.assertTrue(report["fpga_executed"])
            self.assertEqual(
                report["partial_fpga_state"]["completed_calls"], 1
            )
            self.assertEqual(
                report["partial_fpga_state"]["verified_linears"], 1
            )
            self.assertEqual(
                report["partial_fpga_state"]["invoked_projection_names"],
                ["test_projection"]
            )
            self.assertFalse(report_path.with_suffix(".npz").exists())


if __name__ == "__main__":
    unittest.main()
