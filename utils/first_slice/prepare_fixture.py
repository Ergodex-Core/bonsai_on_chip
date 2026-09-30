"""Prepare real Q1_0 block fixtures and separate generic two-bit test operands."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import sys
import tempfile

try:
    import pack_image
except ModuleNotFoundError:
    sys.path.insert(
        0, str(Path(__file__).resolve().parents[1] / "weightstore")
    )
    import pack_image

SCHEMA = "coralnpu.first_slice.fixture.v1"
NUMERICS = "q1-sign-int8-dot128-host-fp32-scale.v1"
IMAGE_SHA256 = "ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99"
SEED = 103
DOMAIN = b"ERG103-Q1-fixture-v1\0"
NOTICE = """This software is copyright 2026-present Prism ML, Inc. It is available under the Apache 2.0 license.
If you publicly deploy or redistribute this software, we would appreciate attribution such as: “Created using Bonsai by Prism ML.”

This software is built from Qwen3-1.7B, Copyright 2024 Alibaba Cloud, which is available under the Apache 2.0 License: https://huggingface.co/Qwen/Qwen3-1.7B/blob/main/LICENSE

Modifications: selected native Q1_0 blocks are extracted for regression tests; their binary signs are additionally represented as generic two-bit operation codes. Seeded synthetic int8 inputs and reference results are added. This is not a new model, native ternary checkpoint, or full-model inference.
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def activations(case_id):
    """Stable SHA256 counter stream; four directed values include int8 extremes."""
    raw = bytearray(
        b"".join(
            hashlib.
            sha256(DOMAIN +
                   struct.pack("<III", SEED, case_id, counter)).digest()
            for counter in range(4)
        )
    )
    raw[:4] = bytes((128, 127, 255, 0))
    return bytes(raw)


def signed_inputs(raw):
    if len(raw) != 128:
        raise ValueError("Expected exactly 128 int8 input bytes")
    return [value if value < 128 else value - 256 for value in raw]


def encode_operations(weights):
    if len(weights) != 128 or any(value not in (-1, 0, 1)
                                  for value in weights):
        raise ValueError("Expected 128 generic operations in {-1,0,1}")
    codes = {-1: 2, 0: 0, 1: 1}
    return bytes(
        sum(codes[weights[i + lane]] << (2 * lane)
            for lane in range(4))
        for i in range(0, 128, 4)
    )


