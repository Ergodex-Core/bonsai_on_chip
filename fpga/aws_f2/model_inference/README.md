# Bonsai Q1_0 on the existing F2 FP32 projection engine

This adapter is designed to run the exact `prism-ml/Bonsai-1.7B-gguf` checkpoint with all
196 decoder projections and its tied LM head on a physical FPGA. Embeddings,
normalization, YaRN RoPE, attention, KV cache, SiLU, residuals and greedy token
selection run on the CPU. Each projection also receives a CPU comparison on
its first FPGA use; a mismatch aborts. There is no CPU fallback in FPGA mode.

Q1_0 weights are decoded exactly into FP32 before upload. This uses the
existing generic FP32 GEMV engine, **not a native 1-bit FPGA accelerator**.
The CPU reference uses the same decoded weights and FP32 activations. It does
not promise bitwise equivalence to llama.cpp's quantized activation kernels.

## Current validation

Local CPU generation passed at an eight-token limit, producing
`The capital of India is **New Delhi`. Independent C-reference checks passed
for 206,720 decoded FP32 values and all three tokenizer cases. The focused
failed-run evidence test also passed. These results establish software
readiness only.

Physical FPGA generation and paired CPU/FPGA token/logit comparison remain
pending approval to transfer the external reviewed driver. No physical FPGA
inference result is claimed by this initial adapter validation.

## Checkpoint and execution contract

- Hugging Face revision: `210a9e99f79cb184909d49595906526eb2b3dd9a`.
- File: `Bonsai-1.7B-Q1_0.gguf`, 248,302,272 bytes.
- SHA-256: `3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3`.
- Actual GGUF: 28 Qwen3 blocks, 197 Q1_0 matrices, 113 FP32 vectors,
  151,669 vocabulary entries, tied embedding/head, YaRN factor 4 with
  original context 8,192. The loader checks this file and inventory strictly.
- Tokenizer vocabulary, merges, special tokens and chat template come from
  that GGUF. The template already emits an empty thinking block.
- Default prompt: `what is capital of India?`; greedy decoding, at most
  32 new tokens, with early EOS permitted.

The original `Ternary-Bonsai-1.7B-PQ2_0.gguf` is a different checkpoint and
packing format. It is rejected by this adapter.

## Prerequisites

Use Python 3.11 or later, `torch==2.14.0+cpu`, `transformers==5.16.1`,
`numpy==2.5.2`, `gguf==0.19.0` and `tokenizers==0.23.2`. Install the CPU
PyTorch wheel from the official PyTorch CPU index in an isolated environment.

FPGA mode additionally requires an existing reviewed `qwen35` workspace:
`host/backend.py`, `build/libbario.so`, `reports/deployment.json`,
`reports/registers.json`, and its `logs/` directory. The backend source SHA-256
must be `3ba5ef687c7f9453e83888ed61791a6701070ad7a178fe0c9039793fadc9d75d`.
The runner records hashes of all four runtime inputs. This external backend
and its compiled artifacts are not distributed in this repository.

Load the matching available AGFI on a reserved F2 slot using the reviewed
external deployment workflow before running this adapter. The backend checks
the loaded AGFI, kernel identity/ABI, DDR readiness and AXI errors. A simulator,
unloaded card, different image or missing external workspace cannot satisfy
FPGA mode. Model weights and output evidence should remain outside tracked
source directories.

## Run and compare

From the repository root, with `MODEL`, `QWEN35_ROOT`, `PYTHON` and `OUT` set to
absolute paths for the verified checkpoint, external backend workspace,
Python executable and a fresh evidence directory:

```bash
ADAPTER=fpga/aws_f2/model_inference
"$PYTHON" "$ADAPTER/run.py" --model "$MODEL" --backend cpu \
  --report "$OUT/cpu.json"
"$PYTHON" "$ADAPTER/run.py" --model "$MODEL" --backend fpga \
  --external-qwen35 "$QWEN35_ROOT" --slot 0 --report "$OUT/fpga.json"
"$PYTHON" "$ADAPTER/compare.py" "$OUT/cpu.json" "$OUT/fpga.json" \
  --report "$OUT/parity.json"
```

Both runs must use identical adapter bytes, dependencies, thread count, prompt
and output limit. Existing outputs are refused. Reports begin as incomplete;
failed runs retain partial host counters without reporting a pass. Complete
hardware reports require all 197 unique projections to execute and pass their
first-use comparison, at least one completed FPGA call, and zero AXI errors.
The comparison requires matching token IDs and finite full-vocabulary logits
within `rtol=1e-3`, `atol=1e-3`. At a divergent decision it compares that step
but excludes later logits whose input histories differ, and fails overall.

The timing includes prefill, host work, MMIO and first-use verification. It
excludes model loading and weight upload; it is not a throughput benchmark.

## Independent adapter checks

`validate_adapter.py` compares 1,024 directed/random Q1_0 blocks and three
original blocks from each of the 197 matrices against Prism's C dequantizer.
It compares exact FP32 bits, including signed zero and finite FP16 extremes.
Three tokenizer cases, including the full rendered user prompt, are compared
against a vocabulary-only Prism llama.cpp model load and checked for roundtrip.

Build the small reference utility against an existing pinned Prism llama.cpp
checkout and shared-library build. Set `LLAMA_SOURCE` and `LLAMA_LIB` to their
absolute directories; no compiler or model source is copied into this repo.

```bash
mkdir -p "$OUT"
c++ -std=c++17 -I "$LLAMA_SOURCE/include" -I "$LLAMA_SOURCE/ggml/include" \
  "$ADAPTER/tokenize_reference.cc" -L "$LLAMA_LIB" -lllama \
  -Wl,-rpath,"$LLAMA_LIB" -o "$OUT/tokenize-reference"
"$PYTHON" "$ADAPTER/validate_adapter.py" --model "$MODEL" \
  --ggml-base-library "$LLAMA_LIB/libggml-base.so" \
  --tokenize-reference "$OUT/tokenize-reference" \
  --report "$OUT/adapter-checks.json"
```

On macOS use the matching library architecture and `.dylib` filename. These
checks establish codec/tokenizer compatibility. Physical FPGA output is
established only by the separate hardware run and paired report comparison.

Run the failure-evidence regression in the same Python environment:

```bash
"$PYTHON" -m unittest discover -s "$ADAPTER" -p test_runner.py -v
```
