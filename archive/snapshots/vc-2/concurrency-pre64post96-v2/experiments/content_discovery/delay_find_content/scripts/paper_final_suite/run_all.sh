#!/usr/bin/env bash
set -uo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
status=0
"${HERE}/00_preflight.sh" || exit $?
if [[ ! -f "${HERE}/../../results/formal-bf1024-h3-campaign-r2/campaign_lock.json" ]]; then
  echo "missing campaign lock; run ${HERE}/05_smoke.sh once before the formal campaign" >&2
  exit 2
fi
"${HERE}/10_latency_v4.sh" || status=1
"${HERE}/20_federation_v4.sh" || status=1
"${HERE}/90_validate.sh" || status=1
exit "${status}"
