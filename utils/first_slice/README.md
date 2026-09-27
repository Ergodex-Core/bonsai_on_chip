# First-slice real-weight and generic-operation fixtures

This generator prepares block-level regression operands. It preserves the
canonical Bonsai Q1_0 image and selects the first and last block from all 197
matrices, plus two blocks crossing a native-image 64-byte line and 4 KiB page.
The result is 396 real cases. Q1_0 remains a binary sign format; mapping its
signs into generic two-bit operations does not make it a ternary checkpoint.
These fixtures do not demonstrate model inference or hardware execution.

## Reproduce locally

Use Python 3.11 or later with `gguf==0.19.0`, the exact checkpoint pinned in the
[weight image tool](../weightstore/README.md), and fresh output directories
outside tracked source directories. `MODEL` is an absolute checkpoint path;
`NATIVE` and `FIXTURE` are separate local output directories:

```bash
python utils/first_slice/prepare_fixture.py \
  --gguf "$MODEL" --native "$NATIVE" --out "$FIXTURE"
python utils/first_slice/prepare_fixture.py \
  --gguf "$MODEL" --native "$NATIVE" --out "$FIXTURE" --verify
python -m unittest discover -s utils/first_slice -p test_prepare_fixture.py -v
```

The first command creates the canonical image with the existing packer if it
is absent. An existing image must match the pinned hash, all tensor metadata,
source bytes and padding; corruption is rejected without overwriting it.
Fixture output must be fresh. `--verify` regenerates expected fixture contents
from the pinned source and canonical image and compares existing artifacts.
Tests use only the Python standard library and also run with:

```bash
bazel test //utils/first_slice:test_prepare_fixture
```

## Consumer contract

`fixture.json` uses schema `coralnpu.first_slice.fixture.v1`. Its `cases` array
contains real Q1_0 data; `synthetic_cases` is a separate generic-operation suite.
Hexadecimal byte strings encode bytes in increasing address/lane order.
No local absolute paths are embedded. Source/model revision and SHA256,
canonical image and manifest hashes, and generator hash bind the fixture.

Each real case contains:

- `id`, tensor name, row, group, selection rule and `logical_image_offset`.
- `native_q1_0_hex`: exactly 18 original bytes, with a block SHA256.
- `scale_fp16_le_hex`: the unchanged first two bytes of that block.
- `operations_ternary2_hex`: 32 bytes, four two-bit operation codes per byte.
- `inputs_int8_hex`: 128 bytes interpreted as signed two's-complement int8.
- `expected_dot_i32`: the exact integer dot product before scale application.
- `expected_host_scaled_fp32_le_hex`: four little-endian FP32 reference bytes.

For lane `i`, the operation code is in bits `2*(i%4)+:2` of byte `i//4`:
`00=0`, `01=+1`, `10=-1`, `11=invalid`. Invalid codes fail the whole operation,
including when their corresponding activation is zero. Real Q1_0 cases use
only codes `01` and `10`. Inputs come from the specified SHA256 counter stream
with seed 103; lanes 0 through 3 are forced to `[-128, 127, -1, 0]`.
Negating -128 must widen before arithmetic. The full 128-lane accumulator fits
signed int32 without saturation.

Numeric version `q1-sign-int8-dot128-host-fp32-scale.v1` requires exact integer
comparison. The separate host stage computes scale times that integer and
rounds once to IEEE binary32, round to nearest, ties to even. FP16 scale bits
are preserved, never estimated or requantized. This host calculation is not
an FPGA scale-stage result and is not the model's native activation kernel.

`synthetic-ternary2.bin` contains six separate 32-byte records with no Q1_0
scales. They cover zeros, all-positive/all-negative operations with -128
inputs, mixed operations, and invalid codes at the first and last lanes.
Their offsets refer only to this 192-byte test image, never to the canonical
native weight image. Invalid cases have `expected_valid=false` and no integer
result. A simulator/FPGA driver must report rejection, not substitute a dot.

## Attribution and generated artifacts

The pinned [Bonsai release](https://huggingface.co/prism-ml/Bonsai-1.7B-gguf/tree/210a9e99f79cb184909d49595906526eb2b3dd9a)
uses Apache-2.0 and includes Prism ML and Qwen3/Alibaba attribution in its
[NOTICE](https://huggingface.co/prism-ml/Bonsai-1.7B-gguf/blob/210a9e99f79cb184909d49595906526eb2b3dd9a/NOTICE.txt).
Generated fixture directories include the Apache license and attribution,
plus a notice explaining the block selection and operation-code conversion.
Keep those files with any redistributed derived vectors. The full checkpoint,
canonical image and generated real-weight vectors are not committed by this
tool. Reports may publish source hashes, aggregate coverage and measured results.
