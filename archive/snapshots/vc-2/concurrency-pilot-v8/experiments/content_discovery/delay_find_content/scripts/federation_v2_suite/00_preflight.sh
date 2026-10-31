#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
REPO=$(cd -- "${HERE}/../../../.." && pwd)
for cmd in python3 docker kathara cmake; do command -v "${cmd}" >/dev/null || { echo "missing ${cmd}" >&2; exit 1; }; done
if [[ -e "${REPO}/kathara/ruikekkk/.federation-v2.lock/owner" ]]; then
  echo "stale or active federation-v2 lab lock:" >&2
  cat "${REPO}/kathara/ruikekkk/.federation-v2.lock/owner" >&2
  exit 1
fi
available_kib=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
((available_kib >= 12582912)) || { echo "federation-v2 requires at least 12 GiB available memory for 88 containers" >&2; exit 1; }
available_blocks=$(df -Pk "${REPO}" | awk 'NR==2{print $4}')
((available_blocks >= 20971520)) || { echo "federation-v2 requires at least 20 GiB free disk" >&2; exit 1; }
python3 "${HERE}/generate_federation_v2.py"
python3 -m py_compile "${HERE}/generate_federation_v2.py" "${HERE}/federation_v2_controller.py" \
  "${HERE}/federation_v2_cache_probe.py" "${HERE}/analyze_federation_v2_trace.py" \
  "${HERE}/run_federation_v2_suite.py" "${HERE}/analyze_federation_v2.py" \
  "${HERE}/prepare_federation_v2_smoke.py" "${HERE}/run_federation_v2_smoke.py"
python3 "${HERE}/test_federation_v2.py"
bash -n "${HERE}/run_federation_v2_once.sh"
for script in "${HERE}"/federation_v2_suite/*.sh; do bash -n "${script}"; done
containers=$(docker ps --format '{{.Names}}') || { echo "cannot access Docker API" >&2; exit 1; }
legacy_count=$(grep -Ec '^kathara_' <<<"${containers}" || true)
((legacy_count == 0)) || echo "warning: ${legacy_count} unrelated/legacy Kathara containers are active; memory gate has still passed"
echo "static preflight passed; run FEDERATION_V2_RUN_SMOKE=1 $0 to execute the 88-container smoke"
if [[ "${FEDERATION_V2_RUN_SMOKE:-0}" == 1 ]]; then
  python3 "${HERE}/run_federation_v2_smoke.py"
fi
