"""Check Q1_0 decoding and tokenizer against an external Prism llama.cpp build."""
import argparse
import ctypes as C
import json
from pathlib import Path
import subprocess

import numpy as np

from model import build_tokenizer, decode_q1, read_checkpoint, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--ggml-base-library", type=Path, required=True)
    parser.add_argument("--tokenize-reference", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Validation report already exists")
    reader, metadata = read_checkpoint(args.model)
    library = C.CDLL(str(args.ggml_base_library.resolve()))
    reference_decode = library.dequantize_row_q1_0
    reference_decode.argtypes = [C.c_void_p, C.c_void_p, C.c_int64]
    reference_decode.restype = None
    rng = np.random.default_rng(197)
    blocks = rng.integers(0, 256, (1024, 18), dtype=np.uint8)
    scales = rng.uniform(-20, 20, 1024).astype("<f2")
    # Include zero, both signs, and the smallest/largest finite FP16 scales.
    scales[:6] = [0.0, -0.0, 2**-24, -(2**-24), 65504, -65504]
    blocks[:, :2] = scales.view(np.uint8).reshape(-1, 2)
    model_blocks = []
    for tensor in reader.tensors:
        if int(tensor.tensor_type) == 41:
            original = tensor.data.reshape(-1, 18)
            model_blocks.extend(original[[0, len(original) // 2, -1]])
    blocks = np.concatenate([blocks, np.asarray(model_blocks)])
    reference = np.empty((len(blocks), 128), np.float32)
    reference_decode(blocks.ctypes.data, reference.ctypes.data, reference.size)
    decoded = decode_q1(blocks)
    if not np.array_equal(decoded.view(np.uint32), reference.view(np.uint32)):
        raise AssertionError(
            "Q1_0 dequantized bits differ from independent C implementation"
        )
    tokenizer = build_tokenizer(metadata)
    rendered = tokenizer.apply_chat_template(
        [{
            "role": "user",
            "content": "what is capital of India?"
        }],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    cases = [
        rendered, "what is capital of India?", " India\nभारत: New Delhi 1234!"
    ]
    results = []
    for text in cases:
        process = subprocess.run(
            [
                str(args.tokenize_reference.resolve()),
                str(args.model.resolve())
            ],
            input=text,
            text=True,
            capture_output=True,
            check=True,
        )
        expected = json.loads(process.stdout)
        actual = tokenizer(text, add_special_tokens=False).input_ids
        if actual != expected or tokenizer.decode(
                actual, skip_special_tokens=False) != text:
            raise AssertionError(
                "Tokenizer reference parity or roundtrip failed"
            )
        results.append({"text": text, "token_ids": actual, "roundtrip": True})
    report = {
        "passed": True,
        "gguf_sha256": sha256(args.model),
        "model_loader_sha256": sha256(Path(__file__).parent / "model.py"),
        "codec_blocks": len(blocks),
        "codec_values": reference.size,
        "real_tensor_count": 197,
        "real_blocks": len(model_blocks),
        "reference_library_sha256": sha256(args.ggml_base_library),
        "reference_tokenizer_sha256": sha256(args.tokenize_reference),
        "tokenizer_cases": results
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
