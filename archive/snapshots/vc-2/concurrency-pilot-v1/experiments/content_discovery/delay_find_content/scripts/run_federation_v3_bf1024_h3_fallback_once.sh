#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd -- "${EXP_DIR}/../../.." && pwd)
LAB_DIR="${REPO_ROOT}/kathara/ruikekkk"
SUITE=${FEDERATION_FALLBACK_SUITE:-federation-v3-bf1024-h3-fallback-pilot}
ROOT="${FEDERATION_V3_FALLBACK_WORKLOAD_ROOT:-${EXP_DIR}/workloads/${SUITE}}"
BLOOM_BITS=1024
BLOOM_HASH_COUNT=3
WIRE_VERSION=3
REQUEST_CODEC_FRAME_BYTES=162
REQUEST_TCP_FRAME_BYTES=166
FALLBACK_DELAY_MS=1500
QUERY_TIMEOUT_MS=7000
CONTROLLER_TIMEOUT_MS=8000

[[ $# -eq 2 || $# -eq 3 ]] || {
  echo "Usage: $0 <scenario> [nc|known-owner|tcp-direct] <new-run-dir>" >&2
  exit 2
}
SCENARIO=$1
if [[ $# -eq 2 ]]; then METHOD=nc; RUN_DIR=$2; else METHOD=$2; RUN_DIR=$3; fi
[[ "${METHOD}" =~ ^(nc|known-owner|tcp-direct)$ ]] || { echo "invalid method ${METHOD}" >&2; exit 2; }
[[ ! -e "${RUN_DIR}" ]] || { echo "run directory already exists: ${RUN_DIR}" >&2; exit 2; }
mkdir -p "${LAB_DIR}"
LOCK_DIR="${LAB_DIR}/.${SUITE}.lock"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "${SUITE} lab is already owned; inspect ${LOCK_DIR}/owner and teardown before retrying" >&2
  exit 3
fi
printf 'pid=%s\nrun_dir=%s\nstarted_at=%s\n' "$$" "${RUN_DIR}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${LOCK_DIR}/owner"
early_unlock() { rm -f "${LOCK_DIR}/owner"; rmdir "${LOCK_DIR}" 2>/dev/null || true; }
trap early_unlock EXIT

# The scenario renderer temporarily replaces these tracked topology views.
# Preserve the exact pre-run bytes (including any user edits) and restore them
# only after Kathara teardown.  Do not reconstruct them from Git.
TOPOLOGY_A="${LAB_DIR}/shared/ruikekkk-topology-cluster-a.yaml"
TOPOLOGY_B="${LAB_DIR}/shared/ruikekkk-topology-cluster-b.yaml"
TOPOLOGY_BACKUP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/${SUITE}.topology.XXXXXX")
TOPOLOGY_A_STATE=unknown
TOPOLOGY_B_STATE=unknown

restore_topology_file() {
  local target=$1
  local backup=$2
  local state=$3
  case "${state}" in
    present)
      local temporary="${target}.fallback-restore.$$"
      mkdir -p -- "$(dirname -- "${target}")"
      cp -p -- "${backup}" "${temporary}"
      mv -f -- "${temporary}" "${target}"
      ;;
    missing)
      rm -f -- "${target}"
      ;;
    unknown)
      # Backup did not complete, and the renderer has not started yet.
      ;;
  esac
}

KATHARA_LAUNCHED=false
cleanup() {
  local exit_status=$?
  trap - EXIT
  if [[ "${KATHARA_LAUNCHED}" == true ]]; then
    "${REPO_ROOT}/scripts/kathara/down_ruikekkk_scenario.sh" \
      >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || true
  fi
  restore_topology_file "${TOPOLOGY_A}" "${TOPOLOGY_BACKUP_DIR}/cluster-a.yaml" \
    "${TOPOLOGY_A_STATE}" || true
  restore_topology_file "${TOPOLOGY_B}" "${TOPOLOGY_BACKUP_DIR}/cluster-b.yaml" \
    "${TOPOLOGY_B_STATE}" || true
  rm -rf -- "${TOPOLOGY_BACKUP_DIR}"
  rm -f "${LOCK_DIR}/owner"
  rmdir "${LOCK_DIR}" 2>/dev/null || true
  exit "${exit_status}"
}
trap cleanup EXIT

