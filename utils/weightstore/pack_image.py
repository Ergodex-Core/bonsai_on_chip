"""Pack the pinned Bonsai Q1_0 checkpoint into a native read-only weight image.

Tensor starts and image end are aligned to 64 bytes. Payloads are copied
unchanged from the GGUF; the descriptor manifest is a separate file.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

MODEL_ID = "prism-ml/Bonsai-1.7B-gguf"
MODEL_REVISION = "210a9e99f79cb184909d49595906526eb2b3dd9a"
MODEL_SHA256 = "3d7c6c90dd98717a203adb22d5eacd2581850e40aa5327e144b97766cae5f7e3"
MODEL_BYTES = 248302272
PAYLOAD_BYTES = 242357152
IMAGE_BYTES = 242357184
LINE_BYTES = 64
CHUNK_BYTES = 1024 * 1024


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def align(value):
    return (value + LINE_BYTES - 1) // LINE_BYTES * LINE_BYTES


def expected_tensors():
    """Exact tensor names, row-major shapes and GGML type IDs in this checkpoint."""
    expected = {
        "token_embd.weight": ((151669, 2048), 41),
        "output_norm.weight": ((2048, ), 0),
    }
    layer = {
        "attn_k": ((1024, 2048), 41),
        "attn_v": ((1024, 2048), 41),
        "attn_q": ((2048, 2048), 41),
        "attn_output": ((2048, 2048), 41),
        "ffn_gate": ((6144, 2048), 41),
        "ffn_up": ((6144, 2048), 41),
        "ffn_down": ((2048, 6144), 41),
        "attn_k_norm": ((128, ), 0),
        "attn_q_norm": ((128, ), 0),
        "attn_norm": ((2048, ), 0),
        "ffn_norm": ((2048, ), 0),
    }
    for index in range(28):
        for name, descriptor in layer.items():
            expected[f"blk.{index}.{name}.weight"] = descriptor
    return expected


def inspect_checkpoint(source):
    """Import GGUF only for the real pack operation; core tests use stdlib only."""
    if source.stat().st_size != MODEL_BYTES or sha256(source) != MODEL_SHA256:
        raise ValueError(
            "Source size/SHA256 does not match the pinned Q1_0 checkpoint"
        )
    from gguf import GGUFReader
    reader = GGUFReader(source)
    if reader.byte_order != "I" or reader.fields["general.architecture"
                                                 ].contents() != "qwen3":
        raise ValueError("Expected native little-endian Qwen3 GGUF")
    if reader.fields["general.file_type"].contents() != 40:
        raise ValueError("Expected GGUF MOSTLY_Q1_0 file type 40")
    expected = expected_tensors()
    names = [tensor.name for tensor in reader.tensors]
    if len(names) != len(set(names)) or set(names) != set(expected):
        raise ValueError("Missing, extra or duplicate GGUF tensor names")
    records = []
    cursor = 0
    for tensor in reader.tensors:
        shape = tuple(int(n) for n in tensor.shape[::-1])
        type_id = int(tensor.tensor_type)
        if (shape, type_id) != expected[tensor.name]:
            raise ValueError(f"Unexpected shape/type for {tensor.name}")
        elements = 1
        for extent in shape:
            elements *= extent
        payload_bytes = elements // 128 * 18 if type_id == 41 else elements * 4
        if int(tensor.n_elements) != elements or int(tensor.n_bytes
                                                     ) != payload_bytes:
            raise ValueError(
                f"Unexpected element/byte count for {tensor.name}"
            )
        source_offset = int(tensor.data_offset)
        if source_offset < reader.data_offset or source_offset + payload_bytes > MODEL_BYTES:
            raise ValueError("Tensor range is outside the GGUF payload")
        row_elements = shape[-1]
        record = {
            "name":
            tensor.name,
            "format":
            "Q1_0" if type_id == 41 else "F32",
            "shape":
            list(shape),
            "shape_order":
            "row_major",
            "elements":
            elements,
            "offset":
            cursor,
            "payload_bytes":
            payload_bytes,
            "source_gguf_offset":
            source_offset,
            "row_bytes":
            row_elements // 128 * 18 if type_id == 41 else row_elements * 4,
            "rows":
            shape[0] if len(shape) == 2 else 1,
            "padding_after_bytes":
            align(cursor + payload_bytes) - cursor - payload_bytes,
        }
        if type_id == 41:
            record.update(
                blocks_per_row=row_elements // 128,
                block_elements=128,
                block_bytes=18
            )
        records.append(record)
        cursor = align(cursor + payload_bytes)
    if (Counter(record["format"]
                for record in records) != {"Q1_0": 197, "F32": 113}
            or sum(record["payload_bytes"]
                   for record in records) != PAYLOAD_BYTES
            or cursor != IMAGE_BYTES):
        raise ValueError("Unexpected complete native image inventory/size")
    # Require non-overlapping source ranges, independently of descriptor order.
    source_end = reader.data_offset
    for record in sorted(records, key=lambda item: item["source_gguf_offset"]):
        if record["source_gguf_offset"] < source_end:
            raise ValueError("Overlapping source tensor payloads")
        source_end = record["source_gguf_offset"] + record["payload_bytes"]
    return records


def assemble_image(source, image, records):
    """Write payloads in GGUF descriptor order, with deterministic zero padding."""
    with source.open("rb") as original, image.open("xb") as packed:
        for record in records:
            if packed.tell() != record["offset"] or packed.tell() % LINE_BYTES:
                raise ValueError("Noncanonical image offsets")
            original.seek(record["source_gguf_offset"])
            remaining = record["payload_bytes"]
            digest = hashlib.sha256()
            while remaining:
                data = original.read(min(remaining, CHUNK_BYTES))
                if not data:
                    raise ValueError("Truncated source tensor")
                packed.write(data)
                digest.update(data)
                remaining -= len(data)
            record["sha256"] = digest.hexdigest()
            packed.write(bytes(record["padding_after_bytes"]))
        packed.flush()
        os.fsync(packed.fileno())


def verify_image(source, image, records):
    """Reopen both files; compare every payload byte/hash and every padding byte."""
    position = 0
    with source.open("rb") as original, image.open("rb") as packed:
        for record in records:
            if record["offset"] != position or position % LINE_BYTES:
                raise ValueError("Invalid tensor offset or image ordering")
            original.seek(record["source_gguf_offset"])
            remaining = record["payload_bytes"]
            digest = hashlib.sha256()
            while remaining:
                count = min(remaining, CHUNK_BYTES)
                actual = packed.read(count)
                expected = original.read(count)
                if len(actual) != count or len(
                        expected) != count or actual != expected:
                    raise ValueError(
                        f"Payload differs from GGUF: {record['name']}"
                    )
                digest.update(actual)
                remaining -= count
            if digest.hexdigest() != record["sha256"]:
                raise ValueError(f"Tensor hash mismatch: {record['name']}")
            position += record["payload_bytes"]
            padding = align(position) - position
            if record["padding_after_bytes"] != padding or packed.read(
                    padding) != bytes(padding):
                raise ValueError("Missing or nonzero image padding")
            position += padding
        if packed.read(1) or image.stat().st_size != position:
            raise ValueError("Extra image bytes or incorrect final size")
    return position


def pack_image(source, output):
    """Publish a complete image/manifest directory only after independent checks."""
    source, output = Path(source).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(
            "Output path already exists; choose a fresh directory"
        )
    records = inspect_checkpoint(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
    )
    try:
        image = staging / "weights.bin"
        assemble_image(source, image, records)
        verified_bytes = verify_image(source, image, records)
        if verified_bytes != IMAGE_BYTES or sha256(source) != MODEL_SHA256:
            raise ValueError(
                "Unexpected image size or source changed while packing"
            )
        manifest = {
            "schema": "coralnpu.weightstore.native.v1",
            "status": "verified",
            "source": {
                "model": MODEL_ID,
                "revision": MODEL_REVISION,
                "file": "Bonsai-1.7B-Q1_0.gguf",
                "bytes": MODEL_BYTES,
                "sha256": MODEL_SHA256
            },
            "line_bytes": LINE_BYTES,
            "byte_order": "little",
            "tensor_order": "gguf",
            "image_file": "weights.bin",
            "image_bytes": verified_bytes,
            "image_sha256": sha256(image),
            "payload_bytes": PAYLOAD_BYTES,
            "padding_bytes": verified_bytes - PAYLOAD_BYTES,
            "tensor_count": len(records),
            "aliases": {
                "lm_head.weight": "token_embd.weight"
            },
            "q1_0_layout": {
                "group_elements": 128,
                "group_bytes": 18,
                "scale_offset_bytes": 0,
                "scale_format": "IEEE754_binary16_little_endian",
                "signs_offset_bytes": 2,
                "sign_bytes": 16,
                "sign_bit_order": "lsb_first",
                "zero_bit": "-scale",
                "one_bit": "+scale"
            },
            "verification": {
                "all_payload_bytes_equal_source": True,
                "all_tensor_hashes_verified": True,
                "all_padding_zero": True
            },
            "packer_sha256": sha256(Path(__file__)),
            "tensors": records,
        }
        with (staging / "manifest.json").open("x") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if output.exists() or output.is_symlink():
            raise FileExistsError("Output path appeared during packing")
        # Same-filesystem directory rename publishes the verified pair together.
        staging.rename(output)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gguf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = pack_image(args.gguf, args.out)
    print(
        json.dumps({
            key: manifest[key]
            for key in (
                "status", "tensor_count", "image_bytes", "payload_bytes",
                "padding_bytes", "image_sha256"
            )
        },
                   indent=2)
    )


if __name__ == "__main__":
    main()
