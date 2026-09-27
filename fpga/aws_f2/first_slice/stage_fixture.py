#!/usr/bin/env python3
"""Verify canonical bytes and generate a compact DDR/XSim fixture outside Git."""
import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def signed_inputs(case):
    data = bytes.fromhex(case["inputs_int8_hex"])
    if len(data) != 128:
        raise ValueError(
            "Each fixture must supply exactly 128 int8 activations"
        )
    return data, [x if x < 128 else x - 256 for x in data]


def stage_fixture(fixture_path, image_path, output):
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    if fixture.get("schema") != "coralnpu.first_slice.fixture.v1":
        raise ValueError("Unsupported fixture schema")
    canonical = fixture["canonical_image"]
    if (canonical["bytes"] != 242357184 or canonical["sha256"] !=
            "ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99"
        ):
        raise ValueError(
            "Fixture does not name the pinned canonical Bonsai image"
        )
    if (image_path.stat().st_size != canonical["bytes"]
            or sha256_file(image_path) != canonical["sha256"]):
        raise ValueError("Canonical image size/SHA256 mismatch")
    real = [
        case for case in fixture["cases"] if case["kind"] == "real_q1_0_signs"
    ]
    selected = [
        real[0],
        next(
            c for c in real
            if c["crosses_64_byte_line"] and not c["crosses_4096_byte_page"]
        ),
        next(c for c in real if c["crosses_4096_byte_page"])
    ]
    # Preserve each selected boundary shape, but explicitly relocate the blocks.
    offsets = [
        0, 64 + selected[1]["logical_image_offset"] % 64,
        selected[2]["logical_image_offset"] % 4096
    ]
    if offsets[2] + 18 <= 4096:
        raise ValueError("Selected third block does not cross a page")
    image = bytearray(4160)
    records = []
    with image_path.open("rb") as source:
        for case, offset in zip(selected, offsets):
            source.seek(case["logical_image_offset"])
            block = source.read(18)
            if (block.hex() != case["native_q1_0_hex"]
                    or hashlib.sha256(block).hexdigest()
                    != case["native_block_sha256"]
                    or block[:2].hex() != case["scale_fp16_le_hex"]):
                raise ValueError("Native block/scale mismatch: " + case["id"])
            inputs, signed = signed_inputs(case)
            signs = int.from_bytes(block[2:], "little")
            oracle = sum(
                value * (1 if signs >> lane & 1 else -1)
                for lane, value in enumerate(signed)
            )
            if oracle != case["expected_dot_i32"]:
                raise ValueError(
                    "Independent integer dot disagrees: " + case["id"]
                )
            image[offset:offset + 18] = block
            records.append({
                "id":
                case["id"],
                "kind":
                case["kind"],
                "opcode":
                1,
                "tensor":
                case["tensor"],
                "canonical_offset":
                case["logical_image_offset"],
                "relocated_offset":
                offset,
                "block_sha256":
                case["native_block_sha256"],
                "inputs_int8_hex":
                inputs.hex(),
                "expected_dot_i32":
                oracle,
                "expected_raw_scale_u16":
                int.from_bytes(block[:2], "little")
            })
    case = next(
        c for c in fixture["synthetic_cases"] if c["id"] == "synthetic_mixed"
    )
    packed = bytes.fromhex(case["operations_ternary2_hex"])
    if len(packed) != 32 or not case["expected_valid"]:
        raise ValueError("Invalid synthetic ternary fixture")
    inputs, signed = signed_inputs(case)
    operations = [(packed[i // 4] >> (2 * (i % 4))) & 3 for i in range(128)]
    if 3 in operations:
        raise ValueError("Reserved ternary operation in valid fixture")
    oracle = sum(
        value * (0, 1, -1)[operation]
        for value, operation in zip(signed, operations)
    )
    if oracle != case["expected_dot_i32"]:
        raise ValueError("Independent ternary dot disagrees")
    image[256:288] = packed
    records.append({
        "id": case["id"],
        "kind": case["kind"],
        "opcode": 2,
        "canonical_offset": None,
        "relocated_offset": 256,
        "block_sha256": hashlib.sha256(packed).hexdigest(),
        "inputs_int8_hex": inputs.hex(),
        "expected_dot_i32": oracle,
        "expected_raw_scale_u16": 0x3c00
    })
    digest = hashlib.sha256(image).digest()
    lines = [
        "// Generated fixture: compact relocated bytes, not the full canonical image.",
        f"localparam int FIXTURE_BYTES = {len(image)};",
        f"localparam int FIXTURE_LINES = {len(image) // 64};",
        f"localparam int FIXTURE_CASES = {len(records)};",
        "logic [511:0] fixture_lines [FIXTURE_LINES];",
        "logic [1023:0] fixture_activations [FIXTURE_CASES];",
        "logic [31:0] fixture_digest [8];",
        "logic [31:0] fixture_opcode [FIXTURE_CASES];",
        "logic [31:0] fixture_offset [FIXTURE_CASES];",
        "logic [31:0] fixture_dot [FIXTURE_CASES];",
        "logic [15:0] fixture_scale [FIXTURE_CASES];",
        "task automatic initialize_fixture();"
    ]
    for i in range(len(image) // 64):
        lines.append(
            f"  fixture_lines[{i}]=512'h{image[i*64:(i+1)*64][::-1].hex()};"
        )
    for i in range(8):
        lines.append(
            f"  fixture_digest[{i}]=32'h{int.from_bytes(digest[i*4:i*4+4], 'little'):08x};"
        )
    for i, record in enumerate(records):
        lines += [
            f"  fixture_activations[{i}]=1024'h{bytes.fromhex(record['inputs_int8_hex'])[::-1].hex()};",
            f"  fixture_opcode[{i}]=32'd{record['opcode']};",
            f"  fixture_offset[{i}]=32'd{record['relocated_offset']};",
            f"  fixture_dot[{i}]=32'h{record['expected_dot_i32'] & 0xffffffff:08x};",
            f"  fixture_scale[{i}]=16'h{record['expected_raw_scale_u16']:04x};"
        ]
    lines += ["endtask", ""]
    manifest = {
        "schema": "coralnpu.f2.compact_fixture.v1",
        "scope":
        "3 actual Q1_0 blocks relocated to exercise aligned/line-crossing/page-crossing DDR reads; 1 separate synthetic ternary case. Not a full canonical image load or inference.",
        "canonical_image": canonical,
        "source_fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "compact_image_bytes": len(image),
        "compact_image_sha256": digest.hex(),
        "independent_integer_oracles_verified": True,
        "cases": records
    }
    output.mkdir(parents=True, exist_ok=True)
    for name in ("first_slice_fixture.svh", "compact_fixture.bin",
                 "compact_fixture.json"):
        if (output / name).exists():
            raise ValueError("Fixture destination exists: " + name)
    (output / "first_slice_fixture.svh").write_text("\n".join(lines))
    (output / "compact_fixture.bin").write_bytes(image)
    (output /
     "compact_fixture.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = stage_fixture(args.fixture, args.image, args.output)
    print(
        json.dumps({
            key: result[key]
            for key in
            ("scope", "compact_image_bytes", "compact_image_sha256")
        },
                   indent=2)
    )


if __name__ == "__main__":
    main()