if [[ -e "${TOPOLOGY_A}" ]]; then
  cp -p -- "${TOPOLOGY_A}" "${TOPOLOGY_BACKUP_DIR}/cluster-a.yaml"
  TOPOLOGY_A_STATE=present
else
  TOPOLOGY_A_STATE=missing
fi
if [[ -e "${TOPOLOGY_B}" ]]; then
  cp -p -- "${TOPOLOGY_B}" "${TOPOLOGY_BACKUP_DIR}/cluster-b.yaml"
  TOPOLOGY_B_STATE=present
else
  TOPOLOGY_B_STATE=missing
fi

INPUT="${ROOT}/mapped/${SCENARIO}"
[[ -f "${INPUT}/schedule.tsv" && -f "${INPUT}/metadata.json" ]] || {
  echo "missing generated input ${SCENARIO}" >&2
  exit 2
}
SCHEDULE_HASH=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["schedule_sha256"])' "${INPUT}/metadata.json")
mkdir -p "${RUN_DIR}/logs" "${RUN_DIR}/traces"
cp "${ROOT}/scenarios/${SCENARIO}.yaml" "${RUN_DIR}/scenario.yaml"
cp "${INPUT}/metadata.json" "${RUN_DIR}/schedule_metadata.json"
cp "${INPUT}/activation.json" "${RUN_DIR}/activation_spec.json"
cp "${INPUT}/schedule.tsv" "${RUN_DIR}/schedule.tsv"
cp "${INPUT}/isolation_probes.tsv" "${RUN_DIR}/isolation_probes.tsv"

SHARED="${LAB_DIR}/shared/${SUITE}"
rm -rf "${SHARED}"
mkdir -p "${SHARED}/data" "${SHARED}/control" "${LAB_DIR}/shared/logs" "${LAB_DIR}/shared/traces"
rm -f "${LAB_DIR}/shared/logs/"*.log "${LAB_DIR}/shared/traces/"*.jsonl
cp "${INPUT}/data/"node*.tsv "${SHARED}/data/"
cp "${SCRIPT_DIR}/latency_v3_clock_probe.py" "${SHARED}/clock_probe.py"
cp "${SCRIPT_DIR}/latency_v3_tcp_node.py" "${SHARED}/tcp_node.py"
cp "${SCRIPT_DIR}/federation_v2_inspect_network.sh" "${SHARED}/inspect_network.sh"
cp "${SCRIPT_DIR}/federation_v3_cache_probe.py" "${SHARED}/cache_probe.py"

STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# Freeze provenance before the image/build/containers can diverge from HEAD.
# The values below are intentionally not recomputed after the long-running lab.
GIT_SNAPSHOT_AT=${STARTED_AT}
GIT_COMMIT=$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)
GIT_STATUS=$(git -C "${REPO_ROOT}" status --porcelain=v1 --untracked-files=all 2>/dev/null || true)
GIT_DIRTY=$([[ -n "${GIT_STATUS}" ]] && echo true || echo false)
GIT_DIRTY_STATE_SHA256=$(printf '%s' "${GIT_STATUS}" | sha256sum | awk '{print $1}')
GIT_DIRTY_PATCH_SHA256=$(git -C "${REPO_ROOT}" diff --binary HEAD -- | sha256sum | awk '{print $1}')

