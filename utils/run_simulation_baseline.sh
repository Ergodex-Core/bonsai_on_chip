#!/usr/bin/env bash
# Reproduce the repository's existing simulator baseline. This is not Arcilator.
set -euo pipefail

mode=${1:-smoke}
script_directory=$(dirname "$0")
script_directory=$(cd "${script_directory}" && pwd)
script_name=$(basename "$0")
script_path=${script_directory}/${script_name}
repo_root=$(cd "${script_directory}/.." && pwd)
result_dir=${2:-"${repo_root}/reports/ERG-102/raw/$(date -u +%Y%m%dT%H%M%SZ)-${mode}"}
case "${mode}" in inventory|smoke|all) ;; *)
  echo "usage: $0 {inventory|smoke|all} [RESULT_DIR]" >&2
  exit 2
esac
cd "${repo_root}"
mkdir -p "${result_dir}"
result_dir=$(cd "${result_dir}" && pwd)
if [[ -e "${result_dir}/manifest.txt" ]]; then
  echo "Refusing to overwrite an existing run: ${result_dir}" >&2
  exit 2
fi

{
  run_time=$(date -u +%FT%TZ)
  printf 'UTC: %s\nMode: %s\n' "${run_time}" "${mode}"
  printf 'Source revision: '
  git rev-parse HEAD
  printf 'Source status (empty means clean):\n'
  git status --porcelain=v1
  printf 'Execution host: '
  uname -sm
  bazel --version
  printf 'Baseline backend: repository defaults (Verilator/Chisel/host tests).\n'
  printf 'Excluded tags: vcs,synthesis,power,spyglass; manual targets are excluded by Bazel wildcard selection.\n'
  printf 'No Arcilator or physical FPGA pass is implied by this run.\n'
} > "${result_dir}/manifest.txt"
git diff HEAD --binary > "${result_dir}/source.patch"
sha256sum "${script_path}" > "${result_dir}/runner.sha256"
# Include new source files in provenance; raw result directories are gitignored.
git ls-files -z --cached --others --exclude-standard > "${result_dir}/source-files.list"
while IFS= read -r -d '' input_file; do
  if [[ -f "${input_file}" ]]; then
    sha256sum -- "${input_file}"
  fi
done < "${result_dir}/source-files.list" > "${result_dir}/source-files.sha256"
if [[ -f .bazelrc.user ]]; then
  sha256sum .bazelrc.user > "${result_dir}/local-config.sha256"
  printf 'Local .bazelrc.user is active; only its hash is recorded, not potentially sensitive contents. Reproduction requires matching configuration.\n' >> "${result_dir}/manifest.txt"
fi

bazel_args=()
if [[ -n "${BAZEL_OUTPUT_USER_ROOT:-}" ]]; then
  bazel_args+=("--output_user_root=${BAZEL_OUTPUT_USER_ROOT}")
fi
if [[ -n "${BAZEL_JVM_HEAP:-}" ]]; then
  bazel_args+=("--host_jvm_args=-Xmx${BAZEL_JVM_HEAP}")
fi

set +e
bazel "${bazel_args[@]}" query 'kind(".*test rule", //...)' \
  --output=xml --noshow_progress \
  > "${result_dir}/test-inventory.xml" 2> "${result_dir}/inventory.log"
inventory_status=$?
set -e
printf '%s\n' "${inventory_status}" > "${result_dir}/inventory.exitcode"
if (( inventory_status != 0 )); then
  cat "${result_dir}/inventory.log" >&2
  exit "${inventory_status}"
fi
if [[ "${mode}" == inventory ]]; then
  echo "Inventory saved: ${result_dir}/test-inventory.xml"
  exit 0
fi

common_args=(--build_tests_only --keep_going --verbose_failures --noshow_progress
  --test_output=errors --test_summary=detailed
  "--jobs=${SIM_BUILD_JOBS:-4}" "--local_test_jobs=${SIM_TEST_JOBS:-2}")
failed=0
run_group() {
  local group=$1
  shift
  printf '%q ' bazel "${bazel_args[@]}" test "${common_args[@]}" "$@" \
    > "${result_dir}/${group}.command"
  printf '\n' >> "${result_dir}/${group}.command"
  set +e
  bazel "${bazel_args[@]}" test "${common_args[@]}" \
    "--build_event_json_file=${result_dir}/${group}.bep.jsonl" "$@" \
    2>&1 | tee "${result_dir}/${group}.log"
  local statuses=("${PIPESTATUS[@]}")
  set -e
  printf '%s\n' "${statuses[0]}" > "${result_dir}/${group}.exitcode"
  printf '%s\n' "${statuses[1]}" > "${result_dir}/${group}.logging-exitcode"
  if (( statuses[0] != 0 || statuses[1] != 0 )); then
    failed=1
  fi
}

if [[ "${mode}" == smoke ]]; then
  run_group smoke //tests/cocotb:core_mini_axi_sim_cocotb
else
  run_group parallel --test_tag_filters=-exclusive,-vcs,-synthesis,-power,-spyglass //...
  run_group exclusive --test_tag_filters=exclusive,-vcs,-synthesis,-power,-spyglass //...
fi

set +e
python3 - "${result_dir}" "${mode}" <<'PY'
import collections
import hashlib
import json
import pathlib
import shutil
import sys
import urllib.parse
import xml.etree.ElementTree as ET

