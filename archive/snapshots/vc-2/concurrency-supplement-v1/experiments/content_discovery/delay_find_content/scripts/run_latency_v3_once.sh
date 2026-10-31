#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd -- "${EXP_DIR}/../../.." && pwd)
LAB_DIR="${REPO_ROOT}/kathara/ruikekkk"
LATENCY_SUITE_NAME="${LATENCY_SUITE_NAME:-latency-v3}"
V3_ROOT="${LATENCY_WORKLOAD_ROOT:-${EXP_DIR}/workloads/latency-v3}"
WIRE_VERSION="${LATENCY_WIRE_VERSION:-2}"
BLOOM_BITS="${LATENCY_BLOOM_BITS:-1024}"
BLOOM_HASH_COUNT="${LATENCY_BLOOM_HASH_COUNT:-1}"
STAGED_FALLBACK="${LATENCY_STAGED_FALLBACK:-0}"
FALLBACK_DELAY_MS="${LATENCY_FALLBACK_DELAY_MS:-1500}"
QUERY_TIMEOUT_MS="${LATENCY_QUERY_TIMEOUT_MS:-4500}"
CONTROLLER_TIMEOUT_MS="${LATENCY_CONTROLLER_TIMEOUT_MS:-5000}"
MAX_HOP_COUNT="${LATENCY_MAX_HOP_COUNT:-8}"
SHARED_NAMESPACE="${LATENCY_SHARED_NAMESPACE:-${LATENCY_SUITE_NAME}}"
ENABLE_SNAPSHOTS="${LATENCY_ENABLE_SNAPSHOTS:-0}"

usage() {
  echo "Usage: $0 <scenario> <nc|known-owner|tcp-direct> <new-run-dir>" >&2
}

[[ $# -eq 3 ]] || { usage; exit 2; }
SCENARIO="$1"
METHOD="$2"
RUN_DIR="$3"
[[ "${METHOD}" == "nc" || "${METHOD}" == "known-owner" || "${METHOD}" == "tcp-direct" ]] || { usage; exit 2; }
[[ ! -e "${RUN_DIR}" ]] || { echo "run directory already exists: ${RUN_DIR}" >&2; exit 2; }

SCENARIO_FILE="${V3_ROOT}/scenarios/${SCENARIO}.yaml"
METADATA_FILE="${V3_ROOT}/mapped/${SCENARIO}.metadata.json"
INPUT_DIR="${V3_ROOT}/mapped/${SCENARIO}"
[[ -f "${SCENARIO_FILE}" && -f "${METADATA_FILE}" && -f "${INPUT_DIR}/schedule.tsv" ]] || {
  echo "missing latency-v3 generated input for ${SCENARIO}" >&2
  exit 2
}

mapfile -t META < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["host_count"]); print(d["mapping_sha256"]); print(d["topology"]); print(d["value_size_bytes"]); print(d["workload"]); print(d["seed"])' "${METADATA_FILE}")
HOST_COUNT="${META[0]}"
SCHEDULE_HASH="${META[1]}"
TOPOLOGY="${META[2]}"
VALUE_SIZE="${META[3]}"
WORKLOAD="${META[4]}"
SEED="${META[5]}"
if (( VALUE_SIZE <= 1024 )); then
  QUIESCENCE_STABLE_MS=250
elif (( VALUE_SIZE <= 65536 )); then
  QUIESCENCE_STABLE_MS=500
elif (( VALUE_SIZE <= 1048576 )); then
  QUIESCENCE_STABLE_MS=2000
else
  QUIESCENCE_STABLE_MS=5000
fi

mkdir -p "${RUN_DIR}/logs" "${RUN_DIR}/traces"
cp "${SCENARIO_FILE}" "${RUN_DIR}/scenario.yaml"
cp "${METADATA_FILE}" "${RUN_DIR}/schedule_metadata.json"
cp "${INPUT_DIR}/schedule.tsv" "${RUN_DIR}/schedule.tsv"

SHARED_V3="${LAB_DIR}/shared/${SHARED_NAMESPACE}"
rm -rf "${SHARED_V3}"
mkdir -p "${SHARED_V3}/data" "${SHARED_V3}/control"
mkdir -p "${LAB_DIR}/shared/logs" "${LAB_DIR}/shared/traces"
rm -f "${LAB_DIR}/shared/logs/"*.log "${LAB_DIR}/shared/traces/"*.jsonl
cp "${INPUT_DIR}/data/"node*.tsv "${SHARED_V3}/data/"
cp "${SCRIPT_DIR}/latency_v3_tcp_node.py" "${SHARED_V3}/tcp_node.py"
cp "${SCRIPT_DIR}/latency_v3_clock_probe.py" "${SHARED_V3}/clock_probe.py"
cp "${SCRIPT_DIR}/latency_v3_inspect_network.sh" "${SHARED_V3}/inspect_network.sh"
if [[ "${ENABLE_SNAPSHOTS}" == 1 ]]; then
  cp "${SCRIPT_DIR}/latency_v4_cache_probe.py" "${SHARED_V3}/cache_probe.py"
fi

STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
GIT_COMMIT=$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)
GIT_STATUS=$(git -C "${REPO_ROOT}" status --porcelain=v1 --untracked-files=all 2>/dev/null || true)
GIT_DIRTY=$([[ -n "${GIT_STATUS}" ]] && echo 1 || echo 0)
GIT_DIRTY_STATE_SHA256=$(printf '%s' "${GIT_STATUS}" | sha256sum | awk '{print $1}')
GIT_DIRTY_PATCH_SHA256=$(git -C "${REPO_ROOT}" diff --binary HEAD -- | sha256sum | awk '{print $1}')
V3_DOCKERFILE="${LATENCY_V3_DOCKERFILE_PATH:-${REPO_ROOT}/Dockerfile.latency-v3}"
[[ -f "${V3_DOCKERFILE}" ]] || { echo "missing latency-v3 Dockerfile: ${V3_DOCKERFILE}" >&2; exit 2; }
DOCKERFILE_SHA256=$(sha256sum "${V3_DOCKERFILE}" | awk '{print $1}')

