"""Generate with CPU reference or physical F2 FP32 projections; no fallback."""
import argparse
import importlib.util
import json
import platform
from pathlib import Path
import time

import numpy as np
import torch
import transformers

from model import LINEAR_COUNT, load_model, sha256

BACKEND_SHA256 = "3ba5ef687c7f9453e83888ed61791a6701070ad7a178fe0c9039793fadc9d75d"
SCOPE = "hybrid: all 196 decoder projections and tied LM head on physical FPGA; embedding, normalization, RoPE, attention, KV cache, SiLU, residuals and token selection on CPU"


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def open_backend(root, slot):
    root = root.resolve()
    source = root / "host/backend.py"
    if sha256(source) != BACKEND_SHA256:
        raise ValueError(
            "External qwen35 backend differs from the reviewed source hash"
        )
    spec = importlib.util.spec_from_file_location(
        "bonsai_external_qwen35_backend", source
    )
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    hashes = {
        name: sha256(root / name)
        for name in (
            "host/backend.py", "build/libbario.so", "reports/deployment.json",
            "reports/registers.json"
        )
    }
    return backend, backend.FpgaDevice(slot), hashes


def install_linears(model, backend, device):
    selected = [(name, module)
                for name, module in model.named_modules()
                if isinstance(module, torch.nn.Linear)]
    if len(selected) != LINEAR_COUNT:
        raise ValueError(
            "Unexpected linear inventory before FPGA installation"
        )
    installed = {}
    for name, module in selected:
        parent_name, _, child = name.rpartition(".")
        replacement = backend.FpgaLinear(module, device, verify=True)
        parent = model.get_submodule(parent_name) if parent_name else model
        setattr(parent, child, replacement)
        installed[name] = replacement
        print(
            f"FPGA uploaded {name}: {tuple(module.weight.shape)}", flush=True
        )
    return installed


def generate(model, tokenizer, prompt, limit):
    rendered = tokenizer.apply_chat_template(
        [{
            "role": "user",
            "content": prompt
        }],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    inputs = tokenizer(rendered, return_tensors="pt", add_special_tokens=False)
    prompt_ids = inputs.input_ids[0].tolist()
    if len(prompt_ids) + limit > model.config.max_position_embeddings:
        raise ValueError(
            "Prompt plus output limit exceeds checkpoint context length"
        )
    if tokenizer.decode(prompt_ids, skip_special_tokens=False) != rendered:
        raise ValueError("Prompt tokenizer roundtrip failed")
    start = time.perf_counter()
    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=limit,
            do_sample=False,
            use_cache=True,
            return_dict_in_generate=True,
            output_logits=True,
            eos_token_id=tokenizer.eos_token_id,
            pad_token_id=tokenizer.pad_token_id,
        )
    elapsed = time.perf_counter() - start
    tokens = output.sequences[0, len(prompt_ids):].tolist()
    logits = torch.stack(output.logits).float().cpu().numpy()
    if not tokens or logits.shape != (
            len(tokens), 1, len(tokenizer)) or not np.isfinite(logits).all():
        raise ValueError(
            "Missing, incorrectly shaped or non-finite full-vocabulary logits"
        )
    if np.argmax(logits[:, 0], axis=-1).tolist() != tokens:
        raise ValueError(
            "Generated tokens differ from raw-logit greedy decisions"
        )
    return {
        "prompt": prompt,
        "rendered_prompt": rendered,
        "prompt_token_ids": prompt_ids,
        "max_new_tokens": limit,
        "generated_token_ids": tokens,
        "response": tokenizer.decode(tokens, skip_special_tokens=True),
        "stop_reason":
        "eos" if tokens[-1] == tokenizer.eos_token_id else "token_limit",
        "generation_seconds_including_prefill_and_first_use_verification":
        elapsed
    }, logits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--backend", choices=("cpu", "fpga"), required=True)
    parser.add_argument("--external-qwen35", type=Path)
    parser.add_argument("--slot", type=int, default=0)
    parser.add_argument("--prompt", default="what is capital of India?")
    parser.add_argument("--max-new-tokens", type=int, default=32)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.max_new_tokens <= 0 or args.threads <= 0:
        parser.error("Token limit and thread count must be positive")
    if args.backend == "fpga" and args.external_qwen35 is None:
        parser.error(
            "FPGA mode requires the reviewed external qwen35 workspace"
        )
    if args.report.exists() or args.report.with_suffix(".npz").exists():
        parser.error(
            "Report/logit output already exists; choose a fresh filename"
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "incomplete",
        "backend": args.backend,
        "fpga_executed": False,
        "scope": SCOPE if args.backend == "fpga" else
        "CPU FP32 reference only; no FPGA execution",
        "python": platform.python_version(),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "numpy": np.__version__,
        "projection_validation":
        "CPU reference comparison on first FPGA use of each of 197 projections; failures abort, no CPU fallback",
        "threads": args.threads,
        "source_sha256": {
            name: sha256(Path(__file__).parent / name)
            for name in ("model.py", "run.py")
        }
    }
    write_json(args.report, report)
    device = None
    installed = {}
    try:
        if args.backend == "fpga":
            backend, device, external_hashes = open_backend(
                args.external_qwen35, args.slot
            )
            report["external_backend_sha256"] = external_hashes
        model, tokenizer, provenance = load_model(args.model, args.threads)
        report.update(provenance)
        if device is not None:
            installed = install_linears(model, backend, device)
        write_json(args.report, report)
        generation, logits = generate(
            model, tokenizer, args.prompt, args.max_new_tokens
        )
        report.update(generation)
        if device is not None:
            device.verify_loaded_image()
            report["fpga"] = device.stats()
            report["invoked_projection_names"] = sorted(
                name for name, module in installed.items() if module.invoked
            )
            report["fpga_executed"] = device.calls > 0
            if (not report["fpga_executed"]
                    or len(report["invoked_projection_names"]) != LINEAR_COUNT
                    or device.invoked_linears != LINEAR_COUNT
                    or device.verified_linears != LINEAR_COUNT
                    or report["fpga"]["axi_error_flags"] != 0):
                raise RuntimeError(
                    "FPGA execution/197-projection coverage/first-use numerical verification incomplete"
                )
        np.savez(args.report.with_suffix(".npz"), logits=logits)
        report["logits_sha256"] = sha256(args.report.with_suffix(".npz"))
        report["status"] = "complete"
        write_json(args.report, report)
        print(
            json.dumps({
                k: report[k]
                for k in (
                    "status", "backend", "fpga_executed",
                    "generated_token_ids", "response"
                )
            },
                       indent=2),
            flush=True
        )
    except BaseException as error:
        report.update(
            status="failed", error=f"{type(error).__name__}: {error}"
        )
        if device is not None:
            # Preserve completed work without attempting MMIO after a hardware failure.
            report["fpga_executed"] = device.calls > 0
            report["partial_fpga_state"] = {
                "completed_calls":
                device.calls,
                "invoked_linears":
                device.invoked_linears,
                "verified_linears":
                device.verified_linears,
                "uploaded_weight_bytes":
                device.uploaded_bytes,
                "invoked_projection_names":
                sorted(
                    name for name, module in installed.items()
                    if module.invoked
                ),
            }
        write_json(args.report, report)
        raise
    finally:
        if device is not None:
            device.close()


if __name__ == "__main__":
    main()
