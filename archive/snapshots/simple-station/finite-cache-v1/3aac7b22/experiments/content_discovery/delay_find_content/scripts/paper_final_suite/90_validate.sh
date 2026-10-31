#!/usr/bin/env bash
set -uo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SCRIPTS=$(cd -- "${HERE}/.." && pwd)
REPO=$(cd -- "${SCRIPTS}/../../../.." && pwd)
status=0
python3 "${SCRIPTS}/formal_campaign.py" verify || status=1
"${SCRIPTS}/latency_v4_bf1024_h3_c512_suite/90_validate.sh" || status=1
"${SCRIPTS}/federation_v4_bf1024_h3_suite/90_validate.sh" || status=1
if ((status == 0)); then
  python3 "${REPO}/experiments/reports/generate_networkcache_bf1024_h3_formal_report.py" || status=1
fi
exit "${status}"
