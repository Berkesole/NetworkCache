#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
for file in "${SCRIPT_DIR}/latency_suite/"*.sh; do
  bash -n "${file}"
done
python3 "${SCRIPT_DIR}/run_latency_v2_suite.py" preflight-static
exec python3 "${SCRIPT_DIR}/run_latency_v2_smoke.py"
