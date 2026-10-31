#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd -- "${EXP_DIR}/../../.." && pwd)
RESULTS_DIR="${EXP_DIR}/results"
REPORT_SCRIPT="${REPO_ROOT}/experiments/reports/generate_latency_convergence_report.py"
WAIT_UNIT="${WAIT_UNIT:-rsync-convergence-docker-mirror-queue.service}"
POLL_SECONDS="${POLL_SECONDS:-60}"
PYTHON_BIN="${PYTHON_BIN:-}"

choose_python() {
  if [[ -n "${PYTHON_BIN}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
    return
  fi
  if [[ -x /tmp/rsync-report-venv/bin/python ]]; then
    printf '%s\n' /tmp/rsync-report-venv/bin/python
    return
  fi
  printf '%s\n' python3
}

wait_for_unit() {
  local unit="$1"
  if [[ -z "${unit}" ]]; then
    return
  fi
  echo "waiting for ${unit}"
  while systemctl is-active --quiet "${unit}"; do
    sleep "${POLL_SECONDS}"
  done
}

valid_kathara_success_batch() {
  local batch_dir="$1"
  [[ -f "${batch_dir}/manifest.csv" ]] || return 1
  python3 - "$batch_dir" <<'PY'
import csv
import sys
from pathlib import Path

batch = Path(sys.argv[1])
manifest = list(csv.DictReader((batch / "manifest.csv").open(newline="", encoding="utf-8")))
if len(manifest) != 20 or any(row.get("status") != "ok" for row in manifest):
    raise SystemExit(1)
merged = list(csv.DictReader((batch / "convergence_merged.csv").open(newline="", encoding="utf-8")))
if len(merged) != 2400 or sum(1 for row in merged if row.get("trace_complete") == "1") != 2400:
    raise SystemExit(1)
PY
}

valid_docker_batch() {
  local batch_dir="$1"
  [[ -f "${batch_dir}/manifest.csv" ]] || return 1
  python3 - "$batch_dir" <<'PY'
import csv
import sys
from pathlib import Path

batch = Path(sys.argv[1])
manifest = list(csv.DictReader((batch / "manifest.csv").open(newline="", encoding="utf-8")))
if len(manifest) != 40 or any(row.get("status") != "ok" for row in manifest):
    raise SystemExit(1)
merged = batch / "merged.csv"
if not merged.exists():
    raise SystemExit(1)
rows = list(csv.DictReader(merged.open(newline="", encoding="utf-8")))
if len(rows) == 0:
    raise SystemExit(1)
PY
}

wait_for_batches() {
  local batch=""
  local complete=0
  until [[ "${complete}" == "1" ]]; do
    complete=1
    for batch in \
      convergence-controlled-16-kathara-success20 \
      convergence-controlled-32-kathara-success20 \
      convergence-zipf-16-kathara-success20 \
      convergence-zipf-32-kathara-success20 \
      convergence-uniform-16-kathara-success20 \
      convergence-uniform-32-kathara-success20; do
      if ! valid_kathara_success_batch "${RESULTS_DIR}/${batch}"; then
        echo "waiting for Kathara success20: ${batch}"
        complete=0
      fi
    done
    for batch in \
      convergence-controlled-docker-n20 \
      convergence-zipf-docker-n20 \
      convergence-uniform-docker-n20; do
      if ! valid_docker_batch "${RESULTS_DIR}/${batch}"; then
        echo "waiting for Docker mirror: ${batch}"
        complete=0
      fi
    done
    if [[ "${complete}" != "1" ]]; then
      sleep "${POLL_SECONDS}"
    fi
  done
}

main() {
  cd "${REPO_ROOT}"
  wait_for_unit "${WAIT_UNIT}"
  wait_for_batches
  local python_bin
  python_bin=$(choose_python)
  echo "generating reports with ${python_bin}"
  "${python_bin}" "${REPORT_SCRIPT}"
}

main "$@"
