#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd -- "${EXP_DIR}/../../.." && pwd)
LAB_DIR="${REPO_ROOT}/kathara/ruikekkk"
ROOT="${EXP_DIR}/workloads/federation-v1"

[[ $# -eq 3 ]] || { echo "Usage: $0 <scenario> <nc|known-owner|tcp-direct> <new-run-dir>" >&2; exit 2; }
SCENARIO="$1"; METHOD="$2"; RUN_DIR="$3"
[[ "${METHOD}" =~ ^(nc|known-owner|tcp-direct)$ ]] || exit 2
[[ ! -e "${RUN_DIR}" ]] || { echo "run directory already exists: ${RUN_DIR}" >&2; exit 2; }
mkdir -p "${LAB_DIR}"
LOCK_DIR="${LAB_DIR}/.federation-v1.lock"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "federation-v1 lab is already owned; inspect ${LOCK_DIR}/owner and run the teardown script before retrying" >&2
  exit 3
fi
printf 'pid=%s\nrun_dir=%s\nstarted_at=%s\n' "$$" "${RUN_DIR}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${LOCK_DIR}/owner"
early_unlock() { rm -f "${LOCK_DIR}/owner"; rmdir "${LOCK_DIR}" 2>/dev/null || true; }
trap early_unlock EXIT
INPUT="${ROOT}/mapped/${SCENARIO}"
[[ -f "${INPUT}/schedule.tsv" && -f "${INPUT}/metadata.json" ]] || { echo "missing generated input ${SCENARIO}" >&2; exit 2; }
SCHEDULE_HASH=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["schedule_sha256"])' "${INPUT}/metadata.json")

mkdir -p "${RUN_DIR}/logs" "${RUN_DIR}/traces"
cp "${ROOT}/scenarios/${SCENARIO}.yaml" "${RUN_DIR}/scenario.yaml"
cp "${INPUT}/metadata.json" "${RUN_DIR}/schedule_metadata.json"
cp "${INPUT}/activation.json" "${RUN_DIR}/activation_spec.json"
cp "${INPUT}/schedule.tsv" "${RUN_DIR}/schedule.tsv"
SHARED="${LAB_DIR}/shared/federation-v1"
rm -rf "${SHARED}"; mkdir -p "${SHARED}/data" "${SHARED}/control"
mkdir -p "${LAB_DIR}/shared/logs" "${LAB_DIR}/shared/traces"
rm -f "${LAB_DIR}/shared/logs/"*.log "${LAB_DIR}/shared/traces/"*.jsonl
cp "${INPUT}/data/"node*.tsv "${SHARED}/data/"
cp "${SCRIPT_DIR}/latency_v3_tcp_node.py" "${SHARED}/tcp_node.py"
cp "${SCRIPT_DIR}/latency_v3_clock_probe.py" "${SHARED}/clock_probe.py"
cp "${SCRIPT_DIR}/federation_v1_inspect_network.sh" "${SHARED}/inspect_network.sh"

STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cleanup() {
  "${REPO_ROOT}/scripts/kathara/down_ruikekkk_scenario.sh" >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || true
  rm -f "${LOCK_DIR}/owner"
  rmdir "${LOCK_DIR}" 2>/dev/null || true
}
trap cleanup EXIT
set +e
env DOCKERFILE_PATH="${REPO_ROOT}/Dockerfile.latency-v3" BUILD_NO_CACHE="${FEDERATION_V1_BUILD_NO_CACHE:-1}" \
  SCENARIOS_DIR="${ROOT}/scenarios" ENABLE_REDIS=ON FETCH_REDIS_DEPS=ON \
  KATHARA_NODE_LOOP_SLEEP_MS=25 KATHARA_LINK_DELAY_MS=2 KATHARA_LINK_RATE_MBIT=1000 \
  KATHARA_LINK_QUEUE_PACKETS=1000 KATHARA_LINK_MTU=1500 \
  RSYNC_WIRE_VERSION=2 RSYNC_REDIS_TIMEOUT_MS=1000 RSYNC_REDIS_MAXMEMORY=0 \
  RSYNC_REDIS_MAXMEMORY_POLICY=noeviction RSYNC_REDIS_REQUIRE_EMPTY=1 \
  RSYNC_QUERY_TIMEOUT_MS=7000 RSYNC_LOOP_ITERATIONS=100000000 \
  RSYNC_SKIP_APP=$([[ "${METHOD}" == tcp-direct ]] && echo 1 || echo 0) \
  RSYNC_ORACLE_UNICAST=$([[ "${METHOD}" == known-owner ]] && echo 1 || echo 0) \
  RSYNC_FEDERATION_METHOD="${METHOD}" RSYNC_EVENT_TRACE=1 RSYNC_EVENT_TRACE_FLUSH_EVERY=64 \
  RSYNC_EVENT_TRACE_RUN_ID="$(basename "${RUN_DIR}")" \
  RSYNC_EXPERIMENT_CONTROL_DIR=/shared/federation-v1/control \
  RSYNC_EXPERIMENT_DATA_DIR=/shared/federation-v1/data \
  RSYNC_EXPERIMENT_SCHEDULE_HASH="${SCHEDULE_HASH}" \
  "${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}" \
  >"${RUN_DIR}/driver.stdout.log" 2>"${RUN_DIR}/driver.stderr.log"
START_STATUS=$?
set -e
STATUS=driver_failed; CONTROLLER_STATUS=99
if [[ ${START_STATUS} -eq 0 ]]; then
  startup_deadline=$((SECONDS + 180))
  while ((SECONDS < startup_deadline)); do
    ready_logs=0; ready_switches=0
    for node in $(seq 1 64); do
      if [[ "${METHOD}" == tcp-direct ]]; then
        grep -q 'network configured without redis or rsync-node-dev' "${LAB_DIR}/shared/logs/node${node}.startup.log" 2>/dev/null && ready_logs=$((ready_logs + 1))
      else
        grep -q 'started rsync-node-dev' "${LAB_DIR}/shared/logs/node${node}.startup.log" 2>/dev/null && ready_logs=$((ready_logs + 1))
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
    STATUS=driver_failed
    CONTROLLER_STATUS=98
  else
  # Allow routes and listeners to settle after the final startup acknowledgement.
  sleep 5
  pids=()
  if [[ "${METHOD}" == tcp-direct ]]; then
    for node in $(seq 1 64); do
      timeout 1800 kathara exec -d "${LAB_DIR}" --wait "node${node}" python3 \
        /shared/federation-v1/tcp_node.py "${node}" "/shared/federation-v1/data/node${node}.tsv" \
        /shared/federation-v1/control "${SCHEDULE_HASH}" >"${RUN_DIR}/logs/node${node}.tcp-wrapper.log" 2>&1 & pids+=("$!")
    done
  fi
  set +e
  python3 "${SCRIPT_DIR}/federation_v1_controller.py" --schedule "${INPUT}/schedule.tsv" \
    --control-dir "${SHARED}/control" --method "${METHOD}" --timeout-ms 8000 \
    --quiescence-timeout-ms 20000 --stable-ms 500 --output "${RUN_DIR}/request_results.csv" \
    >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log"
  CONTROLLER_STATUS=$?
  for pid in "${pids[@]:-}"; do [[ -n "${pid}" ]] && wait "${pid}" || true; done
  set -e
  STATUS=$([[ ${CONTROLLER_STATUS} -eq 0 ]] && echo ok || echo incomplete)
  fi
  find "${LAB_DIR}/shared/logs" -maxdepth 1 -name '*.log' -exec cp {} "${RUN_DIR}/logs/" \; 2>/dev/null || true
  find "${LAB_DIR}/shared/traces" -maxdepth 1 -name '*.jsonl' -exec cp {} "${RUN_DIR}/traces/" \; 2>/dev/null || true
  mkdir -p "${RUN_DIR}/activation_acks"
  find "${SHARED}/control/activated" -maxdepth 1 -name '*.tsv' -exec cp {} "${RUN_DIR}/activation_acks/" \; 2>/dev/null || true
  if [[ -d "${LAB_DIR}/shared/generated" ]]; then
    cp -a "${LAB_DIR}/shared/generated" "${RUN_DIR}/generated_network"
  fi
  if [[ "${STATUS}" != driver_failed && "${METHOD}" != tcp-direct ]]; then
    python3 "${SCRIPT_DIR}/analyze_federation_v1_trace.py" --results "${RUN_DIR}/request_results.csv" \
      --traces "${RUN_DIR}/traces" --output "${RUN_DIR}/trace_breakdown.csv" || STATUS=analysis_failed
  fi
  cp "${RUN_DIR}/activation.json" "${RUN_DIR}/activation_runtime.json" 2>/dev/null || true
  if [[ "${STATUS}" == driver_failed ]]; then
    FINISHED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    python3 -c 'import json,sys;json.dump({"scenario":sys.argv[2],"method":sys.argv[3],"status":"driver_failed","controller_status":int(sys.argv[4]),"started_at":sys.argv[5],"finished_at":sys.argv[6],"wire_version":2,"schedule_sha256":sys.argv[7]},open(sys.argv[1],"w"),indent=2,sort_keys=True)' \
      "${RUN_DIR}/metadata.json" "${SCENARIO}" "${METHOD}" "${CONTROLLER_STATUS}" "${STARTED_AT}" "${FINISHED_AT}" "${SCHEDULE_HASH}"
    echo "driver_failed ${RUN_DIR}"
    exit 1
  fi
  rm -rf "${SHARED}/clock-probe"; mkdir -p "${SHARED}/clock-probe"
  clock_pids=()
  for node in $(seq 1 64); do
    kathara exec -d "${LAB_DIR}" --wait "node${node}" python3 /shared/federation-v1/clock_probe.py \
      "${node}" /shared/federation-v1/clock-probe >/dev/null 2>&1 & clock_pids+=("$!")
  done
  sleep 1; touch "${SHARED}/clock-probe/go"
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
      echo "## node${node}"; kathara exec -d "${LAB_DIR}" --wait "node${node}" \
        /bin/sh /shared/federation-v1/inspect_network.sh || true
    done
  } >"${RUN_DIR}/network_state.txt" 2>&1
