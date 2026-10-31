#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
REPO=$(cd -- "${HERE}/../../../.." && pwd)
for cmd in python3 docker kathara cmake; do command -v "${cmd}" >/dev/null || { echo "missing ${cmd}" >&2; exit 1; }; done
if [[ -e "${REPO}/kathara/ruikekkk/.federation-v1.lock/owner" ]]; then
  echo "stale or active federation-v1 lab lock:" >&2
  cat "${REPO}/kathara/ruikekkk/.federation-v1.lock/owner" >&2
  exit 1
fi
available_kib=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
((available_kib >= 12582912)) || { echo "federation-v1 requires at least 12 GiB available memory for 88 containers" >&2; exit 1; }
available_blocks=$(df -Pk "${REPO}" | awk 'NR==2{print $4}')
((available_blocks >= 20971520)) || { echo "federation-v1 requires at least 20 GiB free disk" >&2; exit 1; }
python3 "${HERE}/generate_federation_v1.py"
python3 -m py_compile "${HERE}/generate_federation_v1.py" "${HERE}/federation_v1_controller.py" \
  "${HERE}/run_federation_v1_suite.py" "${HERE}/analyze_federation_v1.py"
echo "static preflight passed; run FEDERATION_V1_RUN_SMOKE=1 $0 to execute the 88-container smoke"
if [[ "${FEDERATION_V1_RUN_SMOKE:-0}" == 1 ]]; then
  smoke_root="${REPO}/experiments/content_discovery/delay_find_content/results/federation-v1-smoke"
  mkdir -p "${smoke_root}"
  index=1
  while :; do
    output=$(printf '%s/activation-repeated-nc-%03d' "${smoke_root}" "${index}")
    [[ -e "${output}" ]] || break
    index=$((index + 1))
  done
  FEDERATION_V1_BUILD_NO_CACHE=0 "${HERE}/run_federation_v1_once.sh" \
    kua-dc-v3-federation-v1-repeated-seed001 nc "${output}"
  python3 "${HERE}/run_federation_v1_suite.py" validate-run --config repeated-nc --run-dir "${output}"
fi
