#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
RESULTS_DIR="${EXP_DIR}/results"
REPEATS="${REPEATS:-20}"
WAIT_UNIT="${WAIT_UNIT:-rsync-convergence-kathara-success20-queue.service}"
POLL_SECONDS="${POLL_SECONDS:-60}"

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

wait_for_unit() {
  local unit="$1"
  if [[ -z "${unit}" ]]; then
    return
  fi
  echo "waiting for ${unit}"
  while systemctl is-active --quiet "${unit}"; do
    sleep 60
  done
}

require_kathara_success20() {
  local missing=0
  local batch=""
  for batch in \
    convergence-controlled-16-kathara-success20 \
    convergence-controlled-32-kathara-success20 \
    convergence-zipf-16-kathara-success20 \
    convergence-zipf-32-kathara-success20 \
    convergence-uniform-16-kathara-success20 \
    convergence-uniform-32-kathara-success20; do
    if ! valid_kathara_success_batch "${RESULTS_DIR}/${batch}"; then
      echo "missing or invalid Kathara success20 batch: ${batch}" >&2
      missing=1
    fi
  done
  [[ "${missing}" == "0" ]]
}

wait_for_kathara_success20() {
  wait_for_unit "${WAIT_UNIT}"
  until require_kathara_success20; do
    echo "Kathara success20 batches are not complete yet; sleeping ${POLL_SECONDS}s"
    sleep "${POLL_SECONDS}"
  done
}

run_docker_batch_if_needed() {
  local run_group="$1"
  local scenarios="$2"
  local manifest="${RESULTS_DIR}/${run_group}/manifest.csv"
  if [[ -f "${manifest}" ]]; then
    echo "docker mirror already exists: ${run_group}"
    return
  fi
  "${SCRIPT_DIR}/run_docker_batch.sh" \
    --scenarios "${scenarios}" \
    --repeats "${REPEATS}" \
    --run-group-id "${run_group}"
}

main() {
  wait_for_kathara_success20

  run_docker_batch_if_needed \
    "convergence-controlled-docker-n${REPEATS}" \
    "kua-dc-v3-16-convergence-controlled-s2000-seed001,kua-dc-v3-32-convergence-controlled-s2000-seed001"

  run_docker_batch_if_needed \
    "convergence-zipf-docker-n${REPEATS}" \
    "kua-dc-v3-16-convergence-zipf-s2000-seed001,kua-dc-v3-32-convergence-zipf-s2000-seed001"

  run_docker_batch_if_needed \
    "convergence-uniform-docker-n${REPEATS}" \
    "kua-dc-v3-16-convergence-uniform-s2000-seed001,kua-dc-v3-32-convergence-uniform-s2000-seed001"
}

main "$@"
