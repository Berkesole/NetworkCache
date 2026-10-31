#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd -- "${EXP_DIR}/../../.." && pwd)
LAB_DIR="${REPO_ROOT}/kathara/ruikekkk"
DEFAULT_RESULTS_ROOT="${EXP_DIR}/results"
RESULTS_ROOT="${RESULTS_ROOT:-${DEFAULT_RESULTS_ROOT}}"

usage() {
  cat <<'USAGE'
Usage: run_oracle_nc_unicast_once.sh <scenario>

Supported scenarios:
  kua-dc-v3-16-zipf-distanceblock-s2500-seed001
  kua-dc-v3-32-zipf-distanceblock-s2500-seed001
  kua-dc-v3-16-convergence-controlled-s2000-seed001
  kua-dc-v3-32-convergence-controlled-s2000-seed001
  kua-dc-v3-16-convergence-zipf-s2000-seed001
  kua-dc-v3-32-convergence-zipf-s2000-seed001
  kua-dc-v3-16-convergence-zipf-long-s2000-seed001
  kua-dc-v3-32-convergence-zipf-long-s2000-seed001
  kua-dc-v3-{16,32}-latency-v2-*-seed00{1..5}

Runs rsync-node-dev in RSYNC_ORACLE_UNICAST mode: requester nodes use
metadata-provided owner hints to send NetworkCache REQUEST packets directly to
the provider. Leaf/spine nodes remain pure forwarding devices.
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
  kua-dc-v3-16-zipf-distanceblock-s2500-seed001) ;;
  kua-dc-v3-32-zipf-distanceblock-s2500-seed001) ;;
  kua-dc-v3-16-convergence-controlled-s2000-seed001) ;;
  kua-dc-v3-32-convergence-controlled-s2000-seed001) ;;
  kua-dc-v3-16-convergence-zipf-s2000-seed001) ;;
  kua-dc-v3-32-convergence-zipf-s2000-seed001) ;;
  kua-dc-v3-16-convergence-zipf-long-s2000-seed001) ;;
  kua-dc-v3-32-convergence-zipf-long-s2000-seed001) ;;
  kua-dc-v3-16-latency-v2-*) ;;
  kua-dc-v3-32-latency-v2-*) ;;
  *)
    usage >&2
    fail "unsupported oracle NetworkCache unicast scenario: ${SCENARIO}"
    ;;
esac

require_cmd kathara
require_cmd docker
require_cmd python3

SCENARIO_FILE="${LAB_DIR}/scenarios/${SCENARIO}.yaml"
SCHEDULE_METADATA_FILE="${EXP_DIR}/workloads/kua-dc-v3/generated/${SCENARIO}.metadata.json"
[[ -f "${SCENARIO_FILE}" ]] || fail "missing scenario file: ${SCENARIO_FILE}"
[[ -f "${SCHEDULE_METADATA_FILE}" ]] || fail "missing schedule metadata: ${SCHEDULE_METADATA_FILE}"

mapfile -t META < <(python3 - "${SCHEDULE_METADATA_FILE}" <<'PY'
import json
import sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
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
    "spacing_loop_sleep_ms",
    "topology",
]:
    print(data.get(key, ""))
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
TOPOLOGY="${META[11]}"
NODE_LOOP_SLEEP_MS="${KATHARA_NODE_LOOP_SLEEP_MS:-${METADATA_LOOP_SLEEP_MS}}"
KATHARA_WAIT_SECONDS="${KATHARA_WAIT_SECONDS:-$(( (SCENARIO_TICKS * NODE_LOOP_SLEEP_MS + 999) / 1000 + 25 ))}"

GIT_SHA=$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)
GIT_COMMIT=$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)
RUN_ID="${RUN_ID:-oracle-nc-unicast-${SCENARIO}-$(date -u +%Y%m%dT%H%M%SZ)-${GIT_SHA}}"
RUN_DIR="${RESULTS_ROOT}/${RUN_ID}"
LOGS_DIR="${RUN_DIR}/logs"
TRACES_DIR="${RUN_DIR}/traces"
ORACLE_UNICAST_SHARED_DIR="${LAB_DIR}/shared/oracle_unicast"
mkdir -p "${LOGS_DIR}"
if [[ -d "${LAB_DIR}/shared/logs" ]]; then
  find "${LAB_DIR}/shared/logs" -maxdepth 1 -type f -name '*.log' -delete
fi
if [[ -d "${LAB_DIR}/shared/traces" ]]; then
  find "${LAB_DIR}/shared/traces" -maxdepth 1 -type f -name '*.jsonl' -delete
fi

rm -rf "${ORACLE_UNICAST_SHARED_DIR}"
python3 "${SCRIPT_DIR}/prepare_oracle_unicast_inputs.py" \
  --metadata "${SCHEDULE_METADATA_FILE}" \
  --output-dir "${ORACLE_UNICAST_SHARED_DIR}" \
  >"${RUN_DIR}/oracle_nc_unicast_prepare.log"

