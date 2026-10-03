#!/usr/bin/env python3
"""Fail-closed parity gate for the repeated public native-PQ2 CI matrix."""

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from native_reference import make_cases  # noqa: E402

REPEATS = 5
CASE_COUNT = 192
SUBGROUP_CHECKS = 14172
FIXTURE_SHA256 = "4ba3b0a78e077d305641a1ba98e05db24d063c009bc3bcf0fa484ead7a03541f"
SOURCE_REVISIONS = {
    "baseline": "816432917d4ac20bdec0f5725dfacd4b3ec54e4f",
    "fetch": "3d5f4cd75d06f9175720e81e53484428e09a8a09",
    "spatial": "7784c5de174b8ee18d32e20b41d2fdad254d1068",
}
VARIANTS = {
    "E1": {},
    **{
        f"E1-fetch-D{depth}": {
            "FETCH_DEPTH": depth
        }
        for depth in (1, 4, 8)
    },
    **{
        f"spatial-L{lanes}": {
            "DOT_LANES": lanes
        }
        for lanes in (1, 2, 4, 8, 16, 32)
    },
}
RTL_HASHES = {
    "baseline":
    "51d7d9001bc6a222f24b45d8118af1a4df201a84fcf1f7352cb713085f2746ca",
    "fetch":
    "da807e820e65b156ee78db49664638cd55130c0af10a66383f53ce8bc6f27de9",
    "spatial":
    "a4ce49ef900ce3c0f393b27bbd76ae1fd27fa23243ec8230b0e748694b68ffbf",
}
PROFILES = [
    dict(
        zip(("name", "latency", "beat_ii", "capacity", "stall_percent"), row)
    ) for row in (
        ("ideal", 1, 1, 8, 0),
        ("lat20", 20, 1, 8, 0),
        ("lat80", 80, 4, 8, 0),
        ("limited", 80, 8, 2, 0),
        ("stress", 20, 4, 2, 25),
    )
]
TIMING = {"latency_jitter": 0, "mmio_stall_max": 3}
OVERLAP_SKIP = "SKIPPED_known_baseline_limitation_or_not_requested"
COUNTERS = (
    "busy_cycles",
    "axi_testbench_command_cycles",
    "configuration_cycles",
    "dispatch_to_observed_done_cycles",
    "readback_cycles",
    "mmio_reads",
    "mmio_writes",
    "storage_requests",
    "storage_bytes",
    "useful_native_bytes",
    "native_products",
    "peak_outstanding",
    "request_stall_cycles",
    "response_stall_cycles",
)
TOTAL_FIELDS = (
    "busy_cycles",
    "axi_testbench_command_cycles",
    "storage_requests",
    "storage_bytes",
    "native_products",
)
CSV_STRINGS = {"variant", "profile", "kind", "name", "status"}
CSV_FIELDS = CSV_STRINGS | set(COUNTERS) | {
    "trial",
    "latency",
    "beat_ii",
    "capacity",
    "stall_percent",
    "latency_jitter",
    "mmio_stall_max",
    "units",
    "alignment",
    "stride",
}
SUMMARY_FIELDS = {
    "kind",
    "status",
    "cases",
    "integer_subgroup_checks",
    "clock_cycles",
    "simulation_wall_seconds",
    "simulated_cycles_per_second",
    "protocol_tests",
    "storage_requests_including_protocol",
    "held_request_error_seen",
    "cpu_overlap",
    "scope",
}
WALL_FIELDS = {"simulation_wall_seconds", "simulated_cycles_per_second"}
HARNESS_PATHS = {
    "test_engine.cpp": HERE.parent / "tests" / "test_engine.cpp",
    "native_reference.py": HERE.parent / "native_reference.py",
    "ci/run.py": HERE / "run.py",
    "ci/generate_adapter.py": HERE / "generate_adapter.py",
    "ci/unroll_processes.cpp": HERE / "unroll_processes.cpp",
    "ci/install_tools.sh": HERE / "install_tools.sh",
}


class ComparisonError(ValueError):
    """Incomplete, stale, malformed, or unequal benchmark evidence."""


