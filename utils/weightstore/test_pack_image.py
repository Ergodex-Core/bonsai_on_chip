"""Independent small-file checks of payload preservation and failure behavior."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pack_image import assemble_image, pack_image, verify_image


class NativeImageTests(unittest.TestCase):

    def fixture(self, root):
        # Eight native 18-byte groups cross two 64-byte line boundaries.
        q1 = bytes(range(144))
        norm = bytes.fromhex("0000803f")  # original little-endian F32 1.0
        source = root / "source.bin"
        source.write_bytes(b"prefix!" + q1 + b"gap" + norm)
        records = [
            {
                "name": "q1",
                "source_gguf_offset": 7,
                "offset": 0,
                "payload_bytes": 144,
                "padding_after_bytes": 48
            },
            {
                "name": "norm",
                "source_gguf_offset": 154,
                "offset": 192,
                "payload_bytes": 4,
                "padding_after_bytes": 60
            },
        ]
        return source, records, q1 + bytes(48) + norm + bytes(60)

    def test_preserves_native_bytes_crossing_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, records, expected = self.fixture(root)
            image = root / "weights.bin"
            assemble_image(source, image, records)
            self.assertEqual(image.read_bytes(), expected)
            self.assertEqual(
                records[0]["sha256"],
                hashlib.sha256(bytes(range(144))).hexdigest()
            )
            self.assertEqual(verify_image(source, image, records), 256)

    def test_corrupt_payload_and_padding_are_rejected(self):
        for offset, diagnostic in ((65, "Payload differs"), (145, "padding")):
            with self.subTest(offset=offset
                              ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, records, _ = self.fixture(root)
                image = root / "weights.bin"
                assemble_image(source, image, records)
                data = bytearray(image.read_bytes())
                data[offset] ^= 1
                image.write_bytes(data)
                with self.assertRaisesRegex(ValueError, diagnostic):
                    verify_image(source, image, records)

    def test_trailing_bytes_and_descriptor_hash_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, records, _ = self.fixture(root)
            image = root / "weights.bin"
            assemble_image(source, image, records)
            original_hash = records[0]["sha256"]
            records[0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verify_image(source, image, records)
            records[0]["sha256"] = original_hash
            with image.open("ab") as stream:
                stream.write(b"\x00")
            with self.assertRaisesRegex(ValueError, "Extra image bytes"):
                verify_image(source, image, records)

    def test_invalid_source_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wrong.gguf"
            source.write_bytes(b"not the pinned checkpoint")
            with self.assertRaisesRegex(ValueError, "size/SHA256"):
                pack_image(source, root / "out")
            self.assertFalse((root / "out").exists())
            self.assertFalse(list(root.glob(".out.*")))

    def test_failed_verification_cleans_unpublished_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, records, _ = self.fixture(root)
            with patch("pack_image.inspect_checkpoint", return_value=records), \
                    patch("pack_image.verify_image", side_effect=ValueError("injected verification failure")):
                with self.assertRaisesRegex(ValueError,
                                            "injected verification failure"):
                    pack_image(source, root / "out")
            self.assertFalse((root / "out").exists())
            self.assertFalse(list(root.glob(".out.*")))

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "out"
            output.mkdir()
            sentinel = output / "keep"
            sentinel.write_text("original")
            with self.assertRaises(FileExistsError):
                pack_image(root / "unused.gguf", output)
            self.assertEqual(sentinel.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
