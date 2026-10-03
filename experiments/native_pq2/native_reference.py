#!/usr/bin/env python3
"""Independent native PQ2 integer oracle and reproducible fixture writer.

Only the Python standard library is required. A model is never needed for the
synthetic suite; optional GGUF bytes stay in the private generated fixture.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import struct

MAGIC = b"PQ2FX002"
ROM_BYTES = 1024 * 1024
BLOCK_BYTES = 34
BLOCK_LANES = 128


def subgroup_dot(block, activation):
    """Four exact INT32 subgroup results; code 3 is +2, never remapped."""
    if len(block) != BLOCK_BYTES or len(activation) != BLOCK_LANES:
        raise ValueError(
            "Expected one 34-byte PQ2 block and 128 INT8 operands"
        )
    if any(not -128 <= value <= 127 for value in activation):
        raise ValueError("Activation outside signed INT8")
    decoded = [((block[2 + lane // 4] >> (2 * (lane % 4))) & 3) - 1
               for lane in range(BLOCK_LANES)]
    return [
        sum(
            decoded[lane] * activation[lane]
            for lane in range(group * 32, (group + 1) * 32)
        )
        for group in range(4)
    ]


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def ordered_epilogue(blocks, activations, activation_scale_bits):
    """Native scalar order; consecutive DOT128 blocks share a Q8_K FP32 d.

    No FMA: round scale product, scaled dot, and ordered accumulator to FP32.
    The engine harness validates integer results and raw scale bits; this
    reference helper does not claim a full firmware/model inference check.
    """
    if len(blocks) != len(activations) or len(activation_scale_bits
                                              ) != (len(blocks) + 1) // 2:
        raise ValueError(
            "One Q8_K scale is required per pair of DOT128 blocks"
        )
    total = f32(0.0)
    for index, (block, activation) in enumerate(zip(blocks, activations)):
        weight_scale = struct.unpack("<e", block[:2])[0]
        activation_scale = struct.unpack(
            "<f", struct.pack("<I", activation_scale_bits[index // 2])
        )[0]
        dot = sum(subgroup_dot(block, activation))
        total = f32(total + f32(f32(weight_scale * activation_scale) * dot))
    return total


def _read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Truncated GGUF")
    return data


def _integer(stream, fmt):
    return struct.unpack(fmt, _read_exact(stream, struct.calcsize(fmt)))[0]


def _string(stream):
    length = _integer(stream, "<Q")
    if length > 64 * 1024 * 1024:
        raise ValueError("Unreasonably large GGUF string")
    return _read_exact(stream, length).decode("utf-8")


def _metadata(stream, kind, retain=False, depth=0):
    if depth > 4:
        raise ValueError("Nested GGUF metadata exceeds supported depth")
    formats = {
        0: "<B",
        1: "<b",
        2: "<H",
        3: "<h",
        4: "<I",
        5: "<i",
        6: "<f",
        7: "<?",
        10: "<Q",
        11: "<q",
        12: "<d"
    }
    if kind in formats:
        return _integer(stream, formats[kind])
    if kind == 8:
        return _string(stream)
    if kind == 9:
        element_kind, count = _integer(stream, "<I"), _integer(stream, "<Q")
        if count > 10_000_000:
            raise ValueError("Unreasonably large GGUF array")
        if element_kind in formats and not retain:
            size = count * struct.calcsize(formats[element_kind])
            # seek alone would miss a truncated metadata array.
            _read_exact(stream, size)
            return None
        values = [] if retain else None
        for _ in range(count):
            value = _metadata(stream, element_kind, retain, depth + 1)
            if retain:
                values.append(value)
        return values
    raise ValueError(f"Unsupported GGUF metadata type {kind}")


def inspect_gguf(path, absolute_offset, pq2_type_id):
    """Validate an explicitly identified native PQ2 tensor using GGUF metadata.

    GGML extension IDs are toolchain-dependent: the caller must supply the
    PQ2_0 ID from the pinned source. Never guess it from packed bytes.
    """
    with Path(path).open("rb") as stream:
        if _read_exact(stream, 4) != b"GGUF":
            raise ValueError(
                "Only little-endian GGUF model files are supported"
            )
        version = _integer(stream, "<I")
        if version not in (2, 3):
            raise ValueError("Expected GGUF version 2 or 3")
        tensors, metadata_count = _integer(stream,
                                           "<Q"), _integer(stream, "<Q")
        if tensors > 1_000_000 or metadata_count > 1_000_000:
            raise ValueError("Unreasonably large GGUF directory")
        alignment = 32
        for _ in range(metadata_count):
            key, kind = _string(stream), _integer(stream, "<I")
            value = _metadata(stream, kind, key == "general.alignment")
            if key == "general.alignment":
                alignment = value
        descriptors = []
        for _ in range(tensors):
            name, dimensions = _string(stream), _integer(stream, "<I")
            if not 1 <= dimensions <= 4:
                raise ValueError("Unexpected GGUF tensor rank")
            shape = [_integer(stream, "<Q") for _ in range(dimensions)]
            type_id, offset = _integer(stream, "<I"), _integer(stream, "<Q")
            descriptors.append((name, shape, type_id, offset))
        if not isinstance(alignment,
                          int) or alignment < 1 or alignment > 1048576:
            raise ValueError("Invalid GGUF alignment")
        data_start = ((stream.tell() + alignment - 1) // alignment) * alignment
        size = Path(path).stat().st_size
        for name, shape, type_id, offset in descriptors:
            if type_id != pq2_type_id:
                continue
            if any(dimension == 0
                   for dimension in shape) or shape[0] % BLOCK_LANES:
                raise ValueError(
                    "PQ2 tensor dimensions do not contain complete DOT128 blocks"
                )
            start = data_start + offset
            length = math.prod(shape) // BLOCK_LANES * BLOCK_BYTES
            if start + length > size:
                raise ValueError("PQ2 descriptor exceeds file size")
            if start <= absolute_offset < start + length:
                if (absolute_offset - start) % BLOCK_BYTES:
                    raise ValueError(
                        "--offset must identify a native 34-byte block boundary"
                    )
                return {
                    "tensor": name,
                    "tensor_type_id": type_id,
                    "tensor_shape_gguf_order": shape,
                    "offset": absolute_offset,
                    "tensor_start": start,
                    "tensor_end": start + length,
                    "gguf_version": version,
                    "type_validation":
                    "explicit_pinned_type_id_matches_metadata"
                }
    raise ValueError(
        "--offset is not in a tensor with the explicitly supplied PQ2 type ID"
    )


def make_cases(
    seed=7193,
    random_cases=32,
    model=None,
    offset=None,
    pq2_type_id=None,
    model_cases=16
):
    rng = random.Random(seed)
    cases = []
    scale_patterns = [
        0x0000, 0x8000, 0x3C00, 0xBC00, 0x0001, 0x7BFF, 0x7C00, 0x7E01, 0x3555
    ]
    activation_scales = [
        0x00000000, 0x80000000, 0x3C800000, 0xBC800000, 0x3F800000, 0x7FC00001
    ]

    def create(name, units, alignment, stride, pattern, original_blocks=None):
        if pattern == 0:
            activation = [-128] * 128
        elif pattern == 1:
            activation = [127] * 128
        elif pattern == 2:
            activation = [-128, 127, -1, 0] * 32
        elif pattern == 3:
            activation = list(range(-128, 0))
        else:
            activation = [rng.randrange(-128, 128) for _ in range(128)]
            activation[:4] = [-128, 127, -1, 0]
        blocks = []
        for unit in range(units):
            if original_blocks is not None:
                block = original_blocks[unit]
            else:
                scale = scale_patterns[(len(cases) + unit) %
                                       len(scale_patterns)]
                codes = ([pattern] * 128 if pattern < 4 else [
                    (lane + unit) % 4 for lane in range(128)
                ] if pattern == 4 else [rng.randrange(4) for _ in range(128)])
                block = struct.pack("<H", scale) + bytes(
                    sum(codes[lane + sub] << (2 * sub)
                        for sub in range(4))
                    for lane in range(0, 128, 4)
                )
            blocks.append({
                "address": 4096 + alignment + unit * stride,
                "bytes": block,
                "expected": subgroup_dot(block, activation)
            })
        cases.append({
            "name":
            name,
            "units":
            units,
            "alignment":
            alignment,
            "stride":
            stride,
            "activation_scale_bits":
            activation_scales[len(cases) % len(activation_scales)],
            "activation":
            activation,
            "blocks":
            blocks
        })

    for alignment in range(16):
        for units in (1, 7, 21, 31, 32):
            for stride in (34, 544):
                create(
                    f"directed_a{alignment:02}_u{units:02}_s{stride}", units,
                    alignment, stride,
                    (alignment + units + (stride == 544)) % 6
                )
    for index in range(random_cases):
        create(
            f"random_{index:03}", rng.choice((1, 7, 21, 31, 32)),
            rng.randrange(16), rng.choice((34, 544, 1088, 1632)), 5
        )
    model_info = None
    if model is not None:
        if offset is None or pq2_type_id is None:
            raise ValueError(
                "--model requires explicit --offset and --pq2-type-id"
            )
        model_info = inspect_gguf(model, offset, pq2_type_id)
        row_bytes = model_info["tensor_shape_gguf_order"][
            0] // BLOCK_LANES * BLOCK_BYTES
        if (offset - model_info["tensor_start"]
            ) % row_bytes + model_cases * BLOCK_BYTES > row_bytes:
            raise ValueError(
                "Requested model cases cross a native row boundary"
            )
        needed = 31 * row_bytes + model_cases * BLOCK_BYTES
        if offset + needed > model_info["tensor_end"]:
            raise ValueError(
                "Requested model fixture crosses the validated tensor boundary"
            )
        with Path(model).open("rb") as stream:
            for index in range(model_cases):
                blocks = []
                for unit in range(32):
                    stream.seek(
                        offset + unit * row_bytes + index * BLOCK_BYTES
                    )
                    blocks.append(_read_exact(stream, BLOCK_BYTES))
                create(
                    f"model_{index:03}", 32, index % 16, row_bytes, 5, blocks
                )
        with Path(model).open("rb") as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        model_info.update(
            sha256=digest.hexdigest(),
            bytes=Path(model).stat().st_size,
            sampled_blocks=model_cases * 32,
            sampled_rows=32,
            native_row_bytes=row_bytes,
            sampling=
            "same K block across 32 consecutive native rows; unmodified bytes relocated into test ROM",
            activations="seeded synthetic INT8 operands and FP32 scale bits"
        )
    return cases, model_info


def write_fixtures(path, **kwargs):
    cases, model_info = make_cases(**kwargs)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(MAGIC + struct.pack("<II", ROM_BYTES, len(cases)))
        for case in cases:
            name = case["name"].encode("ascii")
            stream.write(struct.pack("<I", len(name)) + name)
            stream.write(
                struct.pack(
                    "<4I", case["units"], case["alignment"], case["stride"],
                    case["activation_scale_bits"]
                )
            )
            stream.write(struct.pack("<128b", *case["activation"]))
            for block in case["blocks"]:
                stream.write(
                    struct.pack("<I", block["address"]) + block["bytes"]
                )
                stream.write(struct.pack("<4i", *block["expected"]))
    code_counts = [0] * 4
    for case in cases:
        for block in case["blocks"]:
            for byte in block["bytes"][2:]:
                for shift in (0, 2, 4, 6):
                    code_counts[(byte >> shift) & 3] += 1
    manifest = {
        "schema":
        2,
        "seed":
        kwargs.get("seed", 7193),
        "cases":
        len(cases),
        "rom_bytes":
        ROM_BYTES,
        "fixture_sha256":
        hashlib.sha256(path.read_bytes()).hexdigest(),
        "code_counts_minus1_zero_plus1_plus2":
        code_counts,
        "unit_counts":
        sorted({case["units"]
                for case in cases}),
        "alignments":
        sorted({case["alignment"]
                for case in cases}),
        "strides":
        sorted({case["stride"]
                for case in cases}),
        "model":
        model_info,
        "oracle":
        "independent Python decode(code)-1; exact four INT32 sums; raw FP16/FP32 scale bits",
        "scope":
        "DOT128 engine only; no full token, board, timing, or FP32 epilogue execution claim"
    }
    path.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=7193)
    parser.add_argument("--random-cases", type=int, default=32)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--offset", type=lambda value: int(value, 0))
    parser.add_argument("--pq2-type-id", type=int)
    parser.add_argument("--model-cases", type=int, default=16)
    args = vars(parser.parse_args())
    output = args.pop("output")
    if args["random_cases"] < 0 or args["model_cases"] < 1:
        parser.error(
            "Case counts must be nonnegative; model-cases must be positive"
        )
    print(json.dumps(write_fixtures(output, **args), indent=2))


if __name__ == "__main__":
    main()
