#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd -- "${EXP_DIR}/../../.." && pwd)
RESULTS_DIR="${EXP_DIR}/results"
TARGET_RUNS="${TARGET_RUNS:-20}"
INITIAL_REPEATS="${INITIAL_REPEATS:-20}"
FILL_REPEATS="${FILL_REPEATS:-8}"
MAX_FILL_ATTEMPTS="${MAX_FILL_ATTEMPTS:-4}"

export RSYNC_EVENT_TRACE="${RSYNC_EVENT_TRACE:-1}"
export KATHARA_NODE_LOOP_SLEEP_MS="${KATHARA_NODE_LOOP_SLEEP_MS:-25}"

valid_success_batch() {
  local batch_dir="$1"
  [[ -f "${batch_dir}/manifest.csv" ]] || return 1
  python3 - "$batch_dir" "$TARGET_RUNS" <<'PY'
import csv
import json
import sys
from collections import Counter
from pathlib import Path

batch = Path(sys.argv[1])
target = int(sys.argv[2])
rows = list(csv.DictReader((batch / "manifest.csv").open(newline="", encoding="utf-8")))
if len(rows) != target or any(row.get("status") != "ok" for row in rows):
    raise SystemExit(1)
merged = batch / "convergence_merged.csv"
if not merged.exists():
    raise SystemExit(1)
conv = list(csv.DictReader(merged.open(newline="", encoding="utf-8")))
if any(row.get("trace_complete") != "1" for row in conv):
    raise SystemExit(1)
counts = Counter(row.get("run_id", "") for row in conv)
if set(counts) != {row.get("run_id", "") for row in rows}:
    raise SystemExit(1)

def resolve(value):
    path = Path(value)
    return path if path.is_absolute() else batch / path

def expected_measurement(row):
    metadata = resolve(row.get("metadata_path", ""))
    candidates = [metadata, metadata.with_name("schedule_metadata.json")]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        value = data.get("measurement_requests")
        if isinstance(value, int):
            return value
        requests = data.get("requests")
        if isinstance(requests, list):
            return sum(1 for request in requests if request.get("phase") == "measurement")
    raise SystemExit(1)

for row in rows:
    if counts[row.get("run_id", "")] != expected_measurement(row):
        raise SystemExit(1)
PY
}

wait_for_unit() {
  local unit="$1"
  if [[ -z "$unit" ]]; then
    return
  fi
  echo "waiting for systemd unit ${unit}"
  while systemctl is-active --quiet "${unit}"; do
    sleep 60
  done
}

collect_success() {
  local output="$1"
  local scenario="$2"
  shift 2
  python3 "${SCRIPT_DIR}/collect_success_runs.py" \
    --output "${RESULTS_DIR}/${output}" \
    --target-per-scenario "${TARGET_RUNS}" \
    --scenarios "${scenario}" \
    --overwrite \
    "$@"
}

try_collect() {
  local output="$1"
  local scenario="$2"
  shift 2
  if [[ -d "${RESULTS_DIR}/${output}" ]]; then
    if valid_success_batch "${RESULTS_DIR}/${output}"; then
      echo "success batch already valid: ${output}"
      return 0
    fi
    echo "existing success batch is incomplete or stale; rebuilding: ${output}" >&2
  fi
  collect_success "$output" "$scenario" "$@"
}

run_one() {
  local output="$1"
  local scenario="$2"
  local attempt_prefix="$3"
  local wait_unit="${4:-}"
  local attempts=()

  if valid_success_batch "${RESULTS_DIR}/${output}"; then
    echo "already complete: ${output}"
    return
  fi

  wait_for_unit "${wait_unit}"

  local existing
  while IFS= read -r existing; do
    attempts+=("${existing}")
  done < <(find "${RESULTS_DIR}" -maxdepth 1 -type d -name "${attempt_prefix}[0-9]*" | sort)

  if [[ ${#attempts[@]} -eq 0 ]]; then
    local attempt="${RESULTS_DIR}/${attempt_prefix}1"
    attempts+=("${attempt}")
    echo "running ${attempt_prefix}1 repeats=${INITIAL_REPEATS}"
    "${SCRIPT_DIR}/run_kathara_batch.sh" \
      --scenarios "${scenario}" \
      --repeats "${INITIAL_REPEATS}" \
      --allow-incomplete \
      --run-group-id "${attempt_prefix}1"
  fi

  if try_collect "${output}" "${scenario}" "${attempts[@]}"; then
    return
  fi

  local next_index=$(( ${#attempts[@]} + 1 ))
  local fill=1
  while [[ "${fill}" -le "${MAX_FILL_ATTEMPTS}" ]]; do
    local attempt="${RESULTS_DIR}/${attempt_prefix}${next_index}"
    attempts+=("${attempt}")
    echo "running ${attempt_prefix}${next_index} repeats=${FILL_REPEATS}"
    "${SCRIPT_DIR}/run_kathara_batch.sh" \
      --scenarios "${scenario}" \
      --repeats "${FILL_REPEATS}" \
      --allow-incomplete \
      --run-group-id "${attempt_prefix}${next_index}"
    if try_collect "${output}" "${scenario}" "${attempts[@]}"; then
      return
    fi
    next_index=$((next_index + 1))
    fill=$((fill + 1))
  done

  echo "failed to collect ${TARGET_RUNS} ok runs for ${scenario}" >&2
  return 1
}

main() {
  cd "${REPO_ROOT}"

  run_one \
    "convergence-controlled-16-kathara-success20" \
    "kua-dc-v3-16-convergence-controlled-s2000-seed001" \
    "convergence-controlled-16-kathara-attempts"

  run_one \
    "convergence-controlled-32-kathara-success20" \
    "kua-dc-v3-32-convergence-controlled-s2000-seed001" \
    "convergence-controlled-32-kathara-attempts" \
    "rsync-convergence-controlled-32-attempts1.service"

  run_one \
    "convergence-zipf-16-kathara-success20" \
    "kua-dc-v3-16-convergence-zipf-s2000-seed001" \
    "convergence-zipf-16-kathara-attempts"

  run_one \
    "convergence-zipf-32-kathara-success20" \
    "kua-dc-v3-32-convergence-zipf-s2000-seed001" \
    "convergence-zipf-32-kathara-attempts"

  run_one \
    "convergence-zipf-long-16-kathara-success20" \
    "kua-dc-v3-16-convergence-zipf-long-s2000-seed001" \
    "convergence-zipf-long-16-kathara-attempts"

  run_one \
    "convergence-zipf-long-32-kathara-success20" \
    "kua-dc-v3-32-convergence-zipf-long-s2000-seed001" \
    "convergence-zipf-long-32-kathara-attempts"

  run_one \
    "convergence-uniform-16-kathara-success20" \
    "kua-dc-v3-16-convergence-uniform-s2000-seed001" \
    "convergence-uniform-16-kathara-attempts"

  run_one \
    "convergence-uniform-32-kathara-success20" \
    "kua-dc-v3-32-convergence-uniform-s2000-seed001" \
    "convergence-uniform-32-kathara-attempts"
}

main "$@"
