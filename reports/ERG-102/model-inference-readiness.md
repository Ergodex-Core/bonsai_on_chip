# Requested Bonsai 1.7B model demonstration

Status: **CPU reference complete; FPGA model demonstration on hold after the
user prioritized Week 0 closure**.
Date: 2026-09-26. Issue: ERG-102.
PR: [#1](https://github.com/Ergodex-Core/bonsai_on_chip/pull/1).
Validated adapter revision: `22a362d5e013047079e51d08278ac0b9e6f38f5d`.

## What ran on the F2 host

The previous F2 work ran on its host CPU: nine AWS AXI-Lite example XSIM tests
and 1,332 repository-default targets passed (1,046 fresh and 286 cached).
The subsequent official AWS shell/MMIO test ran on the physical FPGA, as
recorded in the Week 0 report. No model token was produced by the FPGA. The separate
Arcilator/Verilator pilot passed four configurations locally; it is not a
full Arcilator suite result. See the [baseline report](README.md).

## Requested checkpoint and CPU result

Model: [prism-ml/Bonsai-1.7B-gguf](https://huggingface.co/prism-ml/Bonsai-1.7B-gguf),
revision `210a9e99f79cb184909d49595906526eb2b3dd9a`,
file `Bonsai-1.7B-Q1_0.gguf`, 248,302,272 bytes.
SHA-256: `3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3`.

Exact prompt: `what is capital of India?`.

The local macOS arm64 CPU reference used four threads, greedy decoding and a
32-token maximum. Its actual decoded output was:

```text
The capital of India is **New Delhi**. It is the largest city in India and serves as the political, economic, and cultural center of the country.
```

This is verbatim model output; its additional claims were not fact-checked.

Generated token IDs, including special tokens:

```json
[785, 6722, 315, 6747, 374, 3070, 3564, 21996, 334, 13, 1084, 374, 279, 7772, 3283, 304, 6747, 323, 17045, 438, 279, 4948, 11, 6955, 11, 323, 12752, 4126, 315, 279, 3146, 13]
```

Stop reason: `token_limit`; generated tokens: 32.
This is **CPU-only output**. It is not an FPGA result or the paired Linux
reference needed for the eventual hardware comparison. An earlier local
launch failed on an interpreter/NumPy architecture mismatch; the native-arm64
retry completed with unchanged model and adapter sources.

The [machine-readable evidence](bonsai1p7b-adapter.json) records actual software
versions, source hashes, token IDs and the complete-logit archive hash. Raw logs,
logits, commands and dependency inventory remain in the ignored local evidence
directory. Model weights and the private external driver are not published.

Independent adapter validation passed for 1,615 Q1_0 blocks: 206,720 FP32 values
match the pinned Prism C codec bit for bit. Three tokenizer cases, including
the full rendered prompt, match the llama.cpp reference. A focused failure test
verifies that partial hardware work cannot be reported as a successful run.
A source review also confirmed matching Q/K rotation layout and YaRN defaults
against Prism commit `bdc23b56b4458b9f1655aec5287f3ab56ee8daaa`.
These checks validate the adapter components; they do not establish full-model
logit parity with llama.cpp's quantized activation kernels.

## Physical FPGA run still required

The prepared path uses an existing FP32 GEMV image: all 196 decoder projections
and the tied language-model head run on the FPGA. Embeddings, normalization,
RoPE, attention, KV cache, SiLU, residuals and token selection run on the CPU.
Q1_0 weights expand exactly to FP32. This is a hybrid demonstration using a
generic projection engine; native 1-bit RTL and Bonsai shell integration remain
separate development work.

The user prioritized Week 0 interface and environment completion before this
model demonstration. The physical vendor-shell test satisfies that separate
bring-up scope; this adapter remains follow-on work. Automatic approval review
previously rejected the private external driver transfer and execution pending
explicit authorization. No such transfer or model hardware run is claimed.
The staged model, environment and earlier cloud evidence remain on persistent EBS.

If this diagnostic is resumed, first obtain the outstanding private-driver
transfer authorization and verify host/slot ownership and the matching image.
Run the hardware smoke, then CPU and FPGA generation in the same Linux
environment and compare all token IDs and full-vocabulary logits at the
predeclared `rtol=1e-3`, `atol=1e-3`. Hardware evidence must show all 197
projections invoked and verified, completed FPGA calls, unchanged image identity
and zero AXI errors. No CPU fallback is permitted. The
[adapter instructions](../../fpga/aws_f2/model_inference/README.md) provide exact
run and compare commands. This pending model gate does not close with ERG-102.
