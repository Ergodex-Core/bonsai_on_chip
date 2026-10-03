# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import fnmatch
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock

from utils import run_uvm_regression


class RunUvmRegressionTest(unittest.TestCase):

    def test_zvfbf_and_first_ml_ops_targets_are_denylisted(self):
        denylist = run_uvm_regression.DENYLIST

        self.assertIn("//tests/cocotb:zvfbf_test", denylist)
        self.assertIn(
            "//tests/cocotb/rvv/ml_ops:rvv_float_matmul_assembly", denylist
        )
        self.assertTrue(
            any(
                fnmatch.fnmatch("//tests/cocotb:zvfbf_test", pattern)
                for pattern in denylist
            )
        )
        self.assertIn("//tests/cocotb/vme_test:vme_test_program", denylist)
        self.assertIn(
            "//tests/cocotb/vme_test:vme_matmul_test_program", denylist
        )
        self.assertTrue(
            any(
                fnmatch.fnmatch(
                    "//tests/cocotb/rvv/ml_ops:rvv_float_matmul_assembly",
                    pattern,
                ) for pattern in denylist
            )
        )
        self.assertTrue(
            any(
                fnmatch.
                fnmatch("//tests/cocotb/vme_test:vme_test_program", pattern)
                for pattern in denylist
            )
        )
        self.assertTrue(
            any(
                fnmatch.fnmatch(
                    "//tests/cocotb/vme_test:vme_matmul_test_program", pattern
                ) for pattern in denylist
            )
        )

    def test_all_bf16_targets_are_denylisted(self):
        denylist = run_uvm_regression.DENYLIST
        sample_bf16_targets = [
            "//tests/cocotb/rvv/ml_ops:rvv_bf16_matmul",
            "@coralnpu_hw//tests/cocotb/rvv/ml_ops:rvv_bf16_matmul",
            "//tests/cocotb/rvv/arithmetics:rvv_bf16_mac_vv_m1",
            "@coralnpu_hw//tests/cocotb/rvv/arithmetics:rvv_bf16_pipeline_mf2",
            "//tests/cocotb:rvv_bf16_ops_cocotb_test",
        ]
        for t in sample_bf16_targets:
            self.assertTrue(
                any(fnmatch.fnmatch(t, pattern) for pattern in denylist),
                f"Expected target '{t}' to be excluded by DENYLIST"
            )

    def test_format_batch_entry(self):
        entry = run_uvm_regression.format_batch_entry(
            elf="/path/to/test.elf",
            tohost=0x80001000,
            entry=0x00000000,
            timeout=100000,
            spike_log="SPIKE",
            target="//examples:hello_world",
        )
        self.assertEqual(
            entry,
            "/path/to/test.elf 80001000 00000000 100000 SPIKE //examples:hello_world\n",
        )

    @mock.patch("subprocess.run")
    def test_build_spike_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["bazel"], returncode=0
        )
        self.assertTrue(run_uvm_regression.build_spike())
        mock_run.assert_called_once_with([
            "bazel", "build", "//sw/coralnpu_sim:spike_cosim_dpi"
        ],
                                         check=True)

    @mock.patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["bazel"])
    )
    def test_build_spike_failure(self, mock_run):
        self.assertFalse(run_uvm_regression.build_spike())

    @mock.patch("utils.run_uvm_regression.build_simulator", return_value=True)
    @mock.patch("utils.run_uvm_regression.run_uvm_batch")
    @mock.patch("os.makedirs")
    @mock.patch("os.chmod")
    @mock.patch("os.path.exists", return_value=True)
    @mock.patch("utils.run_uvm_regression.get_entry_point", return_value=0)
    @mock.patch(
        "utils.run_uvm_regression.get_tohost_addr", return_value=0x80001000
    )
    @mock.patch("shutil.copy2")
    @mock.patch("shutil.make_archive")
    def test_run_full_regression_sets_spike_option(
        self, mock_archive, mock_copy, mock_tohost, mock_entry, mock_exists,
        mock_chmod, mock_makedirs, mock_batch, mock_build
    ):
        mock_batch.return_value = (
            [{
                "Target": "//examples:hello_world",
                "Status": "PASS",
                "Reason": "None",
                "Log Path": "logs/hello_world.log",
            }],
            {"//examples:hello_world"},
        )
        with mock.patch("builtins.open", mock.mock_open()) as mock_file:
            run_uvm_regression.run_full_regression(
                tests_to_run=[
                    ("//examples:hello_world", "/path/to/hello_world.elf")
                ],
                spike_enabled=True,
                mpact_root="/fake/mpact",
                mpact_riscv_root=None,
                temp_elf_dir="/tmp",
                simulator="vcs",
            )
            # Find the write calls to verify the batch list entry wrote SPIKE
            written = "".join(
                call.args[0]
                for call in mock_file().write.call_args_list
                if call.args
            )
            self.assertIn("SPIKE", written)


