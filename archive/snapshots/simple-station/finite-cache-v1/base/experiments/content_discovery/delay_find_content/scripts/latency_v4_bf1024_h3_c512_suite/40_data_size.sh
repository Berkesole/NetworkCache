#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 "${SCRIPT_DIR}/run_latency_v4_bf1024_h3_c512_suite.py" run --phase data-size
