#!/usr/bin/env bash
set -uo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
echo "campaign=r2"
echo "== latency-v4 formal R2 =="
"${HERE}/../latency_v4_bf1024_h3_c512_suite/status.sh" || true
echo "== federation-v4 formal R2 =="
"${HERE}/../federation_v4_bf1024_h3_suite/status.sh" || true