TOPOLOGY_A="${LAB_DIR}/shared/ruikekkk-topology-cluster-a.yaml"
TOPOLOGY_B="${LAB_DIR}/shared/ruikekkk-topology-cluster-b.yaml"
TOPOLOGY_BACKUP=$(mktemp -d "${TMPDIR:-/tmp}/${LATENCY_SUITE_NAME}.topology.XXXXXX")
for pair in "a:${TOPOLOGY_A}" "b:${TOPOLOGY_B}"; do
  label=${pair%%:*}; target=${pair#*:}
  if [[ -e "${target}" ]]; then cp -p -- "${target}" "${TOPOLOGY_BACKUP}/${label}.yaml"; else touch "${TOPOLOGY_BACKUP}/${label}.missing"; fi
done
cleanup() {
  local rc=$?
  trap - EXIT
  "${REPO_ROOT}/scripts/kathara/down_ruikekkk_scenario.sh" >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || true
  for pair in "a:${TOPOLOGY_A}" "b:${TOPOLOGY_B}"; do
    label=${pair%%:*}; target=${pair#*:}
    if [[ -f "${TOPOLOGY_BACKUP}/${label}.yaml" ]]; then
      temporary="${target}.latency-restore.$$"; cp -p -- "${TOPOLOGY_BACKUP}/${label}.yaml" "${temporary}" && mv -f -- "${temporary}" "${target}"
    elif [[ -f "${TOPOLOGY_BACKUP}/${label}.missing" ]]; then rm -f -- "${target}"; fi
  done
  rm -rf -- "${SHARED_V3}"
  rm -rf -- "${TOPOLOGY_BACKUP}"
  exit "${rc}"
}
trap cleanup EXIT

set +e
env \
  DOCKERFILE_PATH="${V3_DOCKERFILE}" \
  BUILD_NO_CACHE="${LATENCY_V3_BUILD_NO_CACHE:-1}" \
  SCENARIOS_DIR="${V3_ROOT}/scenarios" \
  ENABLE_REDIS=ON FETCH_REDIS_DEPS=ON \
  KATHARA_NODE_LOOP_SLEEP_MS=25 \
  KATHARA_LINK_DELAY_MS=2 KATHARA_LINK_RATE_MBIT=1000 \
  KATHARA_LINK_QUEUE_PACKETS=1000 KATHARA_LINK_MTU=1500 \
  RSYNC_WIRE_VERSION="${WIRE_VERSION}" RSYNC_BLOOM_BITS="${BLOOM_BITS}" \
  RSYNC_BLOOM_HASH_COUNT="${BLOOM_HASH_COUNT}" RSYNC_STAGED_FALLBACK="${STAGED_FALLBACK}" \
  RSYNC_FALLBACK_DELAY_MS="${FALLBACK_DELAY_MS}" RSYNC_REDIS_TIMEOUT_MS=1000 \
  RSYNC_REDIS_MAXMEMORY=0 RSYNC_REDIS_MAXMEMORY_POLICY=noeviction \
  RSYNC_REDIS_REQUIRE_EMPTY=1 \
  RSYNC_QUERY_TIMEOUT_MS="${QUERY_TIMEOUT_MS}" \
  RSYNC_ORACLE_UNICAST=$([[ "${METHOD}" == "known-owner" ]] && echo 1 || echo 0) \
  RSYNC_LOOP_ITERATIONS=100000000 \
  RSYNC_SKIP_APP=$([[ "${METHOD}" == "tcp-direct" ]] && echo 1 || echo 0) \
  RSYNC_EVENT_TRACE=1 RSYNC_EVENT_TRACE_FLUSH_EVERY=64 \
  RSYNC_EVENT_TRACE_RUN_ID="$(basename "${RUN_DIR}")" \
  RSYNC_EXPERIMENT_CONTROL_DIR="/shared/${SHARED_NAMESPACE}/control" \
  RSYNC_EXPERIMENT_DATA_DIR="/shared/${SHARED_NAMESPACE}/data" \
  RSYNC_EXPERIMENT_SCHEDULE_HASH="${SCHEDULE_HASH}" \
  RUNTIME_PROVENANCE_FILE="${RUN_DIR}/runtime_image_id.before_launch" \
  "${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}" \
  >"${RUN_DIR}/driver.stdout.log" 2>"${RUN_DIR}/driver.stderr.log"
START_STATUS=$?
set -e
if [[ "${START_STATUS}" -ne 0 ]]; then
  STATUS="driver_failed"
  CONTROLLER_STATUS=99
else
  tcp_pids=()
  cache_probe_pids=()
  if [[ "${METHOD}" == "tcp-direct" ]]; then
    for node in $(seq 1 "${HOST_COUNT}"); do
      timeout 900 kathara exec -d "${LAB_DIR}" --wait "node${node}" \
        python3 "/shared/${SHARED_NAMESPACE}/tcp_node.py" \
          "${node}" "/shared/${SHARED_NAMESPACE}/data/node${node}.tsv" \
          "/shared/${SHARED_NAMESPACE}/control" "${SCHEDULE_HASH}" \
        >"${RUN_DIR}/logs/node${node}.tcp-wrapper.log" 2>&1 &
      tcp_pids+=("$!")
    done
  elif [[ "${ENABLE_SNAPSHOTS}" == 1 ]]; then
    for node in $(seq 1 "${HOST_COUNT}"); do
      timeout 43200 kathara exec -d "${LAB_DIR}" --wait "node${node}" \
        python3 "/shared/${SHARED_NAMESPACE}/cache_probe.py" "${node}" \
          "/shared/${SHARED_NAMESPACE}/data/node${node}.tsv" \
          "/shared/${SHARED_NAMESPACE}/control" "${VALUE_SIZE}" \
        >"${RUN_DIR}/logs/node${node}.cache-probe.log" 2>&1 &
      cache_probe_pids+=("$!")
    done
  fi
  set +e
  snapshot_args=()
  if [[ "${ENABLE_SNAPSHOTS}" == 1 && "${METHOD}" != tcp-direct ]]; then
    snapshot_args+=(--snapshot-host-count "${HOST_COUNT}" --snapshot-timeout-s 60
      --snapshot-output-dir "${RUN_DIR}")
  fi
  python3 "${SCRIPT_DIR}/latency_v3_controller.py" \
    --schedule "${INPUT_DIR}/schedule.tsv" \
    --control-dir "${SHARED_V3}/control" \
    --host-count "${HOST_COUNT}" --method "${METHOD}" \
    --timeout-ms "${CONTROLLER_TIMEOUT_MS}" --think-ms 100 \
    --quiescence-timeout-ms 15000 \
    --quiescence-stable-ms "${QUIESCENCE_STABLE_MS}" \
    --output "${RUN_DIR}/request_results.csv" \
    "${snapshot_args[@]}" \
    >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log"
  CONTROLLER_STATUS=$?
  for pid in "${tcp_pids[@]:-}"; do
    [[ -n "${pid}" ]] && wait "${pid}" || true
  done
  for pid in "${cache_probe_pids[@]:-}"; do
    [[ -n "${pid}" ]] && wait "${pid}" || true
  done
  set -e
  STATUS=$([[ "${CONTROLLER_STATUS}" -eq 0 ]] && echo ok || echo incomplete)
fi

if [[ "${START_STATUS}" -eq 0 ]]; then
  find "${LAB_DIR}/shared/logs" -maxdepth 1 -type f -name '*.log' -exec cp {} "${RUN_DIR}/logs/" \; 2>/dev/null || true
  find "${LAB_DIR}/shared/traces" -maxdepth 1 -type f -name '*.jsonl' -exec cp {} "${RUN_DIR}/traces/" \; 2>/dev/null || true
fi

# Simultaneous cross-container monotonic probe. A common shared trigger keeps
# scheduling noise small enough to detect a time-namespace offset.
if [[ "${START_STATUS}" -eq 0 ]]; then
  rm -rf "${SHARED_V3}/clock-probe"
  mkdir -p "${SHARED_V3}/clock-probe"
  clock_pids=()
  for node in $(seq 1 "${HOST_COUNT}"); do
    kathara exec -d "${LAB_DIR}" --wait "node${node}" \
      python3 "/shared/${SHARED_NAMESPACE}/clock_probe.py" "${node}" "/shared/${SHARED_NAMESPACE}/clock-probe" \
      >/dev/null 2>&1 &
    clock_pids+=("$!")
  done
  sleep 1
  touch "${SHARED_V3}/clock-probe/go"
  for pid in "${clock_pids[@]}"; do wait "${pid}" || true; done
  {
    echo "node,monotonic_ns,timens_offsets_sha256"
    for node in $(seq 1 "${HOST_COUNT}"); do
      if [[ -f "${SHARED_V3}/clock-probe/node${node}.txt" ]]; then
        probe="$(tr -d '\r\n' < "${SHARED_V3}/clock-probe/node${node}.txt")"
        echo "${node},${probe//$'\t'/,}"
      fi
    done
  } >"${RUN_DIR}/clock_state.tsv"
fi

if [[ "${START_STATUS}" -eq 0 ]]; then {
  LEAF_COUNT=$((HOST_COUNT / 4))
  DEVICE_IDS=$(seq 1 "${HOST_COUNT}"; seq 101 $((100 + LEAF_COUNT)); seq 201 204)
  for node in ${DEVICE_IDS}; do
    echo "## node${node}"
    kathara exec -d "${LAB_DIR}" --wait "node${node}" \
      /bin/sh "/shared/${SHARED_NAMESPACE}/inspect_network.sh" || true
  done
} >"${RUN_DIR}/network_state.txt" 2>&1; fi

if [[ "${START_STATUS}" -eq 0 ]] && find "${RUN_DIR}/traces" -type f -name '*.jsonl' | grep -q .; then
  if [[ -n "${LATENCY_TRACE_ANALYZER:-}" ]]; then
    python3 "${LATENCY_TRACE_ANALYZER}" --results "${RUN_DIR}/request_results.csv" \
      --schedule "${RUN_DIR}/schedule.tsv" --traces "${RUN_DIR}/traces" \
      --topology "${TOPOLOGY}" --output "${RUN_DIR}/trace_breakdown.csv" \
      >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || STATUS="incomplete"
  else
    python3 "${SCRIPT_DIR}/analyze_trace_events.py" \
    --traces-dir "${RUN_DIR}/traces" \
    --summary-output "${RUN_DIR}/trace_summary.csv" \
    --breakdown-output "${RUN_DIR}/trace_breakdown.csv" \
    --by-class-output "${RUN_DIR}/trace_breakdown_by_class.csv" \
    --scenario "${SCENARIO}" --env kathara-latency-v3 --topology "${TOPOLOGY}" \
    --topology-file "${RUN_DIR}/scenario.yaml" --schedule-metadata "${RUN_DIR}/schedule_metadata.json" \
      >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || STATUS="incomplete"
  fi
fi

FINISHED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [[ -f "${RUN_DIR}/runtime_image_id.before_launch" ]]; then
  RUNTIME_IMAGE_ID=$(tr -d '\r\n' <"${RUN_DIR}/runtime_image_id.before_launch")
else
  RUNTIME_IMAGE_ID=unavailable
fi
python3 -c 'import json,sys; p=sys.argv[1]; json.dump({"scenario":sys.argv[2],"method":sys.argv[3],"topology":sys.argv[4],"workload":sys.argv[5],"seed":int(sys.argv[6]),"value_size_bytes":int(sys.argv[7]),"status":sys.argv[8],"controller_status":int(sys.argv[9]),"git_commit":sys.argv[10],"git_dirty":int(sys.argv[11]),"git_dirty_state_sha256":sys.argv[19],"git_dirty_patch_sha256":sys.argv[29],"git_snapshot_at":sys.argv[13],"git_snapshot_phase":"before_kathara_launch","schedule_sha256":sys.argv[12],"suite":sys.argv[20],"wire_version":int(sys.argv[21]),"bloom_bits":int(sys.argv[22]),"bloom_hash_count":int(sys.argv[23]),"staged_fallback":sys.argv[24]=="1","fallback_delay_ms":int(sys.argv[25]),"cache_backend":"redis","redis_timeout_ms":1000,"query_table_timeout_ms":int(sys.argv[26]),"max_hop_count":int(sys.argv[27]),"node_loop_sleep_ms":25,"link_delay_ms":2,"link_rate_mbit":1000,"link_queue_packets":1000,"mtu":1500,"request_timeout_ms":int(sys.argv[28]),"quiescence_timeout_ms":15000,"quiescence_stable_ms":int(sys.argv[15]),"think_time_ms":100,"started_at":sys.argv[13],"finished_at":sys.argv[14],"dockerfile_path":sys.argv[16],"dockerfile_sha256":sys.argv[17],"runtime_image_id":sys.argv[18],"runtime_image_snapshot_phase":"after_build_before_kathara_launch"},open(p,"w"),indent=2,sort_keys=True); open(p,"a").write("\n")' \
  "${RUN_DIR}/metadata.json" "${SCENARIO}" "${METHOD}" "${TOPOLOGY}" "${WORKLOAD}" "${SEED}" "${VALUE_SIZE}" "${STATUS}" "${CONTROLLER_STATUS}" "${GIT_COMMIT}" "${GIT_DIRTY}" "${SCHEDULE_HASH}" "${STARTED_AT}" "${FINISHED_AT}" "${QUIESCENCE_STABLE_MS}" "${V3_DOCKERFILE}" "${DOCKERFILE_SHA256}" "${RUNTIME_IMAGE_ID}" "${GIT_DIRTY_STATE_SHA256}" "${LATENCY_SUITE_NAME}" "${WIRE_VERSION}" "${BLOOM_BITS}" "${BLOOM_HASH_COUNT}" "${STAGED_FALLBACK}" "${FALLBACK_DELAY_MS}" "${QUERY_TIMEOUT_MS}" "${MAX_HOP_COUNT}" "${CONTROLLER_TIMEOUT_MS}" "${GIT_DIRTY_PATCH_SHA256}"

echo "${STATUS} ${RUN_DIR}"
[[ "${STATUS}" == "ok" ]]
