#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
for file in "${SCRIPT_DIR}/latency_v3_suite/"*.sh; do bash -n "${file}"; done
python3 "${SCRIPT_DIR}/run_latency_v3_suite.py" preflight-static
if [[ "${LATENCY_V3_SKIP_SMOKE:-0}" != "1" ]]; then
  smoke_args=()
  [[ "${LATENCY_V3_OFFLINE_BUILD:-0}" != "1" ]] || smoke_args+=(--offline-build)
  python3 "${SCRIPT_DIR}/run_latency_v3_smoke.py" "${smoke_args[@]}"
fi
echo "latency-v3 preflight passed"