cp "${SCENARIO_FILE}" "${RUN_DIR}/topology.yaml"
cp "${SCHEDULE_METADATA_FILE}" "${RUN_DIR}/schedule_metadata.json"
cp "${ORACLE_UNICAST_SHARED_DIR}/oracle_nc_unicast_schedule.csv" "${RUN_DIR}/oracle_nc_unicast_schedule.csv"
cp "${ORACLE_UNICAST_SHARED_DIR}/oracle_nc_unicast_metadata.json" "${RUN_DIR}/oracle_nc_unicast_metadata.json"
cat >"${RUN_DIR}/metadata.json" <<METAJSON
{
  "run_id": "${RUN_ID}",
  "scenario": "${SCENARIO}",
  "env": "kathara-oracle-nc-unicast",
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
  "node_loop_sleep_ms": "${NODE_LOOP_SLEEP_MS}",
  "kathara_wait_seconds": "${KATHARA_WAIT_SECONDS}",
  "baseline": "oracle-nc-unicast",
  "trace_enabled": "${RSYNC_EVENT_TRACE:-0}",
  "trace_flush_every": "${RSYNC_EVENT_TRACE_FLUSH_EVERY:-64}",
  "trace_clock": "monotonic_ns",
  "note": "NetworkCache REQUEST/DATA baseline with oracle-provided provider hints; no flooding, SyncTable search, or aggregate flush expected."
}
METAJSON

set +e
env \
  BUILD_NO_CACHE="${BUILD_NO_CACHE:-0}" \
  SKIP_BUILD="${SKIP_BUILD:-0}" \
  ENABLE_REDIS="${ENABLE_REDIS:-OFF}" \
  FETCH_REDIS_DEPS="${FETCH_REDIS_DEPS:-OFF}" \
  KATHARA_NODE_LOOP_SLEEP_MS="${NODE_LOOP_SLEEP_MS}" \
  KATHARA_LINK_DELAY_MS="${KATHARA_LINK_DELAY_MS:-2}" \
  RSYNC_ORACLE_UNICAST=1 \
  RSYNC_EVENT_TRACE="${RSYNC_EVENT_TRACE:-0}" \
  RSYNC_EVENT_TRACE_FLUSH_EVERY="${RSYNC_EVENT_TRACE_FLUSH_EVERY:-64}" \
  RSYNC_EVENT_TRACE_RUN_ID="${RUN_ID}" \
  RSYNC_ORACLE_UNICAST_SCHEDULE_DIR=/shared/oracle_unicast \
  "${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}" \
  >"${RUN_DIR}/driver.stdout.log" 2>"${RUN_DIR}/driver.stderr.log"
driver_status=$?
set -e

if [[ "${driver_status}" -ne 0 ]]; then
  if [[ -d "${LAB_DIR}/shared/logs" ]]; then
    find "${LAB_DIR}/shared/logs" -maxdepth 1 -type f -name 'node*.log' -exec cp {} "${LOGS_DIR}/" \;
  fi
  if [[ "${RSYNC_EVENT_TRACE:-0}" != "0" && "${RSYNC_EVENT_TRACE:-0}" != "false" && "${RSYNC_EVENT_TRACE:-0}" != "FALSE" ]]; then
    mkdir -p "${TRACES_DIR}"
    if [[ -d "${LAB_DIR}/shared/traces" ]]; then
      find "${LAB_DIR}/shared/traces" -maxdepth 1 -type f -name '*.jsonl' -exec cp {} "${TRACES_DIR}/" \;
    fi
  fi
  "${REPO_ROOT}/scripts/kathara/down_ruikekkk_scenario.sh" >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || true
  echo "Kathara oracle NetworkCache unicast start failed; retained logs in ${RUN_DIR}" >&2
  exit "${driver_status}"
fi

echo "waiting ${KATHARA_WAIT_SECONDS}s for oracle NetworkCache unicast scenario" >>"${RUN_DIR}/driver.stdout.log"
sleep "${KATHARA_WAIT_SECONDS}"

find "${LAB_DIR}/shared/logs" -maxdepth 1 -type f -name 'node*.log' -exec cp {} "${LOGS_DIR}/" \;
if [[ "${RSYNC_EVENT_TRACE:-0}" != "0" && "${RSYNC_EVENT_TRACE:-0}" != "false" && "${RSYNC_EVENT_TRACE:-0}" != "FALSE" ]]; then
  mkdir -p "${TRACES_DIR}"
  if [[ -d "${LAB_DIR}/shared/traces" ]]; then
    find "${LAB_DIR}/shared/traces" -maxdepth 1 -type f -name '*.jsonl' -exec cp {} "${TRACES_DIR}/" \;
  fi
fi
"${REPO_ROOT}/scripts/kathara/down_ruikekkk_scenario.sh" >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || \
  echo "warning: failed to clean Kathara ruikekkk lab" >&2

python3 "${SCRIPT_DIR}/analyze_logs.py" \
  --logs-dir "${LOGS_DIR}" \
  --output "${RUN_DIR}/summary.csv" \
  --scenario "${SCENARIO}" \
  --env kathara-oracle-nc-unicast \
  --topology "${TOPOLOGY}" \
  --topology-file "${RUN_DIR}/topology.yaml" \
  --schedule-metadata "${RUN_DIR}/schedule_metadata.json"

if [[ -d "${TRACES_DIR}" ]] && find "${TRACES_DIR}" -maxdepth 1 -type f -name '*.jsonl' | grep -q .; then
  python3 "${SCRIPT_DIR}/analyze_trace_events.py" \
    --traces-dir "${TRACES_DIR}" \
    --summary-output "${RUN_DIR}/trace_summary.csv" \
    --breakdown-output "${RUN_DIR}/trace_breakdown.csv" \
    --by-class-output "${RUN_DIR}/trace_breakdown_by_class.csv" \
    --scenario "${SCENARIO}" \
    --env kathara-oracle-nc-unicast \
    --topology "${TOPOLOGY}" \
    --topology-file "${RUN_DIR}/topology.yaml" \
    --schedule-metadata "${RUN_DIR}/schedule_metadata.json"
fi

echo "run directory: ${RUN_DIR}"
echo "summary: ${RUN_DIR}/summary.csv"
