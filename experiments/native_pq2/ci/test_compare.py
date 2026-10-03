#!/usr/bin/env python3
"""Comparator regression tests using synthetic evidence, never a simulator."""

import copy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "native_pq2_ci_compare",
    Path(__file__).with_name("compare.py")
)
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)
HEAD = "1234567890abcdef1234567890abcdef12345678"


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False) + "\n")


class CompareTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="pq2-compare-tests-"
        )
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        # Snapshot harness bytes so concurrently developed runner files cannot
        # invalidate evidence halfway through this suite. All manifest hashes
        # are still checked against real files, including missing-file tests.
        harness_paths = {}
        for name, source in compare.HARNESS_PATHS.items():
            destination = cls.root / "harness" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_bytes() if source.is_file(
            ) else ("test harness " + name).encode()
            destination.write_bytes(content)
            harness_paths[name] = destination
        patch = mock.patch.object(compare, "HARNESS_PATHS", harness_paths)
        patch.start()
        cls.addClassCleanup(patch.stop)
        cls.cases = compare.expected_cases()
        cls.fixture = {
            "schema": 2,
            "seed": 7193,
            "cases": 192,
            "rom_bytes": 1048576,
            "fixture_sha256": compare.FIXTURE_SHA256,
            "model": None,
            "code_counts_minus1_zero_plus1_plus2":
            [109860, 107899, 114456, 121289],
            "unit_counts": [1, 7, 21, 31, 32],
            "alignments": list(range(16)),
            "strides": [34, 544, 1088, 1632],
            "oracle": "synthetic comparator test evidence",
            "scope":
            "synthetic comparator test evidence; no simulation performed",
        }
        source_manifest = {
            "schema": 1,
            "harness": {
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in harness_paths.items()
            },
            "variants": {},
        }
        for name, parameters in compare.VARIANTS.items():
            family = "baseline" if name == "E1" else "fetch" if name.startswith(
                "E1-fetch"
            ) else "spatial"
            source_manifest["variants"][name] = {
                "rtl_sha256": compare.RTL_HASHES[family],
                "parameters": parameters,
            }
        benchmark = {
            "schema":
            1,
            "status":
            "PASS",
            "ci_head":
            HEAD,
            "source_revisions":
            compare.SOURCE_REVISIONS,
            "repeats":
            compare.REPEATS,
            "profiles":
            compare.PROFILES,
            "timing":
            compare.TIMING,
            "cpu_overlap_variants":
            [name for name in compare.VARIANTS if name != "E1"],
            "fixture":
            cls.fixture,
            "source_manifest":
            source_manifest,
            "runs": [],
        }
        cls.baseline = cls.root / "verilator"
        cls.candidate = cls.root / "arcilator"
        cls.baseline.mkdir()
        with (cls.baseline / "cases.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=sorted(compare.CSV_FIELDS)
            )
            writer.writeheader()
            for variant_index, variant in enumerate(compare.VARIANTS):
                for profile in compare.PROFILES:
                    rows = []
                    for name, case in cls.cases.items():
                        units = case["units"]
                        busy = 50 + units * (variant_index +
                                             1) + profile["latency"]
                        configuration, dispatch, readback = 40 + units, busy + 5, units * 5 + 2
                        rows.append({
                            "variant":
                            variant,
                            "profile":
                            profile["name"],
                            "kind":
                            "case",
                            "name":
                            name,
                            "status":
                            "PASS",
                            **case,
                            **compare.TIMING,
                            **{
                                key: value
                                for key, value in profile.items() if key != "name"
                            },
                            "busy_cycles":
                            busy,
                            "axi_testbench_command_cycles":
                            configuration + dispatch + readback,
                            "configuration_cycles":
                            configuration,
                            "dispatch_to_observed_done_cycles":
                            dispatch,
                            "readback_cycles":
                            readback,
                            "mmio_reads":
                            units * 5 + 2,
                            "mmio_writes":
                            units + 35,
                            "storage_requests":
                            units * 3,
                            "storage_bytes":
                            units * 48,
                            "useful_native_bytes":
                            units * 34,
                            "native_products":
                            units * 128,
                            "peak_outstanding":
                            1,
                            "request_stall_cycles":
                            0,
                            "response_stall_cycles":
                            0,
                        })
                    totals = {
                        field: sum(row[field]
                                   for row in rows)
                        for field in compare.TOTAL_FIELDS
                    }
                    for trial in range(compare.REPEATS):
                        writer.writerows({
                            **row, "trial": trial
                        } for row in rows)
                        seconds = 1.0 + trial * 0.125
                        clock_cycles = totals["axi_testbench_command_cycles"
                                              ] + 1000
                        benchmark["runs"].append({
                            "variant":
                            variant,
                            "profile":
                            profile["name"],
                            "trial":
                            trial,
                            "build_wall_seconds":
                            2.0 + trial * 0.25,
                            "totals":
                            totals,
                            "median_busy_cycles":
                            statistics.median(
                                row["busy_cycles"] for row in rows
                            ),
                            "peak_outstanding":
                            1,
                            "summary": {
                                "kind":
                                "summary",
                                "status":
                                "PASS",
                                "cases":
                                192,
                                "integer_subgroup_checks":
                                14172,
                                "clock_cycles":
                                clock_cycles,
                                "simulation_wall_seconds":
                                seconds,
                                "simulated_cycles_per_second":
                                clock_cycles / seconds,
                                "protocol_tests":
                                "PASS",
                                "storage_requests_including_protocol":
                                totals["storage_requests"] + 20,
                                "held_request_error_seen":
                                variant in ("E1-fetch-D4", "E1-fetch-D8"),
                                "cpu_overlap":
                                compare.OVERLAP_SKIP
                                if variant == "E1" else "PASS",
                                "scope":
                                "synthetic comparator test evidence",
                            },
                        })
        cls.candidate.mkdir()
        shutil.copyfile(
            cls.baseline / "cases.csv", cls.candidate / "cases.csv"
        )
        for backend, directory in (("verilator", cls.baseline),
                                   ("arcilator", cls.candidate)):
            document = copy.deepcopy(benchmark)
            document["backend"] = backend
            if backend == "arcilator":
                for run in document["runs"]:
                    run["build_wall_seconds"] *= 1.5
                    run["summary"]["simulation_wall_seconds"] *= 0.5
                    run["summary"]["simulated_cycles_per_second"] *= 2
            write_json(directory / "benchmark.json", document)
            write_json(directory / "fixture-manifest.json", cls.fixture)
            write_json(directory / "source-manifest.json", source_manifest)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="mutation-", dir=self.root
        )
        self.addCleanup(temporary.cleanup)
        self.mutation_root = Path(temporary.name)

    def clone(self, backend="verilator"):
        destination = self.mutation_root / backend
        return Path(shutil.copytree(self.root / backend, destination))

    def mutate_benchmark(self, directory, change, sync_manifest=None):
        path = directory / "benchmark.json"
        data = json.loads(path.read_text())
        change(data)
        write_json(path, data)
        if sync_manifest:
            key = "fixture" if sync_manifest == "fixture-manifest.json" else "source_manifest"
            write_json(directory / sync_manifest, data[key])

    def rewrite_csv(self, directory, transform):
        path = directory / "cases.csv"
        temporary = directory / "cases-new.csv"
        with path.open(newline=""
                       ) as source, temporary.open("w",
                                                   newline="") as destination:
            reader = csv.DictReader(source)
            writer = csv.DictWriter(destination, fieldnames=reader.fieldnames)
            writer.writeheader()
            for index, row in enumerate(reader):
                writer.writerows(transform(index, row))
        temporary.replace(path)

    def assert_rejected(self, directory, message, candidate=None):
        with self.assertRaisesRegex(compare.ComparisonError, message):
            compare.compare(directory, candidate or self.candidate, HEAD)

    def test_complete_matrix_accepts_wall_time_variance(self):
        report = compare.compare(self.baseline, self.candidate, HEAD)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["coverage_per_backend"]["runs"], 250)
        self.assertEqual(report["coverage_per_backend"]["case_rows"], 48000)
        self.assertEqual(
            report["coverage_per_backend"]["integer_subgroup_checks"], 3543000
        )
        self.assertTrue(report["trial_counters_deterministic"])
        timing = report["simulation_wall_timings"][0]
        self.assertEqual(timing["median_ratio_verilator_over_arcilator"], 2.0)
        self.assertEqual(timing["seconds"]["verilator"]["count"], 5)
        self.assertGreater(timing["seconds"]["verilator"]["variance"], 0)

    def test_missing_expected_head(self):
        with self.assertRaisesRegex(compare.ComparisonError,
                                    "Expected CI head is required"):
            compare.compare(self.baseline, self.candidate, None)

    def test_stale_head(self):
        directory = self.clone()
        self.mutate_benchmark(
            directory, lambda data: data.update(ci_head="f" * 40)
        )
        self.assert_rejected(directory, "CI head mismatch")

    def test_missing_run(self):
        directory = self.clone()
        self.mutate_benchmark(directory, lambda data: data["runs"].pop())
        self.assert_rejected(directory, "incomplete run coverage")

    def test_missing_trial(self):
        directory = self.clone()
        self.mutate_benchmark(
            directory, lambda data: data.
            update(runs=[run for run in data["runs"] if run["trial"] != 4])
        )
        self.assert_rejected(directory, "incomplete run coverage")

    def test_missing_case(self):
        directory = self.clone()
        self.rewrite_csv(
            directory, lambda index, row: [] if index == 0 else [row]
        )
        self.assert_rejected(directory, "Incomplete case coverage")

    def test_duplicate_case(self):
        directory = self.clone()
        self.rewrite_csv(
            directory, lambda index, row: [row, row] if index == 0 else [row]
        )
        self.assert_rejected(directory, "Duplicate case row")

    def test_missing_manifests(self):
        for filename in ("benchmark.json", "fixture-manifest.json",
                         "source-manifest.json"):
            with self.subTest(filename=filename):
                directory = self.mutation_root / filename
                shutil.copytree(self.baseline, directory)
                (directory / filename).unlink()
                self.assert_rejected(directory, "Cannot read")

    def test_changed_fixture_even_when_manifests_agree(self):
        directory = self.clone()
        self.mutate_benchmark(
            directory,
            lambda data: data["fixture"].update(fixture_sha256="0" * 64),
            sync_manifest="fixture-manifest.json"
        )
        self.assert_rejected(directory, "fixture fixture_sha256 changed")

    def test_fixture_manifest_binding_mismatch(self):
        directory = self.clone()
        fixture = dict(self.fixture, fixture_sha256="0" * 64)
        write_json(directory / "fixture-manifest.json", fixture)
        self.assert_rejected(
            directory, "fixture-manifest.json binding mismatch"
        )

    def test_source_hash_is_checked_against_current_files(self):
        directory = self.clone()
        self.mutate_benchmark(
            directory,
            lambda data: data["source_manifest"]["harness"].
            update({"test_engine.cpp": "0" * 64}),
            sync_manifest="source-manifest.json"
        )
        self.assert_rejected(directory, "current harness hash mismatch")

    def test_changed_counter_between_backends(self):
        directory = self.clone()

        def change_first(index, row):
            if index == 0:
                row["request_stall_cycles"] = "1"
            return [row]

        self.rewrite_csv(directory, change_first)
        self.assert_rejected(
            directory, "Backend CSV counter/correctness mismatch"
        )

    def test_same_nondeterministic_counter_in_both_backends(self):
        baseline, candidate = self.clone(), self.clone("arcilator")

        def change_second_trial(index, row):
            if index == compare.CASE_COUNT:
                self.assertEqual(row["trial"], "1")
                row["request_stall_cycles"] = "1"
            return [row]

        self.rewrite_csv(baseline, change_second_trial)
        self.rewrite_csv(candidate, change_second_trial)
        self.assert_rejected(
            baseline, "non-deterministic case counters", candidate
        )

    def test_zero_or_reduced_subgroup_checks(self):
        for checks in (0, 14168):
            with self.subTest(checks=checks):
                directory = self.mutation_root / str(checks)
                shutil.copytree(self.baseline, directory)
                self.mutate_benchmark(
                    directory, lambda data: data["runs"][0]["summary"].
                    update(integer_subgroup_checks=checks)
                )
                self.assert_rejected(
                    directory, "invalid integer_subgroup_checks"
                )

    def test_partial_cpu_overlap_manifest(self):
        directory = self.clone()
        self.mutate_benchmark(
            directory, lambda data: data["cpu_overlap_variants"].pop()
        )
        self.assert_rejected(
            directory, "CPU overlap coverage changed or missing"
        )

    def test_missing_cpu_overlap_execution(self):
        directory = self.clone()

        def skip_one_overlap(data):
            run = next(run for run in data["runs"] if run["variant"] != "E1")
            run["summary"]["cpu_overlap"] = compare.OVERLAP_SKIP

        self.mutate_benchmark(directory, skip_one_overlap)
        self.assert_rejected(directory, "invalid cpu_overlap")

    def test_failed_protocol_check(self):
        directory = self.clone()
        self.mutate_benchmark(
            directory, lambda data: data["runs"][0]["summary"].
            update(protocol_tests="FAIL")
        )
        self.assert_rejected(directory, "invalid protocol_tests")

    def test_cli_emits_failure_report_and_nonzero_status(self):
        output = self.mutation_root / "comparison.json"
        for missing_head in (True, False):
            with self.subTest(missing_head=missing_head):
                write_json(output, {"status": "PASS", "stale": True})
                arguments = [
                    "--verilator",
                    str(
                        self.baseline if missing_head else self.mutation_root /
                        "missing"
                    ),
                    "--arcilator",
                    str(self.candidate),
                    "--output",
                    str(output),
                ]
                if not missing_head:
                    arguments.extend(("--expected-head", HEAD))
                with mock.patch("builtins.print"), mock.patch.dict(
                        compare.os.environ, {}, clear=True):
                    status = compare.main(arguments)
                self.assertEqual(status, 1)
                report = json.loads(output.read_text())
                self.assertEqual(report["status"], "FAIL")
                self.assertNotIn("stale", report)
                self.assertNotIn(str(self.root), report["failure"])


