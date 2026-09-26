"""Load the pinned Bonsai Q1_0 GGUF into a CPU FP32 Qwen3 reference.

No alternate checkpoint, requantization, or synthetic weights are accepted.
"""
import hashlib
from collections import Counter
from pathlib import Path

import numpy as np

MODEL_ID = "prism-ml/Bonsai-1.7B-gguf"
REVISION = "210a9e99f79cb184909d49595906526eb2b3dd9a"
MODEL_SHA256 = "3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3"
MODEL_BYTES = 248302272
LINEAR_COUNT = 197


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_checkpoint(path):
    from gguf import GGUFReader
    path = Path(path)
    if path.stat().st_size != MODEL_BYTES or sha256(path) != MODEL_SHA256:
        raise ValueError(
            "Checkpoint size/SHA256 differs from the pinned public Q1_0 model"
        )
    reader = GGUFReader(path)
    if reader.byte_order != "I" or not np.little_endian:
        raise ValueError("This adapter requires little-endian GGUF and host")
    metadata = {key: value.contents() for key, value in reader.fields.items()}
    expected = {
        "general.architecture": "qwen3",
        "general.file_type": 40,
        "qwen3.block_count": 28,
        "qwen3.context_length": 32768,
        "qwen3.embedding_length": 2048,
        "qwen3.feed_forward_length": 6144,
        "qwen3.attention.head_count": 16,
        "qwen3.attention.head_count_kv": 8,
        "qwen3.attention.key_length": 128,
        "qwen3.attention.value_length": 128,
        "qwen3.rope.scaling.type": "yarn",
        "qwen3.rope.scaling.factor": 4.0,
        "qwen3.rope.scaling.original_context_length": 8192,
        "qwen3.rope.freq_base": 1000000.0,
        "tokenizer.ggml.model": "gpt2",
        "tokenizer.ggml.pre": "qwen2",
        "tokenizer.ggml.add_bos_token": False,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(
                f"Unsupported GGUF metadata: {key}={metadata.get(key)!r}"
            )
    if len(metadata["tokenizer.ggml.tokens"]) != 151669:
        raise ValueError("The exact checkpoint has 151669 vocabulary entries")
    if Counter(int(t.tensor_type) for t in reader.tensors) != {41: 197, 0:
                                                               113}:
        raise ValueError("Expected 197 Q1_0 matrices and 113 FP32 vectors")
    return reader, metadata


def decode_q1(blocks):
    """Decode original 18-byte g128 blocks; LSB first, 0=-scale, 1=+scale."""
    blocks = np.asarray(blocks, dtype=np.uint8).reshape(-1, 18)
    scales = blocks[:, :2].copy().view("<f2").reshape(-1).astype(np.float32)
    if not np.isfinite(scales).all():
        raise ValueError("Non-finite Q1_0 scales")
    bits = np.unpackbits(blocks[:, 2:], axis=1, bitorder="little")
    signs = bits.astype(np.float32) * 2.0 - 1.0
    return signs * scales[:, None]


def decode_tensor(tensor):
    shape = tuple(int(n) for n in tensor.shape[::-1])
    if int(tensor.tensor_type) == 0:
        result = np.array(
            tensor.data, dtype=np.float32, copy=True
        ).reshape(shape)
    elif int(tensor.tensor_type
             ) == 41 and len(shape) == 2 and shape[1] % 128 == 0:
        blocks = tensor.data.reshape(-1, 18)
        result = np.empty(int(np.prod(shape)), dtype=np.float32)
        # Bound transient unpacking memory even for the tied embedding/head.
        for start in range(0, len(blocks), 65536):
            end = min(start + 65536, len(blocks))
            result[start * 128:end * 128] = decode_q1(blocks[start:end]
                                                      ).reshape(-1)
        result = result.reshape(shape)
    else:
        raise ValueError(
            f"Unsupported tensor: {tensor.name}, type={tensor.tensor_type}, shape={shape}"
        )
    if not np.isfinite(result).all():
        raise ValueError(f"Non-finite tensor: {tensor.name}")
    return result


def tensor_mapping():
    mapping = {
        "token_embd.weight": "model.embed_tokens.weight",
        "output_norm.weight": "model.norm.weight"
    }
    names = {
        "attn_q": "self_attn.q_proj",
        "attn_k": "self_attn.k_proj",
        "attn_v": "self_attn.v_proj",
        "attn_output": "self_attn.o_proj",
        "attn_q_norm": "self_attn.q_norm",
        "attn_k_norm": "self_attn.k_norm",
        "attn_norm": "input_layernorm",
        "ffn_norm": "post_attention_layernorm",
        "ffn_gate": "mlp.gate_proj",
        "ffn_up": "mlp.up_proj",
        "ffn_down": "mlp.down_proj",
    }
    for layer in range(28):
        for source, destination in names.items():
            mapping[f"blk.{layer}.{source}.weight"
                    ] = f"model.layers.{layer}.{destination}.weight"
    return mapping


def build_tokenizer(metadata):
    from tokenizers import AddedToken
    from transformers import Qwen2Tokenizer
    tokens = metadata["tokenizer.ggml.tokens"]
    types = metadata["tokenizer.ggml.token_type"]
    if len(set(tokens)) != len(tokens) or len(types) != len(tokens):
        raise ValueError("Duplicate tokens or mismatched token type inventory")
    merges = [
        tuple(merge.split(" ")) for merge in metadata["tokenizer.ggml.merges"]
    ]
    if any(len(pair) != 2 for pair in merges):
        raise ValueError("Invalid GGUF BPE merge")
    specials = [
        AddedToken(token, normalized=False, special=True)
        for token, kind in zip(tokens, types)
        if kind in (3, 4)
    ]
    tokenizer = Qwen2Tokenizer(
        vocab={
            token: index
            for index, token in enumerate(tokens)
        },
        merges=merges,
        eos_token=tokens[metadata["tokenizer.ggml.eos_token_id"]],
        pad_token=tokens[metadata["tokenizer.ggml.padding_token_id"]],
        bos_token=None,
        unk_token=None,
        additional_special_tokens=specials,
        chat_template=metadata["tokenizer.chat_template"],
    )
    if len(tokenizer) != len(tokens):
        raise ValueError("Tokenizer conversion changed vocabulary size")
    if any(tokenizer.convert_tokens_to_ids(token) != index
           for index, token in enumerate(tokens)):
        raise ValueError("Tokenizer conversion changed token IDs")
    return tokenizer


def load_model(path, threads=4):
    import torch
    from transformers import Qwen3Config, Qwen3ForCausalLM
    torch.set_num_threads(threads)
    torch.manual_seed(0)
    reader, metadata = read_checkpoint(path)
    mapping = tensor_mapping()
    if {t.name for t in reader.tensors} != set(mapping):
        raise ValueError("Missing or unexpected tensor names")
    tokenizer = build_tokenizer(metadata)
    config = Qwen3Config(
        vocab_size=len(tokenizer),
        hidden_size=metadata["qwen3.embedding_length"],
        intermediate_size=metadata["qwen3.feed_forward_length"],
        num_hidden_layers=metadata["qwen3.block_count"],
        num_attention_heads=metadata["qwen3.attention.head_count"],
        num_key_value_heads=metadata["qwen3.attention.head_count_kv"],
        head_dim=metadata["qwen3.attention.key_length"],
        hidden_act="silu",
        max_position_embeddings=metadata["qwen3.context_length"],
        rms_norm_eps=metadata["qwen3.attention.layer_norm_rms_epsilon"],
        tie_word_embeddings=True,
        attention_bias=False,
        use_sliding_window=False,
        rope_parameters={
            "rope_type":
            "yarn",
            "rope_theta":
            metadata["qwen3.rope.freq_base"],
            "factor":
            metadata["qwen3.rope.scaling.factor"],
            "original_max_position_embeddings":
            metadata["qwen3.rope.scaling.original_context_length"]
        },
        eos_token_id=metadata["tokenizer.ggml.eos_token_id"],
        pad_token_id=metadata["tokenizer.ggml.padding_token_id"],
    )
    config._attn_implementation = "eager"
    # CPU construction materializes nonpersistent RoPE buffers as well as weights.
    model = Qwen3ForCausalLM(config).float().eval()
    state = {
        mapping[t.name]: torch.from_numpy(decode_tensor(t))
        for t in reader.tensors
    }
    state["lm_head.weight"] = state["model.embed_tokens.weight"]
    model.load_state_dict(state, strict=True, assign=True)
    if model.lm_head.weight.data_ptr(
    ) != model.model.embed_tokens.weight.data_ptr():
        raise ValueError("Tied embedding/head weights were not preserved")
    linears = {
        name: list(module.weight.shape)
        for name, module in model.named_modules()
        if isinstance(module, torch.nn.Linear)
    }
    if len(linears) != LINEAR_COUNT:
        raise ValueError(
            f"Expected {LINEAR_COUNT} linear projections, found {len(linears)}"
        )
    provenance = {
        "model":
        MODEL_ID,
        "revision":
        REVISION,
        "gguf_sha256":
        MODEL_SHA256,
        "gguf_bytes":
        MODEL_BYTES,
        "vocab_size":
        len(tokenizer),
        "original_tensor_types": {
            "Q1_0": 197,
            "F32": 113
        },
        "arithmetic":
        "exact Q1_0 weight dequantization to FP32; FP32 activations/accumulation",
        "config":
        config.to_dict(),
        "linear_shapes":
        linears,
        "chat_template_sha256":
        hashlib.sha256(metadata["tokenizer.chat_template"].encode()
                       ).hexdigest()
    }
    return model, tokenizer, provenance
