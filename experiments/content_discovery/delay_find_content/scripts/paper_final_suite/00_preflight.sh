#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SCRIPTS=$(cd -- "${HERE}/.." && pwd)
REPO=$(cd -- "${SCRIPTS}/../../../.." && pwd)
cd "${REPO}"

available_kib=$(df -Pk "${REPO}" | awk 'NR==2 {print $4}')
required_kib=$((200 * 1024 * 1024))
((available_kib >= required_kib)) || {
  echo "formal campaign requires at least 200 GiB free; available KiB=${available_kib}" >&2
  exit 2
}
available_mem_kib=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
required_mem_kib=$((16 * 1024 * 1024))
((available_mem_kib >= required_mem_kib)) || {
  echo "formal campaign requires at least 16 GiB available memory; available KiB=${available_mem_kib}" >&2
  exit 2
}

"${SCRIPTS}/latency_v4_bf1024_h3_c512_suite/00_preflight.sh"
python3 "${SCRIPTS}/run_latency_v4_bf1024_h3_c512_suite.py" approve-pilot
"${SCRIPTS}/federation_v4_bf1024_h3_suite/00_preflight.sh"
python3 "${SCRIPTS}/test_formal_campaign.py"
echo "R2 static preflight passed; run 05_smoke.sh once to freeze the corrected runtime image and R2 campaign lock"