KATHARA_LAUNCHED=true
set +e
env DOCKERFILE_PATH="${REPO_ROOT}/Dockerfile.latency-v3" \
  BUILD_NO_CACHE="${FEDERATION_V3_FALLBACK_BUILD_NO_CACHE:-1}" \
  SCENARIOS_DIR="${ROOT}/scenarios" ENABLE_REDIS=ON FETCH_REDIS_DEPS=ON \
  KATHARA_NODE_LOOP_SLEEP_MS=25 KATHARA_LINK_DELAY_MS=2 KATHARA_LINK_RATE_MBIT=1000 \
  KATHARA_LINK_QUEUE_PACKETS=1000 KATHARA_LINK_MTU=1500 \
  RSYNC_WIRE_VERSION=${WIRE_VERSION} RSYNC_BLOOM_BITS=${BLOOM_BITS} \
  RSYNC_BLOOM_HASH_COUNT=${BLOOM_HASH_COUNT} RSYNC_STAGED_FALLBACK=1 \
  RSYNC_FALLBACK_DELAY_MS=${FALLBACK_DELAY_MS} RSYNC_QUERY_TIMEOUT_MS=${QUERY_TIMEOUT_MS} \
  RSYNC_REDIS_TIMEOUT_MS=1000 RSYNC_REDIS_MAXMEMORY=0 \
  RSYNC_REDIS_MAXMEMORY_POLICY=noeviction RSYNC_REDIS_REQUIRE_EMPTY=1 \
  RSYNC_LOOP_ITERATIONS=100000000 \
  RSYNC_SKIP_APP=$([[ "${METHOD}" == tcp-direct ]] && echo 1 || echo 0) \
  RSYNC_ORACLE_UNICAST=$([[ "${METHOD}" == known-owner ]] && echo 1 || echo 0) \
  RSYNC_FEDERATION_METHOD="${METHOD}" RSYNC_EVENT_TRACE=1 RSYNC_EVENT_TRACE_FLUSH_EVERY=64 \
  RSYNC_EVENT_TRACE_RUN_ID="$(basename "${RUN_DIR}")" \
  RSYNC_EXPERIMENT_CONTROL_DIR="/shared/${SUITE}/control" \
  RSYNC_EXPERIMENT_DATA_DIR="/shared/${SUITE}/data" \
  RSYNC_EXPERIMENT_SCHEDULE_HASH="${SCHEDULE_HASH}" \
  RUNTIME_PROVENANCE_FILE="${RUN_DIR}/runtime_image_id.before_launch" \
  "${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}" \
  >"${RUN_DIR}/driver.stdout.log" 2>"${RUN_DIR}/driver.stderr.log"
START_STATUS=$?
set -e
STATUS=driver_failed
CONTROLLER_STATUS=99