fi
FINISHED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
GIT_COMMIT=$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)
GIT_DIRTY=$([[ -n "$(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null)" ]] && echo true || echo false)
python3 -c 'import json,sys;json.dump({"scenario":sys.argv[2],"method":sys.argv[3],"status":sys.argv[4],"controller_status":int(sys.argv[5]),"started_at":sys.argv[6],"finished_at":sys.argv[7],"wire_version":2,"cache_backend":"redis","host_count":64,"cluster_size":32,"value_size_bytes":65536,"link_delay_ms":2,"link_rate_mbit":1000,"link_queue_packets":1000,"mtu":1500,"node_loop_sleep_ms":25,"query_table_timeout_ms":7000,"schedule_sha256":sys.argv[8],"git_commit":sys.argv[9],"git_dirty":sys.argv[10]=="true"},open(sys.argv[1],"w"),indent=2,sort_keys=True)' \
  "${RUN_DIR}/metadata.json" "${SCENARIO}" "${METHOD}" "${STATUS}" "${CONTROLLER_STATUS}" "${STARTED_AT}" "${FINISHED_AT}" "${SCHEDULE_HASH}" "${GIT_COMMIT}" "${GIT_DIRTY}"
echo "${STATUS} ${RUN_DIR}"
[[ "${STATUS}" == ok ]]
