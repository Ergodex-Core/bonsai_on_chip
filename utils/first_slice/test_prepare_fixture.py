"""Small independent checks for fixture numerics, integrity and fail-closed use."""
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import prepare_fixture as fixture


class FirstSliceTests(unittest.TestCase):

    def test_native_lsb_order_and_negative_int8_extreme(self):
        raw = struct.pack("<e", 0.5) + bytes([0xAA] * 16)
        inputs = bytes([128, 127] * 64)
        weights, dot, scaled = fixture.q1_oracle(raw, inputs)
        self.assertEqual(weights, [-1, 1] * 64)
        self.assertEqual(dot, 255 * 64)
        self.assertEqual(scaled, struct.pack("<f", 8160.0))
        packed = fixture.encode_operations(weights)
        self.assertEqual(packed, bytes([0x66] * 32))
        self.assertEqual(fixture.generic_dot(packed, inputs), 16320)

    def test_integer_extremes_and_zeros(self):
        inputs = bytes([128] * 128)
        self.assertEqual(
            fixture.generic_dot(bytes([0x55] * 32), inputs), -16384
        )
        self.assertEqual(
            fixture.generic_dot(bytes([0xAA] * 32), inputs), 16384
        )
        self.assertEqual(fixture.generic_dot(bytes(32), inputs), 0)

    def test_invalid_codes_fail_even_with_zero_inputs(self):
        for lane in (0, 63, 127):
            packed = bytearray(32)
            packed[lane // 4] = 3 << (2 * (lane % 4))
            with self.subTest(lane=lane
                              ), self.assertRaisesRegex(ValueError,
                                                        f"lane {lane}"):
                fixture.generic_dot(packed, bytes(128))

    def test_nonfinite_scale_and_wrong_lengths_fail(self):
        for scale in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(scale=scale
                              ), self.assertRaisesRegex(ValueError,
                                                        "Nonfinite"):
                fixture.q1_oracle(
                    struct.pack("<e", scale) + bytes(16), bytes(128)
                )
        with self.assertRaisesRegex(ValueError, "18-byte"):
            fixture.q1_oracle(bytes(17), bytes(128))
        with self.assertRaisesRegex(ValueError, "128 int8"):
            fixture.q1_oracle(bytes(18), bytes(127))
        with self.assertRaisesRegex(ValueError, "32 operation"):
            fixture.generic_dot(bytes(31), bytes(128))

    def test_host_scale_has_one_fp32_rounding(self):
        # 2047/1024 * 16319 is exactly halfway between neighboring FP32 values.
        raw = struct.pack("<e", 2047 / 1024) + bytes(16)
        inputs = bytes([128] * 127 + [193])  # final signed input -63
        _, dot, scaled = fixture.q1_oracle(raw, inputs)
        self.assertEqual(dot, 16319)
        self.assertEqual(scaled.hex(), "20dcfe46")

    def test_input_stream_is_stable_and_includes_extremes(self):
        expected = b"".join(
            hashlib.sha256(
                b"ERG103-Q1-fixture-v1\0" +
                struct.pack("<III", 103, 7, counter)
            ).digest() for counter in range(4)
        )
        self.assertEqual(
            fixture.activations(7),
            bytes([128, 127, 255, 0]) + expected[4:]
        )
        self.assertNotEqual(fixture.activations(7), fixture.activations(8))
        self.assertEqual(
            fixture.signed_inputs(fixture.activations(7))[:4],
            [-128, 127, -1, 0]
        )

    def test_source_and_native_block_mismatch_fails(self):
        tensor = {
            "offset": 0,
            "source_gguf_offset": 0,
            "name": "example",
            "blocks_per_row": 16
        }
        with self.assertRaisesRegex(ValueError, "differs from source"):
            fixture.real_case(
                io.BytesIO(bytes(18)), io.BytesIO(bytes([1]) + bytes(17)),
                tensor, 0, "first", 0
            )

    def test_synthetic_image_is_distinct_and_has_invalid_oracles(self):
        cases, image = fixture.synthetic_cases()
        self.assertEqual(len(cases), 6)
        self.assertEqual(len(image), 192)
        self.assertEqual([case["expected_dot_i32"] for case in cases[:3]],
                         [0, -16384, 16384])
        for case in cases:
            raw = image[case["image_offset"]:case["image_offset"] + 32]
            self.assertEqual(raw.hex(), case["operations_ternary2_hex"])
            inputs = bytes.fromhex(case["inputs_int8_hex"])
            if case["expected_valid"]:
                self.assertEqual(
                    fixture.generic_dot(raw, inputs), case["expected_dot_i32"]
                )
            else:
                self.assertIsNone(case["expected_dot_i32"])
                with self.assertRaises(ValueError):
                    fixture.generic_dot(raw, inputs)

    def test_bad_source_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wrong.gguf"
            source.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "size/SHA256"):
                fixture.prepare(source, root / "native", root / "out")
            self.assertFalse((root / "native").exists())
            self.assertFalse((root / "out").exists())

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "out"
            output.mkdir()
            sentinel = output / "keep"
            sentinel.write_text("previous")
            with self.assertRaises(FileExistsError):
                fixture.prepare(root / "unused", root / "native", output)
            self.assertEqual(sentinel.read_text(), "previous")

    def test_native_corruption_and_metadata_mismatch_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = root / "native"
            native.mkdir()
            original = bytes(64)
            (native / "weights.bin").write_bytes(original)
            record = {"name": "example", "offset": 0}
            manifest = {
                "schema": "coralnpu.weightstore.native.v1",
                "source": {
                    "model": fixture.pack_image.MODEL_ID,
                    "revision": fixture.pack_image.MODEL_REVISION,
                    "file": "Bonsai-1.7B-Q1_0.gguf",
                    "bytes": fixture.pack_image.MODEL_BYTES,
                    "sha256": fixture.pack_image.MODEL_SHA256
                },
                "status": "verified",
                "line_bytes": 64,
                "byte_order": "little",
                "tensor_order": "gguf",
                "image_file": "weights.bin",
                "payload_bytes": fixture.pack_image.PAYLOAD_BYTES,
                "padding_bytes": 64 - fixture.pack_image.PAYLOAD_BYTES,
                "tensor_count": 1,
                "image_bytes": 64,
                "image_sha256": hashlib.sha256(original).hexdigest(),
                "tensors": [{
                    "name": "example",
                    "offset": 1
                }]
            }
            (native / "manifest.json").write_text(json.dumps(manifest))
            with patch("prepare_fixture.pack_image.inspect_checkpoint",
                       return_value=[record]), patch(
                           "prepare_fixture.pack_image.IMAGE_BYTES",
                           64), patch("prepare_fixture.IMAGE_SHA256",
                                      hashlib.sha256(original).hexdigest()):
                with self.assertRaisesRegex(ValueError, "metadata differs"):
                    fixture.load_native(root / "source", native, False)
                (native / "weights.bin").write_bytes(bytes([1]) + original[1:])
                with self.assertRaisesRegex(ValueError,
                                            "image hash/size mismatch"):
                    fixture.load_native(root / "source", native, False)

    def test_existing_fixture_tamper_fails_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.json").write_text('{"expected_dot_i32": 6}')
            (root / "synthetic-ternary2.bin").write_bytes(b"synthetic")
            with self.assertRaisesRegex(ValueError, "canonical regeneration"):
                fixture.verify_bundle(
                    root, {"expected_dot_i32": 5}, b"synthetic", b"license"
                )

    def test_changed_attribution_fails_bundle_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.json").write_text('{}')
            (root / "synthetic-ternary2.bin").write_bytes(b"synthetic")
            (root / "NOTICE.txt").write_text("changed")
            (root / "LICENSE").write_bytes(b"license")
            with self.assertRaisesRegex(ValueError, "attribution"):
                fixture.verify_bundle(root, {}, b"synthetic", b"license")


if __name__ == "__main__":
    unittest.main()
