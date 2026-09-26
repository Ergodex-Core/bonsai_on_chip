"""Compare complete CPU and physical-FPGA runs of identical decoded GGUF weights."""
import argparse
import json
from pathlib import Path

import numpy as np

from model import LINEAR_COUNT, sha256


def compare(reference_path, fpga_path):
    reference = json.loads(reference_path.read_text())
    fpga = json.loads(fpga_path.read_text())
    if (reference["status"] != "complete" or fpga["status"] != "complete"
            or reference["backend"] != "cpu" or fpga["backend"] != "fpga"
            or not fpga["fpga_executed"]
            or fpga["fpga"]["completed_calls"] <= 0):
        raise ValueError("Require complete CPU and physical-FPGA reports")
    for key in ("model", "revision", "gguf_sha256", "arithmetic", "config",
                "linear_shapes", "chat_template_sha256", "prompt",
                "prompt_token_ids", "max_new_tokens", "torch", "transformers",
                "numpy", "threads", "source_sha256"):
        if reference[key] != fpga[key]:
            raise ValueError(f"Incompatible runs: {key}")
    if (set(fpga["invoked_projection_names"]) != set(fpga["linear_shapes"])
            or len(fpga["invoked_projection_names"]) != LINEAR_COUNT
            or fpga["fpga"]["verified_linears"] != LINEAR_COUNT
            or fpga["fpga"]["axi_error_flags"] != 0):
        raise ValueError("FPGA coverage or first-use parity is incomplete")
    arrays = []
    for path, report in ((reference_path, reference), (fpga_path, fpga)):
        artifact = path.with_suffix(".npz")
        if sha256(artifact) != report["logits_sha256"]:
            raise ValueError("Logit artifact hash mismatch")
        array = np.load(artifact, allow_pickle=False)["logits"]
        if array.shape != (len(
                report["generated_token_ids"]
        ), 1, report["vocab_size"]) or not np.isfinite(array).all():
            raise ValueError("Incomplete or non-finite full-vocabulary logits")
        arrays.append(array)
    left, right = reference["generated_token_ids"], fpga["generated_token_ids"]
    common = 0
    for a, b in zip(left, right):
        if a != b:
            break
        common += 1
    # The first differing decision still has matching input history; later ones do not.
    steps = min(common + 1, len(left), len(right))
    if not steps:
        raise ValueError("No generated decisions to compare")
    a, b = arrays[0][:steps], arrays[1][:steps]
    difference = np.abs(a - b)
    logits_match = bool(np.allclose(a, b, rtol=1e-3, atol=1e-3))
    return {
        "passed": left == right and logits_match,
        "tokens_match": left == right,
        "logits_match": logits_match,
        "common_token_prefix": common,
        "compared_logit_steps": steps,
        "vocabulary_entries_per_step": reference["vocab_size"],
        "max_absolute_logit_error": float(difference.max()),
        "rtol": 1e-3,
        "atol": 1e-3,
        "reference_report_sha256": sha256(reference_path),
        "fpga_report_sha256": sha256(fpga_path),
        "fpga_completed_calls": fpga["fpga"]["completed_calls"],
        "verified_unique_projections": LINEAR_COUNT,
        "gguf_sha256": reference["gguf_sha256"],
        "agfi": fpga["fpga"]["agfi"]
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("fpga", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Comparison report already exists")
    result = compare(args.reference, args.fpga)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
