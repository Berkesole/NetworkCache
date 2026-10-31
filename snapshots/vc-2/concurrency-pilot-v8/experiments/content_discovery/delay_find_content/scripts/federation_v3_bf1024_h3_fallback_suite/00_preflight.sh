#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
REPO=$(cd -- "${HERE}/../../../.." && pwd)
LOCK="${REPO}/kathara/ruikekkk/.federation-v3-bf1024-h3-fallback-pilot.lock/owner"

for command in python3 docker kathara cmake c++ pdfunite; do
  command -v "${command}" >/dev/null || { echo "missing dependency: ${command}" >&2; exit 1; }
done
[[ ! -e "${LOCK}" ]] || { echo "active or stale pilot lock: ${LOCK}" >&2; cat "${LOCK}" >&2; exit 1; }
available_kib=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
((available_kib >= 16777216)) || { echo "pilot requires at least 16 GiB available memory" >&2; exit 1; }

python3 "${HERE}/generate_federation_v3_bf1024_h3_fallback_pilot.py"
python3 -m py_compile "${HERE}"/*federation_v3_bf1024_h3_fallback*.py
python3 "${HERE}/test_federation_v3_bf1024_h3_fallback_pilot.py"
bash -n "${HERE}/run_federation_v3_bf1024_h3_fallback_once.sh"
for script in "${HERE}"/federation_v3_bf1024_h3_fallback_suite/*.sh; do bash -n "${script}"; done
for script in "${HERE}"/federation_v3_bf1024_h3_fallback_postfix_suite/*.sh; do bash -n "${script}"; done

render_check=$(mktemp -d)
trap 'rm -rf "${render_check}"' EXIT
mkdir -p "${render_check}/lab"
scenario=kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot-catalog512-zipf-nc-seed001
LAB_DIR="${render_check}/lab" SHARED_DIR="${render_check}/shared" \
  SCENARIOS_DIR="${REPO}/experiments/content_discovery/delay_find_content/workloads/federation-v3-bf1024-h3-fallback-pilot/scenarios" \
  RSYNC_WIRE_VERSION=3 RSYNC_BLOOM_BITS=1024 RSYNC_BLOOM_HASH_COUNT=3 \
  RSYNC_STAGED_FALLBACK=1 RSYNC_FALLBACK_DELAY_MS=1500 RSYNC_QUERY_TIMEOUT_MS=7000 \
  RSYNC_FEDERATION_METHOD=nc "${REPO}/scripts/kathara/render_ruikekkk_scenario.sh" "${scenario}" >/dev/null
grep -q 'export RSYNC_WIRE_VERSION="3"' "${render_check}/lab/node1.startup"
grep -q 'export RSYNC_BLOOM_BITS="1024"' "${render_check}/lab/node1.startup"
grep -q 'export RSYNC_BLOOM_HASH_COUNT="3"' "${render_check}/lab/node1.startup"
grep -q 'export RSYNC_STAGED_FALLBACK="1"' "${render_check}/lab/node1.startup"
grep -q 'export RSYNC_FALLBACK_DELAY_MS="1500"' "${render_check}/lab/node1.startup"
grep -q 'export RSYNC_QUERY_TIMEOUT_MS="7000"' "${render_check}/lab/node1.startup"
grep -q 'max_hop_count: 12' "${render_check}/shared/ruikekkk-topology-cluster-a.yaml"
rm -rf "${render_check}"
trap - EXIT

cmake --build "${REPO}/build" --target test_core test_packet_codec test_query_table_fallback \
  test_node_staged_fallback rsync-node-dev -- -j2
"${REPO}/build/test_core"
"${REPO}/build/test_packet_codec"
"${REPO}/build/test_query_table_fallback"
"${REPO}/build/test_node_staged_fallback"
docker ps >/dev/null
echo "federation-v3 bf1024-h3 staged-fallback pilot preflight passed"