directory = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
inventory = ET.parse(directory / "test-inventory.xml").getroot()
expected = {"parallel": set(), "exclusive": set(), "smoke": {"//tests/cocotb:core_mini_axi_sim_cocotb"}}
for rule in inventory.findall("rule"):
    tags = {tag.get("value") for tag in rule.findall("list[@name='tags']/string")}
    if tags & {"manual", "vcs", "synthesis", "power", "spyglass"}:
        continue
    expected["exclusive" if "exclusive" in tags else "parallel"].add(rule.get("name"))
summaries = {}
for group in (["smoke"] if mode == "smoke" else ["parallel", "exclusive"]):
    path = directory / f"{group}.bep.jsonl"
    tests = []
    artifacts = []
    artifact_errors = []
    result_records = collections.Counter()
    event_ids = set()
    announced_ids = set()
    terminal_event = False
    completed = None
    aborted = []
    parse_errors = []
    lines = path.read_text().splitlines() if path.exists() else []
    if not path.exists():
        parse_errors.append({"error": "missing build event stream"})
    for number, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            parse_errors.append({"line": number, "error": str(error)})
            continue
        if "id" in event:
            event_ids.add(json.dumps(event["id"], sort_keys=True))
        announced_ids.update(json.dumps(child, sort_keys=True) for child in event.get("children", []))
        terminal_event = terminal_event or event.get("lastMessage") is True
        if "finished" in event:
            completed = event["finished"]
        if "aborted" in event:
            aborted.append({"id": event.get("id"), "details": event["aborted"]})
        result = event.get("testResult")
        if result is not None:
            identifier = event["id"]["testResult"]
            result_records[identifier["label"]] += 1
            saved_outputs = 0
            # The target/attempt identity avoids collisions between test outputs.
            identity = json.dumps(identifier, sort_keys=True)
            destination = directory / "test-artifacts" / group / hashlib.sha256(identity.encode()).hexdigest()[:20]
            for output in result.get("testActionOutput", []):
                uri = urllib.parse.urlparse(output.get("uri", ""))
                if uri.scheme != "file":
                    artifact_errors.append({"test": identifier, "error": "non-local test output", "output": output})
                    continue
                source = pathlib.Path(urllib.parse.unquote(uri.path))
                if not source.is_file():
                    artifact_errors.append({"test": identifier, "error": "missing test output", "output": output})
                    continue
                destination.mkdir(parents=True, exist_ok=True)
                target = destination / pathlib.Path(output.get("name", source.name)).name
                shutil.copy2(source, target)
                artifacts.append({"test": identifier, "path": str(target.relative_to(directory)), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
                saved_outputs += 1
            if saved_outputs == 0:
                artifact_errors.append({"test": identifier, "error": "test result has no preserved output"})
        summary = event.get("testSummary")
        if summary is not None:
            tests.append({
                "label": event["id"]["testSummary"]["label"],
                "status": summary.get("overallStatus", "UNKNOWN"),
                "runs": summary.get("totalRunCount", 0),
                "cached_runs": summary.get("totalNumCached", 0),
            })
    for test in tests:
        if result_records[test["label"]] < max(1, test["runs"]):
            artifact_errors.append({"label": test["label"], "error": "missing test-result records", "expected_runs": test["runs"], "reported_results": result_records[test["label"]]})
    unreceived_events = sorted(announced_ids - event_ids)
    reported = {test["label"] for test in tests}
    missing = sorted(expected[group] - reported)
    logging_exit = int((directory / f"{group}.logging-exitcode").read_text())
    summaries[group] = {
        "logging_exit_code": logging_exit,
        "expected_candidate_targets": len(expected[group]),
        "targets_without_test_summary": missing,
        "build_finished_event": completed,
        "aborted_events": aborted,
        "event_parse_errors": parse_errors,
        "terminal_event_received": terminal_event,
        "unreceived_announced_events": [json.loads(identifier) for identifier in unreceived_events],
        "test_artifacts": artifacts,
        "artifact_errors": artifact_errors,
        "evidence_complete": not missing and not parse_errors and not artifact_errors and not unreceived_events and terminal_event and completed is not None and logging_exit == 0,
        "bazel_exit_code": int((directory / f"{group}.exitcode").read_text()),
        "reported_test_targets": len(tests),
        "target_status_counts": dict(collections.Counter(t["status"] for t in tests)),
        "tests": tests,
    }
output = {
    "backend": "repository-defaults; not Arcilator",
    "counting": "Bazel target summaries, not individual cocotb/Scala testcase counts",
    "caveat": "Unbuilt, filtered, unsupported, manual and skipped tests are not passes. Inspect inventory, test XML and logs for exact coverage.",
    "groups": summaries,
}
(directory / "summary.json").write_text(json.dumps(output, indent=2) + "\n")
for name, data in summaries.items():
    print(name, "exit=", data["bazel_exit_code"], data["target_status_counts"], "without_result=", len(data["targets_without_test_summary"]), "evidence_complete=", data["evidence_complete"])
sys.exit(0 if all(data["evidence_complete"] for data in summaries.values()) else 1)
PY
summary_status=$?
set -e
if (( summary_status != 0 )); then failed=1; fi
echo "Evidence saved: ${result_dir}"
exit "${failed}"
