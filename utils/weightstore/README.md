# Native CoralNPU weight image prototype

This tool prepares the exact pinned Bonsai Q1_0 checkpoint for a common ROM,
DDR or HBM weight-store interface. It copies native tensor bytes and scales,
adds only 64-byte tensor alignment, and aliases the tied output head. It does
not implement the storage controller, CoralNPU inference or an FPGA bitstream.
See the [architecture contract](../../doc/microarch/weightstore.md).

Use Python 3.11 or later with `gguf==0.19.0` in an isolated environment.
The model must already be downloaded and match the size/SHA256 pinned in the
tool; another model or quantization format is rejected. Set `MODEL` to the
absolute checkpoint path and `OUT` to a new directory outside tracked sources:

```bash
python utils/weightstore/pack_image.py --gguf "$MODEL" --out "$OUT"
python -m unittest discover -s utils/weightstore -p test_pack_image.py -v
```

The integrity tests use the standard library and are also a Bazel target:

```bash
bazel test //utils/weightstore:test_pack_image
```

Successful packing publishes `weights.bin` and `manifest.json` together only
after reopening and verifying every tensor payload, hash and padding byte.
Existing output directories are refused. The resulting image contains 310
tensors in 242,357,184 bytes; the manifest describes native row strides, group
packing, source offsets, image offsets and the tied-head alias. Its source GGUF
offsets are provenance only; hardware uses the canonical image offsets.

Expected image SHA-256:
`ccd70c080f8758e1b4df58c2fdbdd6d13bb65571abbc56feb6942de1f8c07d99`.

The [validated prototype report](../../reports/ERG-102/weightstore-image-prototype.json)
records exact source hashes and results. No generated weight image is committed.
