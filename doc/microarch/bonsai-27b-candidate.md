# Bonsai 2 27B candidate: design implications

**Design research only, 2026-09-30.** The selected executable fixture remains
Bonsai-1.7B Q1_0. No 27B importer, array RTL, HBM backend, inference or token
generation is implemented by this note.

## Candidate and evidence boundary

The official [Ternary Bonsai 2 27B GGUF release](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/tree/b072e1d3b35a0a630cece372c2127528e0994386)
at revision `b072e1d3b35a0a630cece372c2127528e0994386` is a candidate,
not a selected or validated hardware checkpoint. The accompanying
[metadata manifest](bonsai-27b-candidate.json) records publisher LFS identities,
header hashes, tensor accounting and source references. We read the complete
GGUF headers, ending before tensor payloads; no complete weight-file hash or
numeric equivalence has been checked locally.

| Candidate container | Exact bytes | Header-derived tensor formats |
| --- | ---: | --- |
| PTQ1_0 | 5,946,648,928 | 402 PTQ1_0, 353 F32, 96 BF16 tensors |
| PQ2_0 | 7,206,168,928 | 402 PQ2_0, 353 F32, 96 BF16 tensors |

Each language checkpoint contains 26,895,998,464 tensor elements. GGUF container
size includes metadata and alignment; it is not a final repacked HBM image or
peak runtime residency estimate. Optional vision projection files are separate
and have not been selected for the language workload.

## Format and arithmetic decisions

The pinned official runtime [block definitions](https://github.com/PrismML-Eng/llama.cpp/blob/adfffbe41b2cabcd51fff326ab045662265062bb/ggml/src/ggml-common.h#L199-L220)
use 128 values per group for both formats:

- **PQ2_0:** 34 bytes, comprising an FP16 scale followed by 32 packed bytes.
  Its [decoder](https://github.com/PrismML-Eng/llama.cpp/blob/adfffbe41b2cabcd51fff326ab045662265062bb/ggml/src/ggml-quants.c#L494-L512)
  maps codes `00/01/10/11` to `-1/0/+1/+2`. This differs from the current generic
  ternary fixture. Header inspection does not prove that payload code `11`
  never occurs. Scan the selected full payload and specify rejection or +2
  arithmetic before committing to ternary-only PEs for this format.
- **PTQ1_0:** 28 bytes, with 24 main packed-trit bytes, two remainder bytes and
  a trailing FP16 scale. Preserve the official
  [interleaved decoder order](https://github.com/PrismML-Eng/llama.cpp/blob/adfffbe41b2cabcd51fff326ab045662265062bb/ggml/src/ggml-quants.c#L2255-L2285).
  It is not the existing two-bit fixture and needs its own unpacking/line-crossing
  tests and format ID.

The proposed array's exact group-dot contract can remain a diagnostic boundary,
but model execution needs more than packed dot products. Candidate metadata
specifies a normalized Sylvester Walsh-Hadamard transform in 1024-element blocks
on the last input dimension, explicit signs, 401 forward weight mappings and one
inverse embedding mapping, including grouped GDN handling. Freeze transform
placement relative to activation quantization, sign ordering, normalization,
rounding and inverse embedding behavior against the pinned official runtime.
Do not assume the current seeded int8 activations represent that contract.

## CoralNPU integration and writable state

The candidate uses Qwen3.5 hybrid attention: 64 layers, comprising 48 linear
attention layers and 16 full-attention layers. Norms and recurrent helpers also
consume F32/BF16 tensors. The [official runtime graph](https://github.com/PrismML-Eng/llama.cpp/blob/adfffbe41b2cabcd51fff326ab045662265062bb/src/models/qwen35.cpp)
is the reference for the helper inventory. Plan recurrent/convolution state,
gating, normalization, attention, embeddings and transform support alongside the
array and CoralNPU firmware; ordinary dense-attention KV storage alone is
insufficient. No helper implementation or cycle estimate is established here.

For one sequence with separate dense F16 K and V, all 16 full-attention layers,
four KV heads and 256 elements per head, the conditional KV allocation is:

`16 × 2 × 4 × 256 × 2 = 65,536 bytes/token`.

That gives 512 MiB at 8,192 tokens and 16 GiB at the advertised 262,144-token
context, before weights, linear-attention state, activations, scratch, allocator
overhead or optional vision tensors. These are arithmetic estimates, not measured
allocations. The maximum-context dense-F16 assumption plus weights cannot fit in
16 GiB of HBM. Choose and validate a supported context, cache format or placement
policy before a fit claim. Keep writable state outside the sealed weight region.

## Acceptance before implementation

1. Select the immutable model file, verify the full payload hash and enumerate
   actual formats/codes, tensor aliases, scales and transform metadata.
2. Freeze separate native format descriptors and independent unpacking/numeric
   references. Approve activation/transform precision and model-level tolerances.
3. Assign the CoralNPU/array/helper boundary and specify the new helper and state
   lifetimes. The existing DOT128 mailbox remains versioned and unchanged.
4. Complete the [large-image addressing and residency gate](weightstore.md#27b-capacity-and-addressability-gate),
   including a new ABI beyond 256 MiB and whole-image sealing/readback across banks.
5. Validate operator and layer outputs before inference or throughput claims.
   Future implementation issues require a PR, runnable simulation, actual FPGA
   evidence for their stated scope and an attached report.

The [model card](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/blob/b072e1d3b35a0a630cece372c2127528e0994386/README.md)
and the [official runtime fork](https://github.com/PrismML-Eng/llama.cpp/tree/adfffbe41b2cabcd51fff326ab045662265062bb)
are pinned references. Their existence is not evidence that this repository has
run the model.