if [[ ${START_STATUS} -eq 0 ]]; then
  startup_deadline=$((SECONDS + 300))
  ready_logs=0
  ready_switches=0
  while ((SECONDS < startup_deadline)); do
    ready_logs=0
    ready_switches=0
    for node in $(seq 1 64); do
      if [[ "${METHOD}" == tcp-direct ]]; then
        grep -q 'network configured without redis or rsync-node-dev' \
          "${LAB_DIR}/shared/logs/node${node}.startup.log" 2>/dev/null && ready_logs=$((ready_logs + 1))
      else
        grep -q 'started rsync-node-dev' "${LAB_DIR}/shared/logs/node${node}.startup.log" \
          2>/dev/null && ready_logs=$((ready_logs + 1))
      fi
    done
    for node in $(seq 101 108) $(seq 201 204) $(seq 301 308) $(seq 401 404); do
      grep -q 'configured forwarding switch' "${LAB_DIR}/shared/logs/node${node}.startup.log" 2>/dev/null && ready_switches=$((ready_switches + 1))
    done
    ((ready_logs == 64 && ready_switches == 24)) && break
    sleep 2
  done
  if ((ready_logs != 64 || ready_switches != 24)); then
    echo "startup gate: hosts=${ready_logs}/64 switches=${ready_switches}/24" >>"${RUN_DIR}/driver.stderr.log"
    CONTROLLER_STATUS=98
  else
    sleep 2
    bloom_ready=64
    wire_ready=64
    if [[ "${METHOD}" != tcp-direct ]]; then
      bloom_ready=0
      wire_ready=0
      for node in $(seq 1 64); do
        log="${LAB_DIR}/shared/logs/node${node}.log"
        grep -q "bloom configuration bits=${BLOOM_BITS} hashes=${BLOOM_HASH_COUNT}" "${log}" \
          2>/dev/null && bloom_ready=$((bloom_ready + 1))
        grep -q "wire configuration version=${WIRE_VERSION}" "${log}" \
          2>/dev/null && wire_ready=$((wire_ready + 1))
      done
    fi
    if ((bloom_ready != 64 || wire_ready != 64)); then
      echo "parameter gate: bloom=${bloom_ready}/64 wire=${wire_ready}/64" >>"${RUN_DIR}/driver.stderr.log"
      CONTROLLER_STATUS=97
    else
      sleep 5
      pids=()
      if [[ "${METHOD}" == tcp-direct ]]; then
        for node in $(seq 1 64); do
          timeout 10800 kathara exec -d "${LAB_DIR}" --wait "node${node}" python3 \
            "/shared/${SUITE}/tcp_node.py" "${node}" "/shared/${SUITE}/data/node${node}.tsv" \
            "/shared/${SUITE}/control" "${SCHEDULE_HASH}" \
            >"${RUN_DIR}/logs/node${node}.tcp-wrapper.log" 2>&1 &
          pids+=("$!")
        done
      else
        for node in $(seq 1 64); do
          timeout 10800 kathara exec -d "${LAB_DIR}" --wait "node${node}" python3 \
            "/shared/${SUITE}/cache_probe.py" "${node}" "/shared/${SUITE}/data/node${node}.tsv" \
            "/shared/${SUITE}/control" >"${RUN_DIR}/logs/node${node}.cache-probe.log" 2>&1 &
          pids+=("$!")
        done
      fi
      set +e
      controller_args=(--method "${METHOD}")
      [[ "${METHOD}" == tcp-direct ]] && controller_args+=(--skip-cache-probes)
      python3 "${SCRIPT_DIR}/federation_v3_controller.py" --schedule "${INPUT}/schedule.tsv" \
        --isolation-probes "${INPUT}/isolation_probes.tsv" \
        --control-dir "${SHARED}/control" --timeout-ms "${CONTROLLER_TIMEOUT_MS}" \
        --quiescence-timeout-ms 20000 --stable-ms 500 --output "${RUN_DIR}/request_results.csv" \
        "${controller_args[@]}" \
        >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log"
      CONTROLLER_STATUS=$?
      for pid in "${pids[@]:-}"; do [[ -n "${pid}" ]] && wait "${pid}" || true; done
      set -e
      STATUS=$([[ ${CONTROLLER_STATUS} -eq 0 ]] && echo ok || echo incomplete)
    fi
  fi

  find "${LAB_DIR}/shared/logs" -maxdepth 1 -name '*.log' -exec cp {} "${RUN_DIR}/logs/" \; 2>/dev/null || true
  find "${LAB_DIR}/shared/traces" -maxdepth 1 -name '*.jsonl' -exec cp {} "${RUN_DIR}/traces/" \; 2>/dev/null || true
  mkdir -p "${RUN_DIR}/activation_acks"
  find "${SHARED}/control/activated" -maxdepth 1 -name '*.tsv' -exec cp {} "${RUN_DIR}/activation_acks/" \; 2>/dev/null || true
  [[ ! -d "${LAB_DIR}/shared/generated" ]] || cp -a "${LAB_DIR}/shared/generated" "${RUN_DIR}/generated_network"

  if [[ "${STATUS}" != driver_failed && "${METHOD}" != tcp-direct ]]; then
    python3 "${SCRIPT_DIR}/analyze_federation_v3_bf1024_h3_fallback_trace.py" \
      --results "${RUN_DIR}/request_results.csv" --traces "${RUN_DIR}/traces" \
      --request-frame-bytes "${REQUEST_TCP_FRAME_BYTES}" --output "${RUN_DIR}/trace_breakdown.csv" || STATUS=analysis_failed
  fi
  cp "${RUN_DIR}/activation.json" "${RUN_DIR}/activation_runtime.json" 2>/dev/null || true

  if [[ "${STATUS}" != driver_failed ]]; then
    rm -rf "${SHARED}/clock-probe"
    mkdir -p "${SHARED}/clock-probe"
    clock_pids=()
    for node in $(seq 1 64); do
      kathara exec -d "${LAB_DIR}" --wait "node${node}" python3 "/shared/${SUITE}/clock_probe.py" \
        "${node}" "/shared/${SUITE}/clock-probe" >/dev/null 2>&1 &
      clock_pids+=("$!")
    done
    sleep 1
    touch "${SHARED}/clock-probe/go"
    for pid in "${clock_pids[@]}"; do wait "${pid}" || true; done
    {
      echo "node,monotonic_ns,timens_offsets_sha256"
      for node in $(seq 1 64); do
        probe=$(tr -d '\r\n' <"${SHARED}/clock-probe/node${node}.txt" 2>/dev/null || true)
        [[ -n "${probe}" ]] && echo "${node},${probe//$'\t'/,}"
      done
    } >"${RUN_DIR}/clock_state.tsv"
    {
      for node in $(seq 1 64) $(seq 101 108) $(seq 201 204) $(seq 301 308) $(seq 401 404); do
        echo "## node${node}"
        kathara exec -d "${LAB_DIR}" --wait "node${node}" /bin/sh "/shared/${SUITE}/inspect_network.sh" || true
      done
    } >"${RUN_DIR}/network_state.txt" 2>&1
  fi
