#!/usr/bin/env python3
"""Build and compare native PQ2 engines on an explicitly allocated EC2 worker.

No SSH, instance launch, or credential handling. The controller copies sources
to the allocated worker and invokes this entrypoint there. Evidence excludes
model payloads, hostnames, absolute paths, and cloud identifiers.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time

from native_reference import ROM_BYTES, write_fixtures

HERE = Path(__file__).resolve().parent
LABEL = re.compile(r"^[A-Za-z0-9_-]+$")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command_version(command):
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True
    )
    return completed.stdout.splitlines()[0]


def parse_profile(value):
    parts = value.split(":")
    if len(parts) != 5 or not LABEL.fullmatch(parts[0]):
        raise argparse.ArgumentTypeError(
            "profile format NAME:LATENCY:BEAT_II:CAPACITY:STALL_PERCENT"
        )
    numbers = [int(part) for part in parts[1:]]
    if not (1 <= numbers[0] <= 10000 and 1 <= numbers[1] <= 10000
            and 1 <= numbers[2] <= 1024 and 0 <= numbers[3] <= 95):
        raise argparse.ArgumentTypeError("profile values out of range")
    return dict(
        zip(("name", "latency", "beat_ii", "capacity", "stall_percent"),
            [parts[0], *numbers])
    )


def checked_run(command, logfile, timeout):
    with logfile.open("w") as log:
        completed = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False
        )
    if completed.returncode:
        # Build logs are local-only, may contain paths, and are not copied into
        # the sanitized evidence. The operator can inspect them on the worker.
        raise RuntimeError(
            f"Command failed with exit {completed.returncode}; inspect private log {logfile.name}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant", action="append", help="LABEL=RTL_PATH (repeat)"
    )
    parser.add_argument(
        "--parameter",
        action="append",
        default=[],
        help="LABEL:PARAMETER=INTEGER (repeat)"
    )
    parser.add_argument(
        "--check-cpu-overlap",
        action="append",
        default=[],
        help="Run pending-ROM/MMIO overlap regression for LABEL"
    )
    parser.add_argument("--profile", action="append", type=parse_profile)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allocation",
        required=True,
        help="Sanitized coordination label; no cloud IDs"
    )
    parser.add_argument("--seed", type=int, default=7193)
    parser.add_argument("--random-cases", type=int, default=32)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--offset", type=lambda value: int(value, 0))
    parser.add_argument("--pq2-type-id", type=int)
    parser.add_argument("--model-cases", type=int, default=16)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--verilator", default="verilator")
    parser.add_argument("--verilator-arg", action="append", default=[])
    parser.add_argument("--build-timeout", type=int, default=600)
    parser.add_argument("--simulation-timeout", type=int, default=600)
    parser.add_argument("--latency-jitter", type=int, default=0)
    parser.add_argument("--mmio-stall-max", type=int, default=3)
    args = parser.parse_args()
    if platform.system() != "Linux" or os.environ.get(
            "BENCHMARK_EC2_AUTHORIZED") != "1":
        parser.error(
            "Run only on the coordinated EC2 Linux worker with BENCHMARK_EC2_AUTHORIZED=1"
        )
    if not 1 <= args.jobs <= 2:
        parser.error("--jobs must be 1 or 2 within the shared allocation")
    if not LABEL.fullmatch(args.allocation):
        parser.error("--allocation must be a sanitized short label")
    if args.random_cases < 0 or args.model_cases < 1 or args.latency_jitter < 0 or args.mmio_stall_max < 0:
        parser.error("invalid fixture or timing count")
    if (args.model is None) != (args.offset
                                is None) or (args.model is not None
                                             and args.pq2_type_id is None):
        parser.error("--model requires explicit --offset and --pq2-type-id")
    variants = {}
    for item in args.variant or [
            f"e1={HERE / 'baseline' / 'coral_weight_axi.sv'}"
    ]:
        name, separator, path = item.partition("=")
        if not separator or not LABEL.fullmatch(name) or name in variants:
            parser.error("--variant requires a unique safe LABEL=RTL_PATH")
        source = Path(path).resolve()
        if not source.is_file():
            parser.error(f"RTL source does not exist for {name}")
        variants[name] = {"source": source, "parameters": {}}
    for item in args.parameter:
        variant, separator, setting = item.partition(":")
        parameter, equals, value = setting.partition("=")
        if not separator or not equals or variant not in variants or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", parameter):
            parser.error("invalid parameter; use LABEL:PARAMETER=INTEGER")
        if parameter in ("ROM_BYTES", "DATA_BITS", "ID_BITS", "ENGINE_ENABLE",
                         "ROM_BASE", "MMIO_BASE"):
            parser.error("benchmark protocol parameters are fixed")
        variants[variant]["parameters"][parameter] = int(value, 0)
    if any(name not in variants for name in args.check_cpu_overlap):
        parser.error("--check-cpu-overlap must name a selected variant")
    profiles = args.profile or [
        parse_profile(value) for value in (
            "ideal:1:1:8:0", "lat20:20:1:8:20", "lat80:80:4:8:20",
            "limited:80:8:2:25"
        )
    ]
    if len({profile["name"] for profile in profiles}) != len(profiles):
        parser.error("profile names must be unique")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("--output must be empty to preserve previous evidence")
    output.mkdir(parents=True, exist_ok=True)
    private = output / "private"
    private.mkdir()
    fixture_path = private / "fixtures.bin"
    fixture = write_fixtures(
        fixture_path,
        seed=args.seed,
        random_cases=args.random_cases,
        model=args.model,
        offset=args.offset,
        pq2_type_id=args.pq2_type_id,
        model_cases=args.model_cases
    )
    shutil.copyfile(
        fixture_path.with_suffix(".json"), output / "fixture-manifest.json"
    )
    source_manifest = {
        "schema": 1,
        "harness": {
            path.name: digest(path)
            for path in (
                HERE / "run_benchmark.py", HERE / "native_reference.py",
                HERE / "tests" / "test_engine.cpp"
            )
        },
        "variants": {
            name: {
                "rtl_sha256": digest(item["source"]),
                "parameters": item["parameters"]
            }
            for name, item in variants.items()
        }
    }
    (output / "source-manifest.json"
     ).write_text(json.dumps(source_manifest, indent=2) + "\n")
    evidence = {
        "schema":
        1,
        "status":
        "RUNNING",
        "allocation":
        args.allocation,
        "started_utc":
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "environment": {
            "system":
            platform.system(),
            "machine":
            platform.machine(),
            "python":
            platform.python_version(),
            "verilator":
            command_version([args.verilator, "--version"]),
            "compiler":
            command_version([os.environ.get("CXX", "c++"), "--version"]),
            "jobs":
            args.jobs
        },
        "fixture":
        fixture,
        "source_manifest":
        source_manifest,
        "profiles":
        profiles,
        "clock_constraint":
        "none; functional cycle simulation, no Fmax claim",
        "reset_contract":
        "DUT and storage adapter reset together; pending replies are flushed",
        "timing": {
            "latency_jitter": args.latency_jitter,
            "mmio_stall_max": args.mmio_stall_max
        },
        "cpu_overlap_variants":
        args.check_cpu_overlap,
        "limitations": [
            "No board measurement or synthesis utilization",
            "No full-model token or firmware CPU execution",
            "AXI testbench command latency uses a synthetic master, not compiled firmware",
            "Latency/bandwidth profiles are explicit scenarios, not calibrated HBM measurements",
            "Random stall sequence is seeded per profile and variant; cycle timing changes which requests encounter stalls"
        ],
        "runs": []
    }
    rows = []
    common_flags = [
        "--Wall", "--cc", "--exe", "--build", "-j",
        str(args.jobs), "--top-module", "coral_weight_axi", "--unroll-count",
        "4096", "--unroll-stmts", "100000", "-GROM_BYTES=" + str(ROM_BYTES),
        "-CFLAGS", "-std=c++17 -O2"
    ]
    evidence["verilator_flags"] = common_flags + args.verilator_arg
    evidence_path = output / "benchmark.json"
    try:
        for name, item in variants.items():
            build_dir = private / ("build-" + name)
            command = [
                args.verilator, *common_flags, *args.verilator_arg, "--Mdir",
                str(build_dir), *[
                    f"-G{parameter}={value}"
                    for parameter, value in item["parameters"].items()
                ],
                str(item["source"]),
                str(HERE / "tests" / "test_engine.cpp")
            ]
            begin = time.monotonic()
            checked_run(
                command, private / ("build-" + name + ".log"),
                args.build_timeout
            )
            build_seconds = time.monotonic() - begin
            for profile in profiles:
                log = private / (name + "-" + profile["name"] + ".jsonl")
                command = [
                    str(build_dir / "Vcoral_weight_axi"), "--fixtures",
                    str(fixture_path), "--seed",
                    str(args.seed), "--latency-jitter",
                    str(args.latency_jitter), "--mmio-stall-max",
                    str(args.mmio_stall_max)
                ]
                command += [
                    "--cpu-overlap",
                    str(int(name in args.check_cpu_overlap))
                ]
                for key in ("latency", "beat_ii", "capacity", "stall_percent"):
                    command += [
                        "--" + key.replace("_", "-"),
                        str(profile[key])
                    ]
                checked_run(command, log, args.simulation_timeout)
                records = [
                    json.loads(line)
                    for line in log.read_text().splitlines()
                    if line.startswith("{")
                ]
                summaries = [
                    record for record in records
                    if record.get("kind") == "summary"
                ]
                cases = [
                    record for record in records
                    if record.get("kind") == "case"
                ]
                if len(summaries) != 1 or summaries[0].get(
                        "status") != "PASS" or len(cases) != fixture["cases"]:
                    raise RuntimeError(
                        f"Incomplete correctness evidence for {name}/{profile['name']}"
                    )
                run = {
                    "variant":
                    name,
                    "profile":
                    profile["name"],
                    "build_wall_seconds":
                    build_seconds,
                    "summary":
                    summaries[0],
                    "totals": {
                        key: sum(case[key]
                                 for case in cases)
                        for key in (
                            "busy_cycles", "axi_testbench_command_cycles",
                            "storage_requests", "storage_bytes",
                            "native_products"
                        )
                    },
                    "median_busy_cycles":
                    statistics.median(case["busy_cycles"] for case in cases),
                    "peak_outstanding":
                    max(case["peak_outstanding"] for case in cases)
                }
                evidence["runs"].append(run)
                rows.extend({
                    "variant": name,
                    "profile": profile["name"],
                    **{
                        key: value
                        for key, value in profile.items() if key != "name"
                    }, "latency_jitter": args.latency_jitter,
                    "mmio_stall_max": args.mmio_stall_max,
                    **case
                }
                            for case in cases)
                evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
                print(json.dumps(run), flush=True)
        evidence["status"] = "PASS"
    except Exception as error:
        evidence["status"] = "FAIL"
        evidence["failure"] = str(error)
        raise
    finally:
        evidence["finished_utc"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
        if rows:
            with (output / "cases.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
