#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd -- "${EXP_DIR}/../../.." && pwd)
LAB_DIR="${REPO_ROOT}/kathara/ruikekkk"
DEFAULT_RESULTS_ROOT="${EXP_DIR}/results"
RESULTS_ROOT="${RESULTS_ROOT:-${DEFAULT_RESULTS_ROOT}}"
ORACLE_PORT_BASE="${ORACLE_PORT_BASE:-47000}"
LOOPBACK_PREFIX="${LOOPBACK_PREFIX:-10.90.0}"

usage() {
  cat <<'USAGE'
Usage: run_oracle_kathara_once.sh <scenario>

Supported scenarios:
  kua-dc-v3-16-zipf-distanceblock-s2500-seed001
  kua-dc-v3-32-zipf-distanceblock-s2500-seed001

This runner starts the Kathara pure spine/leaf fabric without rsync-node-dev,
then runs oracle-direct-fetch on each host to measure known-location fetch delay.
USAGE
}

fail() {
  echo "error: $*" >&2
  exit 2
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

SCENARIO="${1:-}"
if [[ -z "${SCENARIO}" ]]; then
  usage >&2
  exit 2
fi

case "${SCENARIO}" in
  kua-dc-v3-16-zipf-distanceblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    ;;
  kua-dc-v3-32-zipf-distanceblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    ;;
  *)
    usage >&2
    fail "unsupported oracle scenario: ${SCENARIO}"
    ;;
esac

require_cmd kathara
require_cmd docker
require_cmd python3
require_cmd timeout

SCENARIO_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
SCHEDULE_METADATA_FILE="${EXP_DIR}/workloads/kua-dc-v3/generated/${SCENARIO}.metadata.json"
[[ -f "${SCENARIO_FILE}" ]] || fail "missing scenario file: ${SCENARIO_FILE}"
[[ -f "${SCHEDULE_METADATA_FILE}" ]] || fail "missing schedule metadata: ${SCHEDULE_METADATA_FILE}"

mapfile -t META < <(python3 - "${SCHEDULE_METADATA_FILE}" <<'PY'
import json
import sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
loop_sleep = int(data.get("spacing_loop_sleep_ms", 25))
scenario_ticks = int(data["scenario_ticks"])
for key in [
    "workload_id",
    "workload_type",
    "seed",
    "request_count",
    "key_count",
    "provider_count",
    "requester_count",
    "phase_policy",
    "host_count",
    "scenario_ticks",
]:
    print(data.get(key, ""))
print(loop_sleep)
PY
)

WORKLOAD_ID="${META[0]}"
WORKLOAD_TYPE="${META[1]}"
WORKLOAD_SEED="${META[2]}"
WORKLOAD_REQUEST_COUNT="${META[3]}"
WORKLOAD_KEY_COUNT="${META[4]}"
WORKLOAD_PROVIDER_COUNT="${META[5]}"
WORKLOAD_REQUESTER_COUNT="${META[6]}"
WORKLOAD_PHASE_POLICY="${META[7]}"
HOST_COUNT="${META[8]}"
SCENARIO_TICKS="${META[9]}"
METADATA_LOOP_SLEEP_MS="${META[10]}"
ORACLE_LOOP_SLEEP_MS="${ORACLE_LOOP_SLEEP_MS:-1}"
ORACLE_WAIT_SECONDS="${ORACLE_WAIT_SECONDS:-$(( (SCENARIO_TICKS * ORACLE_LOOP_SLEEP_MS + 999) / 1000 + 8 ))}"

GIT_SHA=$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)
GIT_COMMIT=$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)
RUN_ID="${RUN_ID:-oracle-${SCENARIO}-$(date -u +%Y%m%dT%H%M%SZ)-${GIT_SHA}}"
RUN_DIR="${RESULTS_ROOT}/${RUN_ID}"
LOGS_DIR="${RUN_DIR}/logs"
ORACLE_SHARED_DIR="${LAB_DIR}/shared/oracle"
mkdir -p "${LOGS_DIR}"
if [[ -d "${LAB_DIR}/shared/logs" ]]; then
  find "${LAB_DIR}/shared/logs" -maxdepth 1 -type f -name '*.log' -delete
fi

cp "${SCENARIO_FILE}" "${RUN_DIR}/topology.yaml"
cp "${SCHEDULE_METADATA_FILE}" "${RUN_DIR}/schedule_metadata.json"
cat >"${RUN_DIR}/metadata.json" <<METAJSON
{
  "run_id": "${RUN_ID}",
  "scenario": "${SCENARIO}",
  "env": "kathara-oracle",
  "topology": "${TOPOLOGY}",
  "workload_id": "${WORKLOAD_ID}",
  "workload_type": "${WORKLOAD_TYPE}",
  "workload_seed": "${WORKLOAD_SEED}",
  "workload_request_count": "${WORKLOAD_REQUEST_COUNT}",
  "workload_key_count": "${WORKLOAD_KEY_COUNT}",
  "workload_provider_count": "${WORKLOAD_PROVIDER_COUNT}",
  "workload_requester_count": "${WORKLOAD_REQUESTER_COUNT}",
  "workload_phase_policy": "${WORKLOAD_PHASE_POLICY}",
  "schedule_metadata": "${SCHEDULE_METADATA_FILE}",
  "git_commit": "${GIT_COMMIT}",
  "host_count": "${HOST_COUNT}",
  "scenario_ticks": "${SCENARIO_TICKS}",
  "metadata_loop_sleep_ms": "${METADATA_LOOP_SLEEP_MS}",
  "oracle_loop_sleep_ms": "${ORACLE_LOOP_SLEEP_MS}",
  "oracle_wait_seconds": "${ORACLE_WAIT_SECONDS}",
  "oracle_port_base": "${ORACLE_PORT_BASE}",
  "loopback_prefix": "${LOOPBACK_PREFIX}",
  "note": "Oracle direct fetch lower-bound baseline; rsync-node-dev is skipped and leaf/spine devices only forward packets."
}
METAJSON

