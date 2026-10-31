#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
DEFAULT_RESULTS_ROOT="${EXP_DIR}/results"
RESULTS_ROOT="${RESULTS_ROOT:-${DEFAULT_RESULTS_ROOT}}"
DEFAULT_SCENARIOS="kua-dc-v3-16-convergence-controlled-s2000-seed001,kua-dc-v3-32-convergence-controlled-s2000-seed001"

usage() {
  cat <<'USAGE'
Usage: run_oracle_nc_unicast_batch.sh [options]

Options:
  --scenarios <csv>       Comma-separated scenarios.
  --repeats <n>           Repetitions per scenario. Default: 20.
  --run-group-id <id>     Batch directory name under results/.
  --overwrite             Remove an existing batch directory before running.
  --allow-incomplete      Exit 0 even when some runs are not ok.
  --help                  Show this help.

Environment passed through to run_oracle_nc_unicast_once.sh:
  KATHARA_WAIT_SECONDS, KATHARA_NODE_LOOP_SLEEP_MS, KATHARA_LINK_DELAY_MS,
  BUILD_NO_CACHE, ENABLE_REDIS, FETCH_REDIS_DEPS, RSYNC_EVENT_TRACE
USAGE
}

SCENARIOS_CSV="${DEFAULT_SCENARIOS}"
REPEATS=20
RUN_GROUP_ID=""
ALLOW_INCOMPLETE=0
OVERWRITE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenarios)
      SCENARIOS_CSV="${2:-}"
      shift 2
      ;;
    --repeats)
      REPEATS="${2:-}"
      shift 2
      ;;
    --run-group-id)
      RUN_GROUP_ID="${2:-}"
      shift 2
      ;;
    --overwrite)
      OVERWRITE=1
      shift
      ;;
    --allow-incomplete)
      ALLOW_INCOMPLETE=1
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "${REPEATS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--repeats must be a positive integer" >&2
  exit 2
fi
if [[ -z "${RUN_GROUP_ID}" ]]; then
  RUN_GROUP_ID="oracle-nc-convergence-controlled-kathara-n${REPEATS}"
fi

BATCH_DIR="${RESULTS_ROOT}/${RUN_GROUP_ID}"
RUNS_DIR="${BATCH_DIR}/runs"
MANIFEST="${BATCH_DIR}/manifest.csv"
if [[ -e "${MANIFEST}" && "${OVERWRITE}" != "1" ]]; then
  echo "refusing to overwrite existing batch manifest: ${MANIFEST}" >&2
  echo "use --overwrite to remove and recreate this batch directory" >&2
  exit 2
fi
if [[ -d "${BATCH_DIR}" && "${OVERWRITE}" == "1" ]]; then
  rm -rf "${BATCH_DIR}"
fi
mkdir -p "${RUNS_DIR}"

csv_escape() {
  local value="$1"
  value="${value//\"/\"\"}"
  printf '"%s"' "${value}"
}

write_manifest_header() {
  printf 'run_id,scenario,repeat_index,status,summary_path,metadata_path,started_at,finished_at,workload_id,workload_type,topology,seed,request_count,key_count,provider_count,requester_count,phase_policy\n' > "${MANIFEST}"
}

append_manifest_row() {
  local run_id="$1"
  local scenario="$2"
  local repeat_index="$3"
  local status="$4"
  local summary_path="$5"
  local metadata_path="$6"
  local started_at="$7"
  local finished_at="$8"
  local workload_id="$9"
  local workload_type="${10}"
  local topology="${11}"
  local seed="${12}"
  local request_count="${13}"
  local key_count="${14}"
  local provider_count="${15}"
  local requester_count="${16}"
  local phase_policy="${17}"
  {
    csv_escape "${run_id}"; printf ','
    csv_escape "${scenario}"; printf ','
    csv_escape "${repeat_index}"; printf ','
    csv_escape "${status}"; printf ','
    csv_escape "${summary_path}"; printf ','
    csv_escape "${metadata_path}"; printf ','
    csv_escape "${started_at}"; printf ','
    csv_escape "${finished_at}"; printf ','
    csv_escape "${workload_id}"; printf ','
    csv_escape "${workload_type}"; printf ','
    csv_escape "${topology}"; printf ','
    csv_escape "${seed}"; printf ','
    csv_escape "${request_count}"; printf ','
    csv_escape "${key_count}"; printf ','
    csv_escape "${provider_count}"; printf ','
    csv_escape "${requester_count}"; printf ','
    csv_escape "${phase_policy}"; printf '\n'
  } >> "${MANIFEST}"
}

read_workload_metadata() {
  local scenario="$1"
  local metadata_file="${EXP_DIR}/workloads/kua-dc-v3/generated/${scenario}.metadata.json"
  if [[ ! -f "${metadata_file}" ]]; then
    printf '\n\n\n\n\n\n\n\n\n'
    return
  fi
  python3 - "${metadata_file}" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for key in [
    "workload_id",
    "workload_type",
    "topology",
    "seed",
    "request_count",
    "key_count",
    "provider_count",
    "requester_count",
    "phase_policy",
]:
    print("" if data.get(key) is None else data.get(key))
PY
}

