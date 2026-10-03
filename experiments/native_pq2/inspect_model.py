#!/usr/bin/env python3
"""Inspect native PQ2 GGUF bytes without exporting tensor or metadata values.

Run on the allocated remote worker, for example:
  python3 inspect_model.py model.gguf --output model-manifest.json

GGUF v2/v3 little-endian only. Unknown tensor types remain opaque spans. Only
Prism PQ2_0 (142) is decoded; Q2_0 (42) and PTQ1_0 (143) are never substituted.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
from typing import BinaryIO

# Verified against these exact Prism sources, not the model's general.file_type.
PRISM_REVISION = "bdc23b56b4458b9f1655aec5287f3ab56ee8daaa"
PRISM_SOURCE = "https://github.com/PrismML-Eng/llama.cpp"
SOURCE_SHA256 = {
    "ggml/include/ggml.h":
    "9a865c28b0fea8d0adaa3218ac3514bfcef3eb2f35f8e7c67bc7fac90586f5e6",
    "ggml/src/ggml-common.h":
    "96539c9bd8e4544692bf242951180edaa2fd5775864fbc3e833f44111b96b934",
    "ggml/src/ggml-quants.c":
    "46dd353f30e7f1c42c7d82d88d91fad64dbe5d6e680b234de25355d9fb482d13",
    "gguf-py/gguf/constants.py":
    "df3d445a98319123be34db85b946eadce792d0912c417ace8e2c64dbba6a4123",
}
PQ2_TYPE = 142
BLOCK_ELEMENTS = 128
BLOCK_BYTES = 34
TYPE_LAYOUTS = {
    0: ("F32", 1, 4),
    1: ("F16", 1, 2),
    PQ2_TYPE: ("PQ2_0", BLOCK_ELEMENTS, BLOCK_BYTES)
}
META_FORMATS = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "f",
    7: "?",
    10: "Q",
    11: "q",
    12: "d"
}
META_NAMES = {
    0: "UINT8",
    1: "INT8",
    2: "UINT16",
    3: "INT16",
    4: "UINT32",
    5: "INT32",
    6: "FLOAT32",
    7: "BOOL",
    8: "STRING",
    9: "ARRAY",
    10: "UINT64",
    11: "INT64",
    12: "FLOAT64"
}


class InvalidGGUF(ValueError):
    """The input does not satisfy a supported storage contract."""


class Reader:

    def __init__(self, stream: BinaryIO, size: int):
        self.stream = stream
        self.size = size

    def remaining(self) -> int:
        return self.size - self.stream.tell()

    def read(self, count: int) -> bytes:
        if count < 0 or count > self.remaining():
            raise InvalidGGUF("field extends beyond end of file")
        data = self.stream.read(count)
        if len(data) != count:
            raise InvalidGGUF("short read")
        return data

    def skip(self, count: int) -> None:
        if count < 0 or count > self.remaining():
            raise InvalidGGUF("field extends beyond end of file")
        self.stream.seek(count, os.SEEK_CUR)

    def number(self, fmt: str) -> int:
        return struct.unpack("<" + fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self, keep: bool = False) -> str | None:
        length = self.number("Q")
        if not keep:
            self.skip(length)
            return None
        if length > 1024 * 1024:
            raise InvalidGGUF("metadata key or tensor name exceeds 1 MiB")
        try:
            return self.read(length).decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvalidGGUF("invalid UTF-8 key or tensor name") from error

    def metadata_value(self, value_type: int, keep: bool = False):
        if value_type not in META_NAMES:
            raise InvalidGGUF(f"unsupported metadata type {value_type}")
        descriptor = {"type": META_NAMES[value_type]}
        if value_type in META_FORMATS:
            fmt = META_FORMATS[value_type]
            if keep:
                return descriptor, self.number(fmt)
            self.skip(struct.calcsize(fmt))
        elif value_type == 8:
            self.string()
        else:
            element_type, count = self.number("I"), self.number("Q")
            if element_type not in META_NAMES or element_type == 9:
                raise InvalidGGUF("unsupported or nested metadata array")
            descriptor.update(
                element_type=META_NAMES[element_type], count=count
            )
            if element_type == 8:
                if count > self.remaining() // 8:
                    raise InvalidGGUF(
                        "string array length exceeds file bounds"
                    )
                for _ in range(count):
                    self.string()
            else:
                self.skip(count * struct.calcsize(META_FORMATS[element_type]))
        return descriptor, None


def read_manifest(stream: BinaryIO, size: int) -> dict:
    reader = Reader(stream, size)
    if reader.read(4) != b"GGUF":
        raise InvalidGGUF("expected little-endian GGUF magic")
    version = reader.number("I")
    if version not in (2, 3):
        raise InvalidGGUF(
            f"unsupported GGUF version {version}; expected 2 or 3"
        )
    tensor_count, metadata_count = reader.number("Q"), reader.number("Q")
    if metadata_count > reader.remaining() // 13:
        raise InvalidGGUF("metadata count exceeds file bounds")
    metadata, keys, alignment = [], set(), 32
    for _ in range(metadata_count):
        key, value_type = reader.string(keep=True), reader.number("I")
        if key in keys:
            raise InvalidGGUF("duplicate metadata key")
        keys.add(key)
        if key == "general.alignment" and value_type != 4:
            raise InvalidGGUF("general.alignment must be UINT32")
        descriptor, value = reader.metadata_value(
            value_type, key == "general.alignment"
        )
        metadata.append({"key": key, **descriptor})
        if key == "general.alignment":
            alignment = value
    if alignment < 1 or alignment & (alignment - 1):
        raise InvalidGGUF("alignment must be a positive power of two")
    if tensor_count > reader.remaining() // 32:
        raise InvalidGGUF("tensor count exceeds file bounds")
    tensors, names = [], set()
    for index in range(tensor_count):
        name, dimensions = reader.string(keep=True), reader.number("I")
        if name in names or not 1 <= dimensions <= 4:
            raise InvalidGGUF(
                "duplicate tensor name or unsupported dimension count"
            )
        names.add(name)
        shape = [reader.number("Q") for _ in range(dimensions)]
        if not all(shape):
            raise InvalidGGUF("zero-sized tensor dimension")
        type_id, offset = reader.number("I"), reader.number("Q")
        if offset % alignment:
            raise InvalidGGUF(
                f"unaligned tensor offset at table index {index}"
            )
        tensors.append({
            "table_index": index,
            "name": name,
            "shape_ggml": shape,
            "type_id": type_id,
            "relative_offset": offset,
            "elements": math.prod(shape)
        })
    table_end = stream.tell()
    data_start = (table_end + alignment - 1) // alignment * alignment
    if data_start > size:
        raise InvalidGGUF("tensor data section is outside the file")
    ordered = sorted(tensors, key=lambda tensor: tensor["relative_offset"])
    for index, tensor in enumerate(ordered):
        start = data_start + tensor["relative_offset"]
        boundary = (
            data_start + ordered[index + 1]["relative_offset"] if index +
            1 < len(ordered) else size
        )
        if not data_start <= start < boundary <= size:
            raise InvalidGGUF(
                "duplicate, reversed or out-of-file tensor boundary"
            )
        tensor.update(
            absolute_offset=start,
            next_boundary_exclusive=boundary,
            storage_span_bytes=boundary - start
        )
        layout = TYPE_LAYOUTS.get(tensor["type_id"])
        if layout is None:
            tensor.update(
                type_name="unsupported",
                size_verified=False,
                payload_bytes=None,
                payload_end_exclusive=None
            )
            continue
        name, block_elements, block_bytes = layout
        if tensor["shape_ggml"][0] % block_elements:
            raise InvalidGGUF(
                f"tensor {tensor['table_index']} row width violates {name} block size"
            )
        payload = tensor["elements"] // block_elements * block_bytes
        if start + payload > boundary:
            raise InvalidGGUF(
                f"tensor {tensor['table_index']} payload overlaps its boundary"
            )
        tensor.update(
            type_name=name,
            size_verified=True,
            block_elements=block_elements,
            block_bytes=block_bytes,
            payload_bytes=payload,
            payload_end_exclusive=start + payload,
            trailing_span_bytes=boundary - start - payload,
            row_elements=tensor["shape_ggml"][0],
            row_bytes=tensor["shape_ggml"][0] // block_elements * block_bytes,
            rows=math.prod(tensor["shape_ggml"][1:])
        )
    return {
        "gguf_version": version,
        "byte_order": "little",
        "alignment": alignment,
        "metadata_count": metadata_count,
        "metadata_schema": metadata,
        "tensor_count": tensor_count,
        "tensor_table_end": table_end,
        "tensor_data_start": data_start,
        "tensors": tensors
    }


def pq2_histogram(stream: BinaryIO, tensor: dict,
                  chunk_blocks: int) -> list[int]:
    stream.seek(tensor["absolute_offset"])
    remaining, byte_counts = tensor["payload_bytes"], Counter()
    while remaining:
        count = min(remaining, chunk_blocks * BLOCK_BYTES)
        data = stream.read(count)
        if len(data) != count:
            raise InvalidGGUF("short PQ2 payload read")
        # Count packed bytes in C, then remove the two unmodified FP16 scale bytes.
        byte_counts.update(data)
        byte_counts.subtract(data[0::BLOCK_BYTES])
        byte_counts.subtract(data[1::BLOCK_BYTES])
        remaining -= count
    codes = [0] * 4
    for packed, count in byte_counts.items():
        for shift in (0, 2, 4, 6):
            codes[(packed >> shift) & 3] += count
    if sum(codes) != tensor["elements"] or min(codes) < 0:
        raise InvalidGGUF("PQ2 histogram does not cover the declared tensor")
    return codes


def identity(stat) -> tuple:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def inspect(path: Path, chunk_blocks: int = 32768) -> dict:
    if chunk_blocks < 1:
        raise ValueError("chunk_blocks must be positive")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        manifest = read_manifest(stream, before.st_size)
        total, blocks, tensor_count = [0] * 4, 0, 0
        for tensor in manifest["tensors"]:
            if tensor["type_id"] != PQ2_TYPE:
                continue
            counts = pq2_histogram(stream, tensor, chunk_blocks)
            tensor_blocks = tensor["elements"] // BLOCK_ELEMENTS
            tensor.update(
                code_counts=counts,
                raw_fp16_scale_count=tensor_blocks,
                raw_fp16_scale_bytes=tensor_blocks * 2
            )
            total = [old + new for old, new in zip(total, counts)]
            blocks += tensor_blocks
            tensor_count += 1
        if tensor_count == 0:
            raise InvalidGGUF(
                "no supported native PQ2_0 (type 142) tensors found"
            )
        stream.seek(0)
        digest = hashlib.sha256()
        while True:
            data = stream.read(8 * 1024 * 1024)
            if not data:
                break
            digest.update(data)
        if identity(before) != identity(os.fstat(stream.fileno())):
            raise InvalidGGUF(
                "input changed during inspection; discard this run"
            )
    return {
        "schema":
        "bonsai.native-pq2.manifest.v1",
        "file": {
            "name": path.name,
            "bytes": before.st_size,
            "sha256": digest.hexdigest()
        },
        "inspector_sha256":
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "contract": {
            "repository": PRISM_SOURCE,
            "revision": PRISM_REVISION,
            "source_sha256": SOURCE_SHA256,
            "ggml_type": PQ2_TYPE,
            "block_elements": BLOCK_ELEMENTS,
            "block_bytes": BLOCK_BYTES,
            "scale": "raw FP16, first 2 bytes of each block",
            "codes": "four successive low-to-high 2-bit codes per packed byte",
            "decoded_values_by_code": [-1, 0, 1, 2]
        },
        "aggregate": {
            "pq2_tensor_count": tensor_count,
            "pq2_elements": sum(total),
            "pq2_payload_bytes": blocks * BLOCK_BYTES,
            "code_counts": total,
            "raw_fp16_scale_count": blocks,
            "raw_fp16_scale_bytes": blocks * 2,
            "zero_fraction": total[1] / sum(total),
            "plus_two_count": total[3]
        },
        **manifest,
        "unsupported_tensor_types":
        sorted({
            tensor["type_id"]
            for tensor in manifest["tensors"]
            if not tensor["size_verified"]
        }),
        "limitations": [
            "Only type 142 payloads are decoded; other values are not read for analysis.",
            "Unsupported tensor sizes are unverified; their spans end at the next offset or EOF.",
            "Histogram counts codes before scaling, including blocks with zero scales.",
            "No scale values, tensor values, or metadata values are exported."
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument(
        "--output", type=Path, help="write JSON here; default: stdout"
    )
    parser.add_argument("--chunk-blocks", type=int, default=32768)
    args = parser.parse_args()
    try:
        if args.output and (args.output.resolve() == args.model.resolve() or
                            (args.output.exists()
                             and os.path.samefile(args.output, args.model))):
            raise ValueError("output must not be the input checkpoint")
        result = inspect(args.model, args.chunk_blocks)
        encoded = json.dumps(
            result, indent=2, sort_keys=True, allow_nan=False
        ) + "\n"
        if args.output:
            args.output.write_text(encoded, encoding="utf-8")
        else:
            sys.stdout.write(encoded)
    except (OSError, ValueError, struct.error) as error:
        print(f"inspect_model: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
