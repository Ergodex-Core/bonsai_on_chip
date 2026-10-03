#!/usr/bin/env python3
"""Bounded executable regression for the production Verilator PCH helper."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

TARGETS = ("//tests/ci/pch:enabled", "//tests/ci/pch:disabled")
MODEL_LABEL = "//tests/ci/pch:enabled_model"
BASELINE_LABEL = "//tests/ci/pch:disabled_model"


def read_json_stream(path):
    """Bazel execution logs are consecutive JSON messages, not a JSON array."""
    text = path.read_text()
    decoder = json.JSONDecoder()
    index = 0
    records = []
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            break
        value, index = decoder.raw_decode(text, index)
        records.extend(value if isinstance(value, list) else [value])
    return records


def field(record, camel, snake, default=None):
    return record.get(camel, record.get(snake, default))


def is_model_compile(record):
    label = field(record, "targetLabel", "target_label", "")
    # Bzlmod may use the canonical main-repository prefix in execution logs.
    return record.get("mnemonic"
                      ) == "CppCompile" and label.endswith(MODEL_LABEL)


def is_baseline_compile(record):
    label = field(record, "targetLabel", "target_label", "")
    return record.get("mnemonic"
                      ) == "CppCompile" and label.endswith(BASELINE_LABEL)


def command_args(record):
    return field(record, "commandArgs", "command_args", [])


def pch_operand(arguments):
    if arguments.count("-include-pch") != 1:
        raise RuntimeError(
            "Generated C++ must explicitly consume exactly one PCH"
        )
    position = arguments.index("-include-pch") + 1
    if position >= len(arguments):
        raise RuntimeError("Missing -include-pch operand")
    return position, arguments[position]


def redirect_outputs(arguments, output):
    """Replay without changing the built object/dependency files."""
    result = list(arguments)
    found_output = False
    for index, value in enumerate(result[:-1]):
        if value == "-o":
            result[index + 1] = str(output)
            found_output = True
        elif value == "-MF":
            result[index + 1] = str(output.with_suffix(".d"))
    if not found_output:
        raise RuntimeError("Compile command has no explicit object output")
    return result


class Regression:

    def __init__(self, args, temporary):
        self.args = args
        self.temporary = Path(temporary)
        self.evidence = args.evidence_dir.resolve()
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.deadline = time.monotonic() + args.timeout
        self.steps = []
        self.results = {"status": "running", "steps": self.steps}
        self.cache = args.disk_cache.resolve(
        ) if args.disk_cache else self.temporary / "disk-cache"
        self.output_bases = [
            self.temporary / "output-one", self.temporary / "output-two"
        ]
        self.output_bases[0] = args.output_base.resolve(
        ) if args.output_base else self.output_bases[0]

    def run(
        self,
        name,
        command,
        cwd=None,
        environment=None,
        expected_success=True
    ):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("PCH regression exceeded its total time limit")
        timeout = min(900, remaining)
        started = time.monotonic()
        log = self.evidence / (name + ".log")
        with log.open("wb") as output:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise RuntimeError(f"{name} exceeded {timeout:.0f} seconds")
        self.steps.append({
            "name":
            name,
            "returncode":
            code,
            "elapsed_seconds":
            round(time.monotonic() - started, 3),
            "log":
            log.name,
        })
        if expected_success and code != 0:
            raise RuntimeError(
                f"{name} failed with exit code {code}; see {log.name}"
            )
        if not expected_success and code == 0:
            raise RuntimeError(f"{name} unexpectedly accepted an invalid PCH")
        return code, log.read_text(errors="replace")

    def bazel(self, output_base, command, *arguments):
        options = [
            "--repository_cache=" + str(self.args.repository_cache.resolve())
        ] if self.args.repository_cache else []
        return [
            self.args.bazel, "--batch", "--output_base=" + str(output_base),
            command, *options, *arguments
        ]

    def build(self, name, output_base, pic, mode="fastbuild", case=1):
        execution_log = self.temporary / (name + "-execution.json")
        options = [
            "--jobs=" + str(self.args.jobs),
            "--noshow_progress",
            "--color=no",
            "--curses=no",
            "--spawn_strategy=sandboxed",
            "--remote_download_outputs=all",
            "--disk_cache=" + str(self.cache),
            "--execution_log_json_file=" + str(execution_log),
            "--cxxopt=-DPCH_BUILD_OPTION_BIAS=11",
            "--cxxopt=-DPCH_REGRESSION_CASE=" + str(case),
            "--compilation_mode=" + mode,
        ]
        if pic:
            options.append("--force_pic")
        try:
            self.run(
                name, self.bazel(output_base, "build", *options, *TARGETS)
            )
        finally:
            if execution_log.exists():
                records = read_json_stream(execution_log)
                for record in records:
                    record.pop("environmentVariables", None)
                    record.pop("environment_variables", None)
                (self.evidence / (name + "-execution.json")
                 ).write_text(json.dumps(records, indent=2) + "\n")
        records = read_json_stream(execution_log)
        # Keep compiler flags and dependency evidence without serializing action
        # environment variables, which can contain local license configuration.
        selected = [
            record for record in records
            if is_model_compile(record) or is_baseline_compile(record)
            or record.get("mnemonic") == "VerilatorPch"
        ]
        for record in selected:
            record.pop("environmentVariables", None)
            record.pop("environment_variables", None)
        (self.evidence / (name + "-actions.json")
         ).write_text(json.dumps(selected, indent=2) + "\n")
        return records, options

    def execution_root(self, name, output_base):
        _, output = self.run(
            name, self.bazel(output_base, "info", "execution_root")
        )
        candidates = [
            Path(line.strip())
            for line in output.splitlines()
            if line.strip().startswith("/")
        ]
        if len(candidates) != 1 or not candidates[0].is_dir():
            raise RuntimeError(
                "Bazel did not return exactly one valid execution root"
            )
        return candidates[0]

    def parity(self, name, output_base, root, pic, mode="fastbuild", case=1):
        outputs = []
        for target in TARGETS:
            options = [
                "--output=files",
                "--cxxopt=-DPCH_BUILD_OPTION_BIAS=11",
                "--cxxopt=-DPCH_REGRESSION_CASE=" + str(case),
                "--compilation_mode=" + mode,
            ]
            if pic:
                options.append("--force_pic")
            _, files = self.run(
                name + "-query-" + target.rsplit(":", 1)[1],
                self.bazel(output_base, "cquery", *options, target)
            )
            paths = [
                root / line.strip()
                for line in files.splitlines()
                if line.strip().startswith("bazel-out/")
            ]
            if len(paths) != 1 or not paths[0].is_file():
                raise RuntimeError(
                    "Fixture cquery did not resolve exactly one executable"
                )
            _, output = self.run(
                name + "-" + target.rsplit(":", 1)[1], [str(paths[0])],
                cwd=root
            )
            if not output.startswith("PASS samples=16384 "):
                raise RuntimeError(
                    "Fixture did not pass its sequential/arithmetic oracle"
                )
            outputs.append(output)
        if outputs[0] != outputs[1]:
            raise RuntimeError(
                "PCH-enabled and PCH-disabled model results differ"
            )
        return outputs[0].strip()

    def check_consumption(self, name, records, root):
        compiles = [record for record in records if is_model_compile(record)]
        baseline = [
            record for record in records if is_baseline_compile(record)
        ]
        if not compiles:
            raise RuntimeError(
                "No executed generated-C++ compile actions were captured; use a fresh output base"
            )
        if len(compiles) != len(baseline):
            raise RuntimeError(
                "PCH changed the number of generated-C++ object compilations"
            )
        if any("_pch_selection" in " ".join(command_args(record))
               for record in compiles + baseline):
            raise RuntimeError(
                "An analysis-only object-selection probe unexpectedly executed"
            )
        if any("-include-pch" in command_args(record) for record in baseline):
            raise RuntimeError(
                "The no-PCH baseline unexpectedly consumes a PCH"
            )
        representatives = {}
        for record in compiles:
            args = command_args(record)
            _, pch = pch_operand(args)
            inputs = {item["path"] for item in record.get("inputs", [])}
            if pch not in inputs:
                raise RuntimeError(
                    "PCH is absent from the compiler action's declared inputs"
                )
            suffix = "pic" if pch.endswith(
                ".pic.pch"
            ) else "nopic" if pch.endswith(".nopic.pch") else None
            if suffix is None or not (root / pch).is_file():
                raise RuntimeError("Unexpected or absent PCH artifact")
            representatives.setdefault(suffix, record)
        invalid_pch = self.temporary / "invalid.pch"
        invalid_pch.write_bytes(
            b"deliberately invalid PCH regression fixture\n"
        )
        for suffix, record in representatives.items():
            args = redirect_outputs(
                command_args(record),
                self.temporary / (name + "-" + suffix + ".o")
            )
            # Replaying from the execroot also verifies the PCH survives deletion
            # of the producer sandbox. The invalid case changes only its operand.
            self.run(name + "-valid-" + suffix, args, cwd=root)
            position, _ = pch_operand(args)
            args[position] = str(invalid_pch)
            _, diagnostic = self.run(
                name + "-invalid-" + suffix,
                args,
                cwd=root,
                expected_success=False
            )
            if "precompiled" not in diagnostic.lower(
            ) and "pch" not in diagnostic.lower():
                raise RuntimeError(
                    "Invalid-PCH failure did not report a PCH diagnostic"
                )
        return {
            "compile_actions": len(compiles),
            "baseline_compile_actions": len(baseline),
            "variants": sorted(representatives),
        }

    def execute(self):
        _, revision = self.run(
            "repository-revision", ["git", "rev-parse", "HEAD"]
        )
        self.results["repository_revision"] = revision.strip()
        files = [Path("rules/verilator.bzl"), Path("rules/verilator_pch.bzl")]
        files.extend(
            path for path in Path("tests/ci/pch").iterdir() if path.is_file()
        )
        self.results["source_sha256"] = {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)
        }
        normal_records, _ = self.build(
            "default-build", self.output_bases[0], False
        )
        root = self.execution_root(
            "default-execution-root", self.output_bases[0]
        )
        self.results["default_output"] = self.parity(
            "default", self.output_bases[0], root, False
        )
        self.results["default_consumption"] = self.check_consumption(
            "default", normal_records, root
        )
        opt_records, _ = self.build(
            "opt-build", self.output_bases[0], False, mode="opt", case=2
        )
        root = self.execution_root("opt-execution-root", self.output_bases[0])
        self.results["opt_output"] = self.parity(
            "opt", self.output_bases[0], root, False, mode="opt", case=2
        )
        self.results["opt_consumption"] = self.check_consumption(
            "opt", opt_records, root
        )
        pic_records, _ = self.build(
            "pic-build", self.output_bases[0], True, mode="opt", case=3
        )
        root = self.execution_root("pic-execution-root", self.output_bases[0])
        self.results["pic_output"] = self.parity(
            "pic", self.output_bases[0], root, True, mode="opt", case=3
        )
        self.results["pic_consumption"] = self.check_consumption(
            "pic", pic_records, root
        )
        if len({self.results["default_output"], self.results["opt_output"],
                self.results["pic_output"]}) != 1:
            raise RuntimeError(
                "PIC and default configurations changed model behavior"
            )
        variants = set(
            self.results["default_consumption"]["variants"] +
            self.results["opt_consumption"]["variants"] +
            self.results["pic_consumption"]["variants"]
        )
        if variants != {"pic", "nopic"}:
            raise RuntimeError(
                "The regression did not exercise both PIC and non-PIC PCH variants"
            )
        if not self.args.skip_cache_relocation:
            hidden = self.temporary / "producer-output-base"
            self.output_bases[0].rename(hidden)
            try:
                self.check_cache_relocation()
            finally:
                hidden.rename(self.output_bases[0])
        self.results["status"] = "passed"

    def check_cache_relocation(self):
        """Require cached PCH use while the producer output base is unavailable."""
        cached_records, _ = self.build(
            "relocated-build", self.output_bases[1], True, mode="opt", case=3
        )
        hits = [
            record for record in cached_records
            if record.get("mnemonic") == "VerilatorPch"
            and field(record, "cacheHit", "cache_hit", False)
        ]
        if not hits:
            raise RuntimeError(
                "Relocated build did not reuse a PCH from the disk cache"
            )
        root = self.execution_root(
            "relocated-execution-root", self.output_bases[1]
        )
        self.results["relocated_output"] = self.parity(
            "relocated", self.output_bases[1], root, True, mode="opt", case=3
        )
        self.results["relocated_consumption"] = self.check_consumption(
            "relocated", cached_records, root
        )
        self.results["relocated_pch_cache_hits"] = len(hits)
        if self.results["relocated_output"] != self.results["pic_output"]:
            raise RuntimeError("Cache relocation changed model behavior")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument(
        "--disk-cache",
        type=Path,
        help="Optional local build cache across bounded retries"
    )
    parser.add_argument(
        "--repository-cache",
        type=Path,
        help="Cache verified repository downloads only"
    )
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--output-base",
        type=Path,
        help="Optional fresh initial Bazel output base"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1200,
        help="Total wall time limit in seconds"
    )
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument(
        "--skip-cache-relocation",
        action="store_true",
        help="Local diagnostic only; does not verify cached PCH relocation"
    )
    args = parser.parse_args()
    if args.timeout <= 0 or args.jobs <= 0:
        parser.error("--timeout and --jobs must be positive")
    with tempfile.TemporaryDirectory(prefix="verilator-pch-regression-"
                                     ) as temporary:
        regression = Regression(args, temporary)
        try:
            regression.execute()
        except (OSError, RuntimeError, ValueError) as error:
            regression.results.update(status="failed", error=str(error))
            print(str(error), flush=True)
        finally:
            (regression.evidence / "result.json"
             ).write_text(json.dumps(regression.results, indent=2) + "\n")
        return 0 if regression.results["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