fi

FINISHED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [[ -f "${RUN_DIR}/runtime_image_id.before_launch" ]]; then
  RUNTIME_IMAGE_ID=$(tr -d '\r\n' <"${RUN_DIR}/runtime_image_id.before_launch")
else
  RUNTIME_IMAGE_ID=unavailable
fi
REQUIRED_GIT_ANCESTOR=${FEDERATION_FALLBACK_REQUIRED_GIT_ANCESTOR:-}
DATASET_LABEL=${FEDERATION_FALLBACK_DATASET_LABEL:-pre-fix-diagnostic}
python3 - "${RUN_DIR}/metadata.json" "${SCENARIO}" "${STATUS}" "${CONTROLLER_STATUS}" \
  "${STARTED_AT}" "${FINISHED_AT}" "${SCHEDULE_HASH}" "${GIT_COMMIT}" "${GIT_DIRTY}" \
  "${GIT_SNAPSHOT_AT}" "${REQUIRED_GIT_ANCESTOR}" "${DATASET_LABEL}" "${METHOD}" \
  "${GIT_DIRTY_STATE_SHA256}" "${GIT_DIRTY_PATCH_SHA256}" "${RUNTIME_IMAGE_ID}" "${SUITE}" <<'PY'
import json
import sys

(path, scenario, status, controller_status, started, finished, schedule_hash,
 commit, dirty, git_snapshot_at, required_git_ancestor, dataset_label, method,
 dirty_state_sha256, dirty_patch_sha256, runtime_image_id, suite) = sys.argv[1:]
metadata = {
    "suite": suite,
    "scenario": scenario,
    "method": method,
    "status": status,
    "controller_status": int(controller_status),
    "started_at": started,
    "finished_at": finished,
    "wire_version": 3,
    "staged_fallback": True,
    "fallback_delay_ms": 1500,
    "query_table_timeout_ms": 7000,
    "controller_timeout_ms": 8000,
    "cache_backend": "none" if method == "tcp-direct" else "redis",
    "redis_maxmemory": 0,
    "redis_maxmemory_policy": "noeviction",
    "host_count": 64,
    "cluster_size": 32,
    "value_size_bytes": 65536,
    "link_delay_ms": 2,
    "link_rate_mbit": 1000,
    "link_queue_packets": 1000,
    "mtu": 1500,
    "node_loop_sleep_ms": 25,
    "bloom_bits": 1024,
    "bloom_hash_count": 3,
    "request_codec_frame_bytes_single_signature": 162,
    "request_tcp_frame_bytes_single_signature": 166,
    "max_hop_count": 12,
    "schedule_sha256": schedule_hash,
    "git_commit": commit,
    "git_dirty": dirty == "true",
    "git_dirty_state_sha256": dirty_state_sha256,
    "git_dirty_patch_sha256": dirty_patch_sha256,
    "git_snapshot_at": git_snapshot_at,
    "git_snapshot_phase": "before_kathara_launch",
    "required_git_ancestor": required_git_ancestor,
    "dataset_label": dataset_label,
    "runtime_image_id": runtime_image_id,
    "runtime_image_snapshot_phase": "after_build_before_kathara_launch",
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(metadata, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
if [[ "${STATUS}" != ok ]]; then
  python3 "${SCRIPT_DIR}/diagnose_federation_v3_bf1024_h3_fallback.py" --run-dir "${RUN_DIR}" || true
fi
echo "${STATUS} ${RUN_DIR}"
[[ "${STATUS}" == ok ]]