def generic_dot(packed, inputs):
    """Independent generic-code oracle; invalid codes fail even for zero inputs."""
    if len(packed) != 32:
        raise ValueError("Expected exactly 32 operation bytes")
    total = 0
    for lane, value in enumerate(signed_inputs(inputs)):
        code = (packed[lane // 4] >> (2 * (lane % 4))) & 3
        if code == 3:
            raise ValueError(f"Invalid generic operation at lane {lane}")
        if code == 1:
            total += value
        elif code == 2:
            total -= value
    if not -(1 << 31) <= total < (1 << 31):
        raise ValueError("Accumulator does not fit int32")
    return total


def q1_oracle(raw, inputs):
    """Compute directly from native signs; never use the packed generic codes."""
    if len(raw) != 18:
        raise ValueError("Expected one native 18-byte Q1_0 block")
    scale = struct.unpack("<e", raw[:2])[0]
    if not math.isfinite(scale):
        raise ValueError("Nonfinite source FP16 scale")
    values = signed_inputs(inputs)
    weights = [
        2 * ((raw[2 + lane // 8] >> (lane % 8)) & 1) - 1
        for lane in range(128)
    ]
    dot = sum(weight * value for weight, value in zip(weights, values))
    # FP16 scale and this bounded integer have an exact binary64 product;
    # conversion below performs one round-to-nearest-even binary32 rounding.
    scaled = struct.pack("<f", scale * dot)
    return weights, dot, scaled


def selection_records(records):
    selected = []
    for tensor in records:
        if tensor["format"] == "Q1_0":
            selected.append((tensor, 0, "first"))
            selected.append(
                (tensor, tensor["payload_bytes"] // 18 - 1, "last")
            )
    q = next(
        record for record in records if record["name"] == "blk.0.attn_q.weight"
    )
    for boundary in (64, 4096):
        block = next(
            index for index in range(q["payload_bytes"] // 18)
            if (q["offset"] + 18 * index) % boundary + 18 > boundary
        )
        selected.append((q, block, f"first_crossing_{boundary}"))
    if len(selected) != 396:
        raise ValueError(
            "Expected first/last blocks for all 197 matrices plus two boundaries"
        )
    return selected


def load_native(source, native, create):
    records = pack_image.inspect_checkpoint(source)
    if not native.exists():
        if not create:
            raise FileNotFoundError("Native image directory does not exist")
        pack_image.pack_image(source, native)
    manifest = json.loads((native / "manifest.json").read_text())
    expected_source = {
        "model": pack_image.MODEL_ID,
        "revision": pack_image.MODEL_REVISION,
        "file": "Bonsai-1.7B-Q1_0.gguf",
        "bytes": pack_image.MODEL_BYTES,
        "sha256": pack_image.MODEL_SHA256,
    }
    if manifest.get("schema"
                    ) != "coralnpu.weightstore.native.v1" or manifest.get(
                        "source") != expected_source:
        raise ValueError("Native manifest schema/source mismatch")
    if manifest.get("image_bytes") != pack_image.IMAGE_BYTES or manifest.get(
            "image_sha256") != IMAGE_SHA256 or pack_image.sha256(
                native / "weights.bin") != IMAGE_SHA256:
        raise ValueError("Canonical native image hash/size mismatch")
    for key, expected in {
            "status": "verified",
            "line_bytes": 64,
            "byte_order": "little",
            "tensor_order": "gguf",
            "image_file": "weights.bin",
            "payload_bytes": pack_image.PAYLOAD_BYTES,
            "padding_bytes": pack_image.IMAGE_BYTES - pack_image.PAYLOAD_BYTES,
            "tensor_count": len(records),
    }.items():
        if manifest.get(key) != expected:
            raise ValueError(f"Native manifest metadata mismatch: {key}")
    actual = manifest.get("tensors", [])
    if len(actual) != len(records) or any(
            any(got.get(key) != value
                for key, value in expected.items())
            for got, expected in zip(actual, records)):
        raise ValueError("Native tensor metadata differs from pinned GGUF")
    if manifest.get("aliases") != {"lm_head.weight": "token_embd.weight"}:
        raise ValueError("Native tied-head alias mismatch")
    pack_image.verify_image(source, native / "weights.bin", actual)
    return records, manifest


def real_case(source, image, tensor, block, selection, case_id):
    relative = 18 * block
    logical = tensor["offset"] + relative
    source.seek(tensor["source_gguf_offset"] + relative)
    image.seek(logical)
    raw, from_image = source.read(18), image.read(18)
    if raw != from_image or len(raw) != 18:
        raise ValueError("Selected native image block differs from source")
    inputs = activations(case_id)
    weights, expected, scaled = q1_oracle(raw, inputs)
    operations = encode_operations(weights)
    if generic_dot(operations, inputs) != expected:
        raise ValueError(
            "Generic operation mapping differs from native sign oracle"
        )
    return {
        "id": f"q1_{case_id:03d}",
        "kind": "real_q1_0_signs",
        "tensor": tensor["name"],
        "selection": selection,
        "row": block // tensor["blocks_per_row"],
        "group": block % tensor["blocks_per_row"],
        "logical_image_offset": logical,
        "native_q1_0_hex": raw.hex(),
        "native_block_sha256": digest(raw),
        "scale_fp16_le_hex": raw[:2].hex(),
        "operations_ternary2_hex": operations.hex(),
        "inputs_int8_hex": inputs.hex(),
        "expected_dot_i32": expected,
        "expected_host_scaled_fp32_le_hex": scaled.hex(),
        "crosses_64_byte_line": logical % 64 + 18 > 64,
        "crosses_4096_byte_page": logical % 4096 + 18 > 4096,
    }


def synthetic_cases():
    cases, image = [], bytearray()
    for case_id, name in enumerate(
        ("zero", "positive_min_input", "negative_min_input", "mixed",
         "invalid_first", "invalid_last")):
        weights = [0] * 128 if name == "zero" else [
            1
        ] * 128 if name == "positive_min_input" else [
            -1
        ] * 128 if name == "negative_min_input" else [(-1, 0, 1)[i % 3]
                                                      for i in range(128)]
        packed = bytearray(encode_operations(weights))
        inputs = bytes([128]) * 128 if "min_input" in name else activations(
            396 + case_id
        )
        invalid_lane = None
        if name.startswith("invalid_"):
            invalid_lane = 0 if name == "invalid_first" else 127
            packed[invalid_lane // 4] |= 3 << (2 * (invalid_lane % 4))
        expected = None if invalid_lane is not None else sum(
            w * x for w, x in zip(weights, signed_inputs(inputs))
        )
        if invalid_lane is None and generic_dot(packed, inputs) != expected:
            raise ValueError("Synthetic reference disagreement")
        cases.append({
            "id": f"synthetic_{name}",
            "kind": "synthetic_generic_ternary2",
            "image_offset": len(image),
            "operations_ternary2_hex": packed.hex(),
            "inputs_int8_hex": inputs.hex(),
            "expected_valid": invalid_lane is None,
            "invalid_lane": invalid_lane,
            "expected_dot_i32": expected
        })
        image.extend(packed)
    return cases, bytes(image)


def build_fixture(source, native, records, manifest):
    cases = []
    with source.open("rb") as original, (native /
                                         "weights.bin").open("rb") as image:
        for case_id, (tensor, block,
                      selection) in enumerate(selection_records(records)):
            cases.append(
                real_case(original, image, tensor, block, selection, case_id)
            )
    synthetic, test_image = synthetic_cases()
    fixture = {
        "schema": SCHEMA,
        "status": "prepared_not_executed",
        "scope":
        "Block-level signed dot products; Q1_0 remains binary. Synthetic generic ternary operations are separate. No full-model, RTL or FPGA execution claim.",
        "source": manifest["source"],
        "canonical_image": {
            "sha256": IMAGE_SHA256,
            "bytes": pack_image.IMAGE_BYTES,
            "line_bytes": 64,
            "manifest_sha256": pack_image.sha256(native / "manifest.json")
        },
        "numerics": {
            "version": NUMERICS,
            "elements": 128,
            "input_type": "int8",
            "accumulator": "exact signed int32; no saturation",
            "host_scale_stage":
            "roundTiesToEven_binary32(exact_binary16_scale * exact_integer_dot)",
            "fpga_scale_stage": False
        },
        "operation_encoding": {
            "bytes": 32,
            "lane_order": "lane i in bits 2*(i%4)+:2 of byte i//4",
            "00": 0,
            "01": 1,
            "10": -1,
            "11": "invalid; fail entire operation"
        },
        "input_generator": {
            "algorithm":
            "SHA256(domain || uint32le(seed) || uint32le(case_index) || uint32le(counter)); concatenate counter=0,1,2,3",
            "domain_hex":
            DOMAIN.hex(),
            "seed":
            SEED,
            "override_first_four_signed_values": [-128, 127, -1, 0],
            "synthetic_extremes":
            "positive_min_input and negative_min_input use -128 in all lanes"
        },
        "cases": cases,
        "synthetic_cases": synthetic,
        "synthetic_image": {
            "file": "synthetic-ternary2.bin",
            "bytes": len(test_image),
            "sha256": digest(test_image),
            "canonical_native_image": False
        },
        "tool_sha256": pack_image.sha256(Path(__file__)),
        "license": {
            "spdx":
            "Apache-2.0",
            "notice_file":
            "NOTICE.txt",
            "license_file":
            "LICENSE",
            "upstream":
            f"https://huggingface.co/{pack_image.MODEL_ID}/tree/{pack_image.MODEL_REVISION}"
        },
    }
    return fixture, test_image


def verify_bundle(output, fixture, test_image, license_bytes):
    if json.loads((output / "fixture.json").read_text()) != fixture or (
            output / "synthetic-ternary2.bin").read_bytes() != test_image:
        raise ValueError(
            "Fixture or synthetic image differs from canonical regeneration"
        )
    if (output / "NOTICE.txt").read_text() != NOTICE or (
            output / "LICENSE").read_bytes() != license_bytes:
        raise ValueError("Fixture license/attribution is missing or changed")


def prepare(source, native, output, verify=False):
    source, native, output = Path(source).resolve(), Path(native).absolute(
    ), Path(output).absolute()
    if not verify and (output.exists() or output.is_symlink()):
        raise FileExistsError("Choose a fresh fixture output directory")
    records, manifest = load_native(source, native, create=not verify)
    fixture, test_image = build_fixture(source, native, records, manifest)
    license_bytes = (Path(__file__).resolve().parents[2] /
                     "LICENSE").read_bytes()
    if verify:
        verify_bundle(output, fixture, test_image, license_bytes)
        return fixture
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
    )
    try:
        (staging /
         "fixture.json").write_text(json.dumps(fixture, indent=2) + "\n")
        (staging / "synthetic-ternary2.bin").write_bytes(test_image)
        (staging / "NOTICE.txt").write_text(NOTICE)
        (staging / "LICENSE").write_bytes(license_bytes)
        verify_bundle(staging, fixture, test_image, license_bytes)
        if output.exists() or output.is_symlink():
            raise FileExistsError("Fixture output appeared during preparation")
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gguf", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--verify",
        action="store_true",
        help=
        "Regenerate expected contents and compare existing artifacts without writing"
    )
    args = parser.parse_args()
    fixture = prepare(args.gguf, args.native, args.out, args.verify)
    print(
        json.dumps({
            "status":
            "verified_not_executed" if args.verify else fixture["status"],
            "real_cases":
            len(fixture["cases"]),
            "synthetic_cases":
            len(fixture["synthetic_cases"]),
            "canonical_image_sha256":
            IMAGE_SHA256,
            "fixture_sha256":
            pack_image.sha256(args.out / "fixture.json")
        },
                   indent=2)
    )


if __name__ == "__main__":
    main()