classify_summary() {
  python3 "${SCRIPT_DIR}/aggregate_summaries.py" --classify-summary "$1"
}

classify_run() {
  local run_dir="$1"
  if [[ "${WORKLOAD_TYPE}" == latency-v2-* ]]; then
    python3 "${SCRIPT_DIR}/classify_latency_v2_run.py" "${run_dir}"
  else
    classify_summary "${run_dir}/summary.csv"
  fi
}

IFS=',' read -r -a SCENARIOS <<< "${SCENARIOS_CSV}"
write_manifest_header

FIRST_RUN=1
NON_OK=0
TOTAL_RUNS=0
export RSYNC_EVENT_TRACE="${RSYNC_EVENT_TRACE:-1}"

for raw_scenario in "${SCENARIOS[@]}"; do
  scenario=$(printf '%s' "${raw_scenario}" | xargs)
  if [[ -z "${scenario}" ]]; then
    continue
  fi
  mapfile -t WORKLOAD_META < <(read_workload_metadata "${scenario}")
  WORKLOAD_ID="${WORKLOAD_META[0]:-}"
  WORKLOAD_TYPE="${WORKLOAD_META[1]:-}"
  WORKLOAD_TOPOLOGY="${WORKLOAD_META[2]:-}"
  WORKLOAD_SEED="${WORKLOAD_META[3]:-}"
  WORKLOAD_REQUEST_COUNT="${WORKLOAD_META[4]:-}"
  WORKLOAD_KEY_COUNT="${WORKLOAD_META[5]:-}"
  WORKLOAD_PROVIDER_COUNT="${WORKLOAD_META[6]:-}"
  WORKLOAD_REQUESTER_COUNT="${WORKLOAD_META[7]:-}"
  WORKLOAD_PHASE_POLICY="${WORKLOAD_META[8]:-}"
  for repeat_index in $(seq 1 "${REPEATS}"); do
    run_id="${scenario}-r${repeat_index}"
    run_dir="${RUNS_DIR}/${run_id}"
    summary_path="runs/${run_id}/summary.csv"
    metadata_path="runs/${run_id}/metadata.json"
    started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    mkdir -p "${run_dir}"

    skip_build=1
    if [[ "${FIRST_RUN}" == "1" ]]; then
      skip_build=0
      FIRST_RUN=0
    fi

    echo "running oracle-nc-unicast scenario=${scenario} repeat=${repeat_index}/${REPEATS} skip_build=${skip_build}"
    driver_status=0
    RUN_ID="${run_id}" \
      RESULTS_ROOT="${RUNS_DIR}" \
      SKIP_BUILD="${skip_build}" \
      "${SCRIPT_DIR}/run_oracle_nc_unicast_once.sh" "${scenario}" \
      > "${run_dir}/batch.stdout.log" \
      2> "${run_dir}/batch.stderr.log" || driver_status=$?

    if [[ "${driver_status}" != "0" ]]; then
      status="driver_failed"
    else
      status=$(classify_run "${RUNS_DIR}/${run_id}")
    fi

    finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    append_manifest_row \
      "${run_id}" \
      "${scenario}" \
      "${repeat_index}" \
      "${status}" \
      "${summary_path}" \
      "${metadata_path}" \
      "${started_at}" \
      "${finished_at}" \
      "${WORKLOAD_ID}" \
      "${WORKLOAD_TYPE}" \
      "${WORKLOAD_TOPOLOGY}" \
      "${WORKLOAD_SEED}" \
      "${WORKLOAD_REQUEST_COUNT}" \
      "${WORKLOAD_KEY_COUNT}" \
      "${WORKLOAD_PROVIDER_COUNT}" \
      "${WORKLOAD_REQUESTER_COUNT}" \
      "${WORKLOAD_PHASE_POLICY}"

    TOTAL_RUNS=$((TOTAL_RUNS + 1))
    if [[ "${status}" != "ok" ]]; then
      NON_OK=$((NON_OK + 1))
      echo "run ${run_id} completed with status=${status}" >&2
    fi
  done
done

python3 "${SCRIPT_DIR}/aggregate_summaries.py" "${BATCH_DIR}"
python3 "${SCRIPT_DIR}/aggregate_trace_breakdowns.py" "${BATCH_DIR}"
python3 "${SCRIPT_DIR}/aggregate_convergence.py" "${BATCH_DIR}"

echo "batch directory: ${BATCH_DIR}"
echo "manifest: ${MANIFEST}"
echo "merged: ${BATCH_DIR}/merged.csv"
echo "trace breakdown: ${BATCH_DIR}/trace_breakdown_merged.csv"
echo "convergence: ${BATCH_DIR}/convergence_merged.csv"
echo "runs: ${TOTAL_RUNS}, non_ok: ${NON_OK}"

if [[ "${NON_OK}" != "0" && "${ALLOW_INCOMPLETE}" != "1" ]]; then
  exit 1
fi
