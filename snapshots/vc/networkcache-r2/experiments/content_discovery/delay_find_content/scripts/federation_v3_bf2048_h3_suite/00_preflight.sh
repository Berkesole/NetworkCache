#!/usr/bin/env bash
set -euo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
REPO=$(cd -- "${HERE}/../../../.." && pwd)
LOCK="${REPO}/kathara/ruikekkk/.federation-v3-bf2048-h3-pilot.lock/owner"

for command in python3 docker kathara cmake c++; do
  command -v "${command}" >/dev/null || { echo "missing dependency: ${command}" >&2; exit 1; }
done
[[ ! -e "${LOCK}" ]] || { echo "active or stale pilot lock: ${LOCK}" >&2; cat "${LOCK}" >&2; exit 1; }

available_kib=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
((available_kib >= 16777216)) || { echo "pilot requires at least 16 GiB available memory" >&2; exit 1; }
available_blocks=$(df -Pk "${REPO}" | awk 'NR==2{print $4}')
((available_blocks >= 20971520)) || { echo "pilot requires at least 20 GiB free disk" >&2; exit 1; }

python3 "${HERE}/generate_federation_v3_bf2048_h3_pilot.py"
python3 -m py_compile "${HERE}"/*federation_v3_bf2048_h3*.py
python3 "${HERE}/test_federation_v3_bf2048_h3_pilot.py"
bash -n "${HERE}/run_federation_v3_bf2048_h3_once.sh"
for script in "${HERE}"/federation_v3_bf2048_h3_suite/*.sh; do bash -n "${script}"; done

render_check=$(mktemp -d)
trap 'rm -rf "${render_check}"' EXIT
mkdir -p "${render_check}/lab"
scenario=kua-dc-v3-federation-v3-bf2048-h3-pilot-catalog512-zipf-nc-seed001
LAB_DIR="${render_check}/lab" SHARED_DIR="${render_check}/shared" \
  SCENARIOS_DIR="${REPO}/experiments/content_discovery/delay_find_content/workloads/federation-v3-bf2048-h3-pilot/scenarios" \
  RSYNC_BLOOM_BITS=2048 RSYNC_BLOOM_HASH_COUNT=3 RSYNC_FEDERATION_METHOD=nc \
  "${REPO}/scripts/kathara/render_ruikekkk_scenario.sh" "${scenario}" >/dev/null
grep -q 'export RSYNC_BLOOM_BITS="2048"' "${render_check}/lab/node1.startup"
grep -q 'export RSYNC_BLOOM_HASH_COUNT="3"' "${render_check}/lab/node1.startup"
grep -q 'max_hop_count: 12' "${render_check}/shared/ruikekkk-topology-cluster-a.yaml"
grep -q 'max_hop_count: 12' "${render_check}/shared/ruikekkk-topology-cluster-b.yaml"
rm -rf "${render_check}"
trap - EXIT

cmake --build "${REPO}/build" --target test_core test_packet_codec rsync-node-dev -- -j2
"${REPO}/build/test_core"
"${REPO}/build/test_packet_codec"
docker ps >/dev/null
echo "federation-v3 bf2048-h3 pilot preflight passed"
