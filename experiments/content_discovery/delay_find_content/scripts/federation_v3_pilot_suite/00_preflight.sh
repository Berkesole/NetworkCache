#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
REPO=$(cd -- "${HERE}/../../../.." && pwd)
for cmd in python3 docker kathara cmake c++; do command -v "${cmd}" >/dev/null || { echo "missing ${cmd}" >&2; exit 1; }; done
[[ ! -e "${REPO}/kathara/ruikekkk/.federation-v3-pilot.lock/owner" ]] || {
  echo "active/stale federation-v3 pilot lock" >&2; cat "${REPO}/kathara/ruikekkk/.federation-v3-pilot.lock/owner" >&2; exit 1; }
available_kib=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
((available_kib >= 16777216)) || { echo "federation-v3 pilot requires at least 16 GiB available memory" >&2; exit 1; }
available_blocks=$(df -Pk "${REPO}" | awk 'NR==2{print $4}')
((available_blocks >= 31457280)) || { echo "federation-v3 pilot requires at least 30 GiB free disk" >&2; exit 1; }
python3 "${HERE}/generate_federation_v3_pilot.py"
python3 "${HERE}/prepare_federation_v3_pilot_smoke.py"
python3 -m py_compile "${HERE}"/*federation_v3*.py
python3 "${HERE}/test_federation_v3_pilot.py"
bash -n "${HERE}/run_federation_v3_pilot_once.sh"
for script in "${HERE}"/federation_v3_pilot_suite/*.sh; do bash -n "${script}"; done
docker ps >/dev/null
echo "static preflight passed"
if [[ "${FEDERATION_V3_RUN_SMOKE:-0}" == 1 ]]; then
  python3 "${HERE}/run_federation_v3_pilot_smoke.py"
else
  echo "run FEDERATION_V3_RUN_SMOKE=1 $0 for the required 88-container smoke"
fi
