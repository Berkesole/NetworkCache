#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec python3 "${SCRIPT_DIR}/run_latency_v3_suite.py" run --phase iid-uniform
