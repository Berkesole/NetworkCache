#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SCRIPTS=$(cd -- "${HERE}/.." && pwd)
python3 "${SCRIPTS}/run_latency_v4_bf1024_h3_c512_suite.py" smoke \
  --method nc --topology 16
RSYNC_SMOKE_REUSE_IMAGE=1 python3 \
  "${SCRIPTS}/run_latency_v4_bf1024_h3_c512_suite.py" smoke \
  --method known-owner --topology 16
RSYNC_SMOKE_REUSE_IMAGE=1 python3 \
  "${SCRIPTS}/run_latency_v4_bf1024_h3_c512_suite.py" smoke \
  --method known-owner --topology 32
RSYNC_SMOKE_REUSE_IMAGE=1 python3 \
  "${SCRIPTS}/run_latency_v4_bf1024_h3_c512_suite.py" smoke \
  --method tcp-direct --topology 32
source "${SCRIPTS}/federation_v3_bf1024_h3_fallback_postfix_suite/postfix_env.sh"
export FEDERATION_FALLBACK_RESULTS_ROOT="${SCRIPTS}/../results/federation-v4-bf1024-h3-formal-r2/smoke-gate"
"${SCRIPTS}/federation_v3_bf1024_h3_fallback_suite/05_smoke.sh"
python3 "${SCRIPTS}/formal_campaign.py" create
echo "R2 method and federation smokes passed; no formal attempt was started"