def require(condition, message):
    if not condition:
        raise ComparisonError(message)


def exact(actual, expected):
    """Compare fixed configuration without Python's bool/int coercion."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            exact(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            exact(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected


def load_json(path):

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(
                key not in result, f"{path.name}: duplicate JSON key {key}"
            )
            result[key] = value
        return result

    def invalid_constant(value):
        raise ComparisonError(f"{path.name}: nonfinite JSON number {value}")

    try:
        with path.open() as stream:
            return json.load(
                stream,
                object_pairs_hook=unique_object,
                parse_constant=invalid_constant
            )
    except OSError as error:
        raise ComparisonError(
            f"Cannot read {path.name}: {type(error).__name__}"
        ) from error
    except json.JSONDecodeError as error:
        raise ComparisonError(f"Cannot parse {path.name}: {error}") from error


def integer(value, label, minimum=0):
    require(
        type(value) is int and value >= minimum, f"{label}: invalid integer"
    )
    return value


def positive_number(value, label):
    require(
        type(value) in (int, float) and math.isfinite(value) and value > 0,
        f"{label}: expected finite positive measurement",
    )
    return value


def expected_cases():
    cases, _ = make_cases(seed=7193, random_cases=32)
    result = {
        case["name"]: {
            key: case[key]
            for key in ("units", "alignment", "stride")
        }
        for case in cases
    }
    require(
        len(result) == CASE_COUNT,
        "Current fixture generator changed case coverage"
    )
    require(
        sum(case["units"] * 4 for case in result.values()) == SUBGROUP_CHECKS,
        "Current fixture generator changed subgroup coverage",
    )
    return result


def validate_manifest(benchmark, expected_head, backend):
    require(
        exact(benchmark.get("schema"), 1),
        f"{backend}: unsupported benchmark schema"
    )
    require(
        benchmark.get("status") == "PASS", f"{backend}: benchmark did not PASS"
    )
    require(
        benchmark.get("backend") == backend,
        f"{backend}: backend binding mismatch"
    )
    require(
        benchmark.get("ci_head") == expected_head,
        f"{backend}: CI head mismatch"
    )
    require(
        exact(benchmark.get("source_revisions"), SOURCE_REVISIONS),
        f"{backend}: pinned source revisions changed or missing",
    )
    require(
        exact(benchmark.get("repeats"), REPEATS),
        f"{backend}: requires five trials"
    )
    require(
        exact(benchmark.get("profiles"), PROFILES),
        f"{backend}: profiles changed or missing"
    )
    require(
        exact(benchmark.get("timing"), TIMING),
        f"{backend}: timing configuration changed"
    )
    overlap = benchmark.get("cpu_overlap_variants")
    require(
        isinstance(overlap, list) and len(overlap) == len(VARIANTS) - 1
        and set(overlap) == set(VARIANTS) - {"E1"},
        f"{backend}: CPU overlap coverage changed or missing",
    )
    fixture = benchmark.get("fixture", {})
    require(isinstance(fixture, dict), f"{backend}: fixture manifest missing")
    for key, value in {
            "schema": 2,
            "seed": 7193,
            "cases": CASE_COUNT,
            "rom_bytes": 1048576,
            "fixture_sha256": FIXTURE_SHA256,
            "model": None,
            "code_counts_minus1_zero_plus1_plus2": [109860, 107899, 114456,
                                                    121289],
            "unit_counts": [1, 7, 21, 31, 32],
            "alignments": list(range(16)),
            "strides": [34, 544, 1088, 1632],
    }.items():
        require(
            key in fixture and exact(fixture[key], value),
            f"{backend}: fixture {key} changed or missing"
        )
    source = benchmark.get("source_manifest", {})
    require(isinstance(source, dict), f"{backend}: source manifest missing")
    harness = source.get("harness", {})
    require(isinstance(harness, dict), f"{backend}: harness manifest missing")
    require(
        set(harness) == set(HARNESS_PATHS),
        f"{backend}: harness coverage changed or missing"
    )
    for name, path in HARNESS_PATHS.items():
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise ComparisonError(
                f"Current harness source missing: {name}"
            ) from error
        require(
            harness[name] == actual,
            f"{backend}: current harness hash mismatch for {name}"
        )
    variants = source.get("variants", {})
    require(isinstance(variants, dict), f"{backend}: variant manifest missing")
    require(
        set(variants) == set(VARIANTS),
        f"{backend}: variant coverage changed or missing"
    )
    for name, parameters in VARIANTS.items():
        family = "baseline" if name == "E1" else "fetch" if name.startswith(
            "E1-fetch"
        ) else "spatial"
        require(
            exact(
                variants[name], {
                    "rtl_sha256": RTL_HASHES[family],
                    "parameters": parameters
                }
            ),
            f"{backend}: source or parameters changed for {name}",
        )


def load_csv(path, cases):
    rows = {}
    try:
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            require(
                len(fields) == len(set(fields)) and set(fields) == CSV_FIELDS,
                f"{path.name}: case columns changed or missing"
            )
            for line_number, raw in enumerate(reader, start=2):
                require(
                    None not in raw and None not in raw.values(),
                    f"CSV line {line_number}: malformed row"
                )
                row = {}
                for field, value in raw.items():
                    if field in CSV_STRINGS:
                        row[field] = value
                    else:
                        require(
                            re.fullmatch(r"[0-9]+", value) is not None,
                            f"CSV line {line_number}: invalid {field}"
                        )
                        row[field] = int(value)
                variant, profile, trial, name = (
                    row[field]
                    for field in ("variant", "profile", "trial", "name")
                )
                require(
                    variant in VARIANTS
                    and profile in {p["name"]
                                    for p in PROFILES} and 0 <= trial < REPEATS
                    and name in cases,
                    f"CSV line {line_number}: unexpected case identity"
                )
                key = (variant, profile, trial, name)
                require(key not in rows, f"Duplicate case row: {key}")
                require(
                    row["kind"] == "case" and row["status"] == "PASS",
                    f"Case did not PASS: {key}"
                )
                expected_profile = next(
                    p for p in PROFILES if p["name"] == profile
                )
                for field, value in {
                        **cases[name], **TIMING, **{
                        k: v for k, v in expected_profile.items() if k != "name"
                    }
                }.items():
                    require(
                        row[field] == value,
                        f"Case configuration changed: {key}/{field}"
                    )
                for field, value in {
                        "native_products": row["units"] * 128,
                        "useful_native_bytes": row["units"] * 34,
                        "storage_bytes": row["storage_requests"] * 16,
                        "axi_testbench_command_cycles":
                        sum(row[k]
                            for k in ("configuration_cycles",
                                      "dispatch_to_observed_done_cycles",
                                      "readback_cycles")),
                }.items():
                    require(
                        row[field] == value,
                        f"Inconsistent case counter: {key}/{field}"
                    )
                for field in ("busy_cycles", "axi_testbench_command_cycles",
                              "storage_requests", "peak_outstanding"):
                    require(
                        row[field] > 0,
                        f"Missing positive case counter: {key}/{field}"
                    )
                require(
                    row["peak_outstanding"] <= row["capacity"],
                    f"Case capacity exceeded: {key}"
                )
                rows[key] = row
    except OSError as error:
        raise ComparisonError(
            f"Cannot read {path.name}: {type(error).__name__}"
        ) from error
    expected_count = len(VARIANTS) * len(PROFILES) * REPEATS * CASE_COUNT
    require(
        len(rows) == expected_count,
        f"Incomplete case coverage: {len(rows)} != {expected_count}"
    )
    return rows


def load_backend(directory, backend, expected_head, cases):
    benchmark = load_json(directory / "benchmark.json")
    require(
        isinstance(benchmark, dict), f"{backend}: invalid benchmark object"
    )
    validate_manifest(benchmark, expected_head, backend)
    for filename, key in (("fixture-manifest.json", "fixture"),
                          ("source-manifest.json", "source_manifest")):
        require(
            load_json(directory / filename) == benchmark[key],
            f"{backend}: {filename} binding mismatch"
        )
    rows = load_csv(directory / "cases.csv", cases)
    runs = {}
    require(
        isinstance(benchmark.get("runs"), list), f"{backend}: runs missing"
    )
    for run in benchmark["runs"]:
        require(isinstance(run, dict), f"{backend}: invalid run")
        key = (run.get("variant"), run.get("profile"), run.get("trial"))
        require(
            key[0] in VARIANTS and key[1] in {p["name"]
                                              for p in PROFILES}
            and type(key[2]) is int and 0 <= key[2] < REPEATS,
            f"{backend}: unexpected run {key}"
        )
        require(key not in runs, f"{backend}: duplicate run {key}")
        summary = run.get("summary", {})
        require(
            isinstance(summary, dict) and set(summary) == SUMMARY_FIELDS,
            f"{backend}/{key}: summary columns changed or missing"
        )
        for field, expected in {
                "kind": "summary",
                "status": "PASS",
                "cases": CASE_COUNT,
                "integer_subgroup_checks": SUBGROUP_CHECKS,
                "protocol_tests": "PASS",
                "cpu_overlap": OVERLAP_SKIP if key[0] == "E1" else "PASS",
        }.items():
            require(
                exact(summary[field], expected),
                f"{backend}/{key}: invalid {field}"
            )
        require(
            type(summary["held_request_error_seen"]) is bool,
            f"{backend}/{key}: held-request coverage missing"
        )
        integer(summary["clock_cycles"], "clock_cycles", 1)
        integer(
            summary["storage_requests_including_protocol"],
            "storage_requests_including_protocol", 1
        )
        positive_number(run.get("build_wall_seconds"), "build_wall_seconds")
        for field in WALL_FIELDS:
            positive_number(summary[field], field)
        selected = [rows[(*key, name)] for name in cases]
        expected_totals = {
            field: sum(row[field]
                       for row in selected)
            for field in TOTAL_FIELDS
        }
        require(
            exact(run.get("totals"), expected_totals),
            f"{backend}/{key}: totals do not match CSV"
        )
        require(
            summary["storage_requests_including_protocol"]
            > expected_totals["storage_requests"],
            f"{backend}/{key}: protocol requests missing"
        )
        require(
            summary["clock_cycles"]
            >= expected_totals["axi_testbench_command_cycles"],
            f"{backend}/{key}: summary clock cycles below command cycles"
        )
        if "median_busy_cycles" in run:
            require(
                run["median_busy_cycles"] == statistics.median(
                    row["busy_cycles"] for row in selected
                ), f"{backend}/{key}: incorrect median_busy_cycles"
            )
        if "peak_outstanding" in run:
            require(
                run["peak_outstanding"] == max(
                    row["peak_outstanding"] for row in selected
                ), f"{backend}/{key}: incorrect peak_outstanding"
            )
        runs[key] = run
    require(
        len(runs) == len(VARIANTS) * len(PROFILES) * REPEATS,
        f"{backend}: incomplete run coverage"
    )
    return {"benchmark": benchmark, "rows": rows, "runs": runs}


def stable_run(run):
    return {
        key: {
            field: value
            for field, value in run[key].items()
            if field not in WALL_FIELDS
        } if key == "summary" else run[key]
        for key in run
        if key not in {"trial", "build_wall_seconds"}
    }


def stable_row(row):
    return {key: value for key, value in row.items() if key != "trial"}


def measurement_stats(values):
    return {
        "samples": values,
        "count": len(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.mean(values),
        "stdev": statistics.stdev(values),
        "variance": statistics.variance(values),
    }


def compare(verilator, arcilator, expected_head):
    require(
        isinstance(expected_head, str)
        and re.fullmatch(r"[0-9a-f]{40}", expected_head) is not None,
        "Expected CI head is required: set GITHUB_SHA or pass --expected-head"
    )
    cases = expected_cases()
    datasets = {
        backend: load_backend(Path(directory), backend, expected_head, cases)
        for backend, directory in (("verilator", verilator),
                                   ("arcilator", arcilator))
    }
    left, right = datasets.values()
    for field in ("fixture", "source_manifest", "source_revisions", "profiles",
                  "timing", "cpu_overlap_variants"):
        require(
            left["benchmark"][field] == right["benchmark"][field],
            f"Backend manifest mismatch: {field}"
        )
    for key, run in left["runs"].items():
        require(
            stable_run(run) == stable_run(right["runs"][key]),
            f"Backend summary/counter mismatch: {key}"
        )
    for key, row in left["rows"].items():
        require(
            row == right["rows"][key],
            f"Backend CSV counter/correctness mismatch: {key}"
        )
    for backend, data in datasets.items():
        for key, run in data["runs"].items():
            require(
                stable_run(run) == stable_run(data["runs"][(*key[:2], 0)]),
                f"{backend}: non-deterministic summary/counters for {key}"
            )
        for key, row in data["rows"].items():
            require(
                stable_row(row) == stable_row(
                    data["rows"][(*key[:2], 0, key[3])]
                ), f"{backend}: non-deterministic case counters for {key}"
            )
    operator_comparisons = []
    wall_timings = []
    for profile in PROFILES:
        profile_name = profile["name"]
        baseline = left["runs"][("E1", profile_name, 0)]["totals"]
        for variant in VARIANTS:
            candidate = left["runs"][(variant, profile_name, 0)]["totals"]
            operator_comparisons.append({
                "profile":
                profile_name,
                "baseline_variant":
                "E1",
                "candidate_variant":
                variant,
                "baseline_totals":
                baseline,
                "candidate_totals":
                candidate,
                "busy_cycle_ratio_baseline_over_candidate":
                baseline["busy_cycles"] / candidate["busy_cycles"],
                "command_cycle_ratio_baseline_over_candidate":
                baseline["axi_testbench_command_cycles"] /
                candidate["axi_testbench_command_cycles"],
            })
            timings = {
                backend:
                measurement_stats([
                    data["runs"][(variant, profile_name,
                                  trial)]["summary"]["simulation_wall_seconds"]
                    for trial in range(REPEATS)
                ])
                for backend, data in datasets.items()
            }
            wall_timings.append({
                "variant":
                variant,
                "profile":
                profile_name,
                "seconds":
                timings,
                "median_ratio_verilator_over_arcilator":
                timings["verilator"]["median"] /
                timings["arcilator"]["median"],
            })
    return {
        "schema":
        1,
        "status":
        "PASS",
        "ci_head":
        expected_head,
        "source_revisions":
        SOURCE_REVISIONS,
        "fixture_sha256":
        FIXTURE_SHA256,
        "source_manifest":
        left["benchmark"]["source_manifest"],
        "coverage_per_backend": {
            "variants": len(VARIANTS),
            "profiles": len(PROFILES),
            "trials": REPEATS,
            "runs": len(left["runs"]),
            "cases_per_run": CASE_COUNT,
            "case_rows": len(left["rows"]),
            "integer_subgroup_checks_per_run": SUBGROUP_CHECKS,
            "integer_subgroup_checks": SUBGROUP_CHECKS * len(left["runs"]),
        },
        "case_counters_equal":
        True,
        "trial_counters_deterministic":
        True,
        "operator_comparisons":
        operator_comparisons,
        "simulation_wall_timings":
        wall_timings,
        "environments": {
            "verilator": left["benchmark"].get("environment"),
            "arcilator": right["benchmark"].get("environment"),
        },
        "limitations": [
            "Operator cycles are functional simulation counts, not a hardware clock or Fmax claim.",
            "Simulator wall times depend on the CI host; ratios are reported, not performance gates.",
            "Variance and standard deviation use the sample estimator over five measured trials.",
            "Public CI covers synthetic fixtures only; historical model runs are not rerun or claimed here.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verilator", type=Path, required=True)
    parser.add_argument("--arcilator", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--expected-head", default=os.environ.get("GITHUB_SHA")
    )
    args = parser.parse_args(argv)
    try:
        report = compare(args.verilator, args.arcilator, args.expected_head)
    except (ComparisonError, KeyError, TypeError, ValueError) as error:
        report = {"schema": 1, "status": "FAIL", "failure": str(error)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps({
            key: report[key]
            for key in ("status", "coverage_per_backend", "failure")
            if key in report
        })
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