class RunnerTests(unittest.TestCase):

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "native_pq2_ci_run",
            Path(__file__).with_name("run.py")
        )
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)
        temporary = tempfile.TemporaryDirectory(prefix="pq2-runner-tests-")
        self.addCleanup(temporary.cleanup)
        self.build = Path(temporary.name)

    def test_llvm_export_uses_translator_not_arcilator_early_stop(self):
        binpath = self.build / "tools"
        with mock.patch.object(self.runner, "run") as run:
            self.runner.export_llvm(binpath, self.build)
        run.assert_called_once_with([
            binpath / "mlir-translate", "--mlir-to-llvmir",
            self.build / "llvm.mlir", "-o", self.build / "model.ll"
        ], self.build / "translate.log")
        self.assertNotIn("--until-before=preproc", str(run.call_args))

    def test_failed_commands_surface_compiler_diagnostics(self):
        for error_type in (self.runner.subprocess.CalledProcessError,
                           self.runner.subprocess.TimeoutExpired):
            with self.subTest(error_type=error_type):
                if error_type is self.runner.subprocess.CalledProcessError:
                    error = error_type(1, ["compiler"])
                else:
                    error = error_type(["compiler"], 1)
                log = self.build / "compile.log"

                def fail(command, **kwargs):
                    kwargs["stdout"].write("compiler diagnostic\n")
                    raise error

                with mock.patch.object(self.runner.subprocess, "run",
                                       side_effect=fail), mock.patch(
                                           "builtins.print") as output:
                    with self.assertRaises(error_type):
                        self.runner.run(["compiler"], log)
                output.assert_called_once_with(
                    "compiler diagnostic\n",
                    file=self.runner.sys.stderr,
                    flush=True
                )
                self.assertEqual(log.read_text(), "compiler diagnostic\n")


if __name__ == "__main__":
    unittest.main()