class VerilatorPathTest(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="uvm paths ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def executable(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
        return path

    def runtime(self, root):
        for relative in ("include/verilated.mk", "include/verilated_config.h",
                         "bin/verilator_includer"):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("runtime fixture\n")
        return root

    def queried_build(self, outputs):
        with mock.patch("utils.run_uvm_regression.subprocess.run") as run:
            run.side_effect = [
                subprocess.CompletedProcess(["bazel"], 0),
                subprocess.CompletedProcess(["bazel"], 0, stdout=outputs)
            ]
            result = run_uvm_regression.build_verilator()
            self.assertEqual(
                run.call_args_list[1].args[0], [
                    "bazel", "cquery", "@verilator//:verilator_bin",
                    "--output=files"
                ]
            )
            return result

    def test_queries_canonical_and_legacy_outputs(self):
        for name in ("+coralnpu_deps_ext+verilator", "verilator~5.052",
                     "verilator"):
            with self.subTest(repository=name):
                path = self.executable(
                    "bazel-out/k8/bin/external/" + name + "/verilator_bin"
                )
                self.assertEqual(
                    self.queried_build(str(path) + "\n"), str(path)
                )

    def test_accepts_relative_paths_and_ignores_non_executable_debug_output(
        self
    ):
        path = self.executable("out/external/+extension+tool/verilator_bin")
        relative = os.path.relpath(path)
        self.assertEqual(
            self.queried_build(relative + "\n" + relative + ".dwp\n"),
            str(path)
        )

    def test_rejects_missing_executable(self):
        path = self.root / "missing/verilator_bin"
        self.assertIsNone(self.queried_build(str(path) + "\n"))

    def test_rejects_non_executable_file(self):
        path = self.executable("nonexec/verilator_bin")
        path.chmod(0o644)
        self.assertIsNone(self.queried_build(str(path) + "\n"))

    def test_rejects_ambiguous_executables(self):
        first = self.executable("one/verilator_bin")
        second = self.executable("two/verilator_bin")
        self.assertIsNone(
            self.queried_build(str(first) + "\n" + str(second) + "\n")
        )

    def test_rejects_empty_or_wrong_target_outputs(self):
        wrong = self.executable("wrong/other_binary")
        for outputs in ("", str(wrong) + "\n"):
            with self.subTest(outputs=outputs):
                self.assertIsNone(self.queried_build(outputs))

    def test_build_failure_does_not_query(self):
        with mock.patch("utils.run_uvm_regression.subprocess.run",
                        side_effect=subprocess.CalledProcessError(
                            1, ["bazel", "build"])) as run:
            self.assertIsNone(run_uvm_regression.build_verilator())
            self.assertEqual(run.call_count, 1)

    def test_query_failure_does_not_guess_old_path(self):
        with mock.patch("utils.run_uvm_regression.subprocess.run") as run:
            run.side_effect = [
                subprocess.CompletedProcess(["bazel"], 0),
                subprocess.CalledProcessError(1, ["bazel", "cquery"])
            ]
            self.assertIsNone(run_uvm_regression.build_verilator())

    def test_canonical_runfiles_contains_complete_runtime(self):
        binary = self.executable("external/+any_extension+tool/verilator_bin")
        runtime = self.runtime(
            Path(str(binary) + ".runfiles") / binary.parent.name
        )
        with mock.patch("utils.run_uvm_regression.subprocess.check_output"
                        ) as query:
            self.assertEqual(
                run_uvm_regression.resolve_verilator_root(str(binary)),
                str(runtime)
            )
            query.assert_not_called()

    def test_legacy_runfiles_alias(self):
        binary = self.executable("external/+extension+tool/verilator_bin")
        runtime = self.runtime(Path(str(binary) + ".runfiles") / "verilator")
        self.assertEqual(
            run_uvm_regression.resolve_verilator_root(str(binary)),
            str(runtime)
        )

    def test_source_fallback_uses_queried_repository(self):
        binary = self.executable("external/+extension+verilator/verilator_bin")
        base = self.root / "output_base"
        runtime = self.runtime(base / "external" / binary.parent.name)
        self.runtime(base / "external" / "verilator_unrelated_version")
        with mock.patch("utils.run_uvm_regression.subprocess.check_output",
                        return_value=str(base)):
            self.assertEqual(
                run_uvm_regression.resolve_verilator_root(str(binary)),
                str(runtime)
            )

    def test_rejects_partial_runtime_and_unrelated_repositories(self):
        binary = self.executable("external/+extension+verilator/verilator_bin")
        partial = Path(str(binary) + ".runfiles") / binary.parent.name
        (partial / "include").mkdir(parents=True)
        (partial / "include/verilated.mk").write_text("incomplete\n")
        base = self.root / "output_base"
        self.runtime(base / "external" / "verilator_unrelated_version")
        with mock.patch("utils.run_uvm_regression.subprocess.check_output",
                        return_value=str(base)):
            self.assertEqual(
                run_uvm_regression.resolve_verilator_root(str(binary)), ""
            )

    def test_runtime_query_failure_returns_empty(self):
        binary = self.executable("external/+extension+tool/verilator_bin")
        with mock.patch("utils.run_uvm_regression.subprocess.check_output",
                        side_effect=subprocess.CalledProcessError(
                            1, ["bazel", "info"])):
            self.assertEqual(
                run_uvm_regression.resolve_verilator_root(str(binary)), ""
            )


if __name__ == "__main__":
    unittest.main()