set +e
env \
  BUILD_NO_CACHE="${BUILD_NO_CACHE:-0}" \
  SKIP_BUILD="${SKIP_BUILD:-0}" \
  ENABLE_REDIS="${ENABLE_REDIS:-OFF}" \
  FETCH_REDIS_DEPS="${FETCH_REDIS_DEPS:-OFF}" \
  KATHARA_NODE_LOOP_SLEEP_MS="${KATHARA_NODE_LOOP_SLEEP_MS:-25}" \
  KATHARA_LINK_DELAY_MS="${KATHARA_LINK_DELAY_MS:-2}" \
  RSYNC_SKIP_APP=1 \
  "${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}" \
  >"${RUN_DIR}/driver.stdout.log" 2>"${RUN_DIR}/driver.stderr.log"
driver_status=$?
set -e

if [[ "${driver_status}" -ne 0 ]]; then
  if [[ -d "${LAB_DIR}/shared/logs" ]]; then
    find "${LAB_DIR}/shared/logs" -maxdepth 1 -type f -name '*.oracle*.log' -exec cp {} "${LOGS_DIR}/" \;
  fi
  "${REPO_ROOT}/scripts/kathara/down_ruikekkk_scenario.sh" >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || true
  echo "Kathara oracle lab start failed; retained logs in ${RUN_DIR}" >&2
  exit "${driver_status}"
fi

rm -rf "${ORACLE_SHARED_DIR}"
python3 "${SCRIPT_DIR}/prepare_oracle_inputs.py" \
  --scenario-file "${SCENARIO_FILE}" \
  --metadata "${SCHEDULE_METADATA_FILE}" \
  --output-dir "${ORACLE_SHARED_DIR}" \
  --loopback-prefix "${LOOPBACK_PREFIX}" \
  --oracle-port-base "${ORACLE_PORT_BASE}" \
  --loop-sleep-ms "${ORACLE_LOOP_SLEEP_MS}" \
  >"${RUN_DIR}/oracle_prepare.log"
cp "${ORACLE_SHARED_DIR}/oracle_schedule.csv" "${RUN_DIR}/oracle_schedule.csv"
cp "${ORACLE_SHARED_DIR}/oracle_metadata.json" "${RUN_DIR}/oracle_metadata.json"

for node_id in $(seq 1 "${HOST_COUNT}"); do
  kathara exec -d "${LAB_DIR}" --wait "node${node_id}" true
done

oracle_pids=()
for node_id in $(seq 1 "${HOST_COUNT}"); do
  rm -f "${LAB_DIR}/shared/logs/node${node_id}.oracle.log" "${LAB_DIR}/shared/logs/node${node_id}.oracle.wrapper.log"
  timeout "${ORACLE_WAIT_SECONDS}" kathara exec -d "${LAB_DIR}" --wait "node${node_id}" \
    /bin/sh "/shared/oracle/node${node_id}.run.sh" \
    >"${LAB_DIR}/shared/logs/node${node_id}.oracle.wrapper.log" 2>&1 &
  oracle_pids+=("$!")
done

echo "waiting for oracle processes, timeout budget ${ORACLE_WAIT_SECONDS}s" >>"${RUN_DIR}/driver.stdout.log"
oracle_status=0
for pid in "${oracle_pids[@]}"; do
  if ! wait "${pid}"; then
    oracle_status=1
  fi
done
if [[ "${oracle_status}" -ne 0 ]]; then
  echo "warning: one or more oracle processes exited non-zero" >>"${RUN_DIR}/driver.stderr.log"
fi

find "${LAB_DIR}/shared/logs" -maxdepth 1 -type f -name '*.oracle*.log' -exec cp {} "${LOGS_DIR}/" \;
"${REPO_ROOT}/scripts/kathara/down_ruikekkk_scenario.sh" >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || \
  echo "warning: failed to clean Kathara ruikekkk lab" >&2

python3 "${SCRIPT_DIR}/analyze_oracle_logs.py" \
  --logs-dir "${LOGS_DIR}" \
  --schedule "${RUN_DIR}/oracle_schedule.csv" \
  --output "${RUN_DIR}/oracle_summary.csv" \
  --scenario "${SCENARIO}" \
  --env kathara-oracle \
  --topology "${TOPOLOGY}" \
  --workload-id "${WORKLOAD_ID}" \
  --workload-type "${WORKLOAD_TYPE}" \
  --seed "${WORKLOAD_SEED}"

echo "run directory: ${RUN_DIR}"
echo "oracle summary: ${RUN_DIR}/oracle_summary.csv"
