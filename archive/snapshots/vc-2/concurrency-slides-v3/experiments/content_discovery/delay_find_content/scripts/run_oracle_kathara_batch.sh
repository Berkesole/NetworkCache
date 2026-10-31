#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
DEFAULT_RESULTS_ROOT="${EXP_DIR}/results"
RESULTS_ROOT="${RESULTS_ROOT:-${DEFAULT_RESULTS_ROOT}}"
DEFAULT_SCENARIOS="kua-dc-v3-16-zipf-distanceblock-s2500-seed001,kua-dc-v3-32-zipf-distanceblock-s2500-seed001"

usage() {
  cat <<'USAGE'
Usage: run_oracle_kathara_batch.sh [options]

Options:
  --scenarios <csv>       Comma-separated oracle scenarios.
  --repeats <n>           Repetitions per scenario. Default: 20.
  --run-group-id <id>     Batch directory name under results/.
  --overwrite             Remove an existing batch directory before running.
  --allow-incomplete      Exit 0 even when some runs are not ok.
  --help                  Show this help.
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
  RUN_GROUP_ID="oracle-distanceblock-kathara-n${REPEATS}"
fi

BATCH_DIR="${RESULTS_ROOT}/${RUN_GROUP_ID}"
RUNS_DIR="${BATCH_DIR}/runs"
MANIFEST="${BATCH_DIR}/manifest.csv"
if [[ -e "${MANIFEST}" && "${OVERWRITE}" != "1" ]]; then
  echo "refusing to overwrite existing oracle batch manifest: ${MANIFEST}" >&2
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
  printf 'run_id,scenario,repeat_index,status,summary_path,metadata_path,started_at,finished_at,workload_id,workload_type,topology,seed,request_count,key_count,provider_count,requester_count,phase_policy\n' >"${MANIFEST}"
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
  } >>"${MANIFEST}"
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
  local summary="$1"
  python3 - "${summary}" <<'PY'
import csv
import sys
from pathlib import Path
path = Path(sys.argv[1])
if not path.exists():
    print("empty")
    raise SystemExit(0)
rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
if not rows:
    print("empty")
    raise SystemExit(0)
measurement = [row for row in rows if row.get("phase") == "measurement"]
checked = measurement if measurement else rows
def ok(row):
    try:
        return row.get("status") == "ok" and float(row.get("oracle_fetch_latency_ns", "")) >= 0
    except ValueError:
        return False
print("ok" if all(ok(row) for row in checked) else "incomplete")
PY
}

IFS=',' read -r -a SCENARIOS <<<"${SCENARIOS_CSV}"
write_manifest_header
non_ok=0
run_counter=0

for scenario in "${SCENARIOS[@]}"; do
  scenario="$(printf '%s' "${scenario}" | xargs)"
  [[ -n "${scenario}" ]] || continue
  mapfile -t meta < <(read_workload_metadata "${scenario}")
  workload_id="${meta[0]:-}"
  workload_type="${meta[1]:-}"
  topology="${meta[2]:-}"
  seed="${meta[3]:-}"
  request_count="${meta[4]:-}"
  key_count="${meta[5]:-}"
  provider_count="${meta[6]:-}"
  requester_count="${meta[7]:-}"
  phase_policy="${meta[8]:-}"

  for repeat in $(seq 1 "${REPEATS}"); do
    run_counter=$((run_counter + 1))
    run_id="${scenario}-oracle-r${repeat}"
    started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "==> oracle run ${run_id}"
    set +e
    if [[ -n "${SKIP_BUILD:-}" ]]; then
      run_skip_build="${SKIP_BUILD}"
    elif [[ "${run_counter}" -eq 1 ]]; then
      run_skip_build=0
    else
      run_skip_build=1
    fi
    RESULTS_ROOT="${RUNS_DIR}" \
      RUN_ID="${run_id}" \
      SKIP_BUILD="${run_skip_build}" \
      BUILD_NO_CACHE="${BUILD_NO_CACHE:-0}" \
      ENABLE_REDIS="${ENABLE_REDIS:-OFF}" \
      FETCH_REDIS_DEPS="${FETCH_REDIS_DEPS:-OFF}" \
      "${SCRIPT_DIR}/run_oracle_kathara_once.sh" "${scenario}"
    driver_status=$?
    set -e
    finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    run_dir="${RUNS_DIR}/${run_id}"
    summary_path="${run_dir}/oracle_summary.csv"
    if [[ "${driver_status}" -ne 0 ]]; then
      status="driver_failed"
    else
      status="$(classify_summary "${summary_path}")"
    fi
    if [[ "${status}" != "ok" ]]; then
      non_ok=$((non_ok + 1))
    fi

    append_manifest_row \
      "${run_id}" \
      "${scenario}" \
      "${repeat}" \
      "${status}" \
      "runs/${run_id}/oracle_summary.csv" \
      "runs/${run_id}/metadata.json" \
      "${started_at}" \
      "${finished_at}" \
      "${workload_id}" \
      "${workload_type}" \
      "${topology}" \
      "${seed}" \
      "${request_count}" \
      "${key_count}" \
      "${provider_count}" \
      "${requester_count}" \
      "${phase_policy}"
  done
done

python3 "${SCRIPT_DIR}/aggregate_oracle_summaries.py" "${BATCH_DIR}"

if [[ "${non_ok}" -ne 0 && "${ALLOW_INCOMPLETE}" != "1" ]]; then
  echo "oracle batch completed with ${non_ok} non-ok run(s): ${BATCH_DIR}" >&2
  exit 1
fi

echo "oracle batch directory: ${BATCH_DIR}"
