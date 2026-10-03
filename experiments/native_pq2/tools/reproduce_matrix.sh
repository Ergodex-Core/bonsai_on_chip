#!/usr/bin/env bash
# Run inside the allocated EC2 service/cgroup. This script launches no instance.
set -euo pipefail
if [[ $# -ne 4 ]]; then
  echo 'Usage: reproduce_matrix.sh MODEL_GGUF FETCH_RTL SPATIAL_RTL OUTPUT_DIRECTORY' >&2
  exit 2
fi
base=$(cd "$(dirname "$0")/.." && pwd)
model=$1
fetch_rtl=$2
spatial_rtl=$3
output=$4
args=(--variant "E1=${base}/baseline/coral_weight_axi.sv")
for depth in 1 4 8; do
  args+=(--variant "E1-fetch-D${depth}=${fetch_rtl}" --parameter "E1-fetch-D${depth}:FETCH_DEPTH=${depth}" --check-cpu-overlap "E1-fetch-D${depth}")
done
for lanes in 1 2 4 8 16 32; do
  args+=(--variant "spatial-L${lanes}=${spatial_rtl}" --parameter "spatial-L${lanes}:DOT_LANES=${lanes}" --check-cpu-overlap "spatial-L${lanes}")
done
for tensor in q down; do
  if [[ "${tensor}" == q ]]; then offset=90141280; blocks=16; else offset=91812960; blocks=48; fi
  python3 "${base}/run_benchmark.py" "${args[@]}" \
    --profile ideal:1:1:8:0 --profile lat20:20:1:8:0 \
    --profile lat80:80:4:8:0 --profile limited:80:8:2:0 --profile stress:20:4:2:25 \
    --latency-jitter 0 --mmio-stall-max 3 --jobs 2 --model "${model}" \
    --offset "${offset}" --pq2-type-id 142 --model-cases "${blocks}" \
    --allocation coordinated-operator-slot --output "${output}/${tensor}"
done
