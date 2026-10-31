#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
RESULTS_DIR="${EXP_DIR}/results"
TARGET_PER_SCENARIO="${TARGET_PER_SCENARIO:-20}"
REPEATS_PER_ATTEMPT="${REPEATS_PER_ATTEMPT:-8}"
POLL_SECONDS="${POLL_SECONDS:-60}"

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
if sum(1 for _ in csv.DictReader(merged.open(newline="", encoding="utf-8"))) == 0:
    raise SystemExit(1)
PY
}

next_attempt_dir() {
  local canonical="$1"
  local index=1
  while [[ -e "${canonical}-attempts${index}" ]]; do
    index=$((index + 1))
  done
  printf '%s-attempts%s\n' "${canonical}" "${index}"
}

attempt_dirs() {
  local canonical="$1"
  local found=0
  local path=""
  for path in "${canonical}"-attempts*; do
    if [[ -d "${path}" ]]; then
      printf '%s\n' "${path}"
      found=1
    fi
  done
  [[ "${found}" == "1" ]]
}

move_invalid_canonical_to_attempt() {
  local canonical="$1"
  if [[ ! -e "${canonical}" ]]; then
    return
  fi
  if valid_docker_batch "${canonical}"; then
    return
  fi
  local destination
  destination=$(next_attempt_dir "${canonical}")
  echo "preserving invalid canonical as ${destination}"
  mv "${canonical}" "${destination}"
}

missing_scenarios() {
  local target="$1"
  local scenarios_csv="$2"
  shift 2
  python3 - "$target" "$scenarios_csv" "$@" <<'PY'
import csv
import sys
from pathlib import Path

target = int(sys.argv[1])
scenarios = [part for part in sys.argv[2].split(",") if part]
counts = {scenario: 0 for scenario in scenarios}
for directory in sys.argv[3:]:
    manifest = Path(directory) / "manifest.csv"
    if not manifest.exists():
        continue
    for row in csv.DictReader(manifest.open(newline="", encoding="utf-8")):
        scenario = row.get("scenario", "")
        if scenario in counts and row.get("status") == "ok":
            counts[scenario] += 1
missing = [scenario for scenario in scenarios if counts[scenario] < target]
print(",".join(missing))
for scenario in scenarios:
    print(f"{scenario}: {counts[scenario]}/{target} ok", file=sys.stderr)
PY
}

collect_success_batch() {
  local canonical="$1"
  local scenarios_csv="$2"
  shift 2
  python3 "${SCRIPT_DIR}/collect_success_runs.py" \
    --output "${canonical}" \
    --target-per-scenario "${TARGET_PER_SCENARIO}" \
    --scenarios "${scenarios_csv}" \
    "$@"
}

ensure_success_batch() {
  local run_group="$1"
  local scenarios_csv="$2"
  local canonical="${RESULTS_DIR}/${run_group}"

  until valid_docker_batch "${canonical}"; do
    move_invalid_canonical_to_attempt "${canonical}"

    local attempts=()
    while IFS= read -r attempt; do
      attempts+=("${attempt}")
    done < <(attempt_dirs "${canonical}" || true)

    local missing="${scenarios_csv}"
    if [[ "${#attempts[@]}" -gt 0 ]]; then
      missing=$(missing_scenarios "${TARGET_PER_SCENARIO}" "${scenarios_csv}" "${attempts[@]}")
    fi

    if [[ -z "${missing}" ]]; then
      echo "collecting ${run_group} from ${#attempts[@]} attempt directories"
      collect_success_batch "${canonical}" "${scenarios_csv}" "${attempts[@]}"
      continue
    fi

    local next_attempt
    next_attempt=$(next_attempt_dir "${canonical}")
    local next_run_group
    next_run_group=$(basename "${next_attempt}")
    echo "running supplemental Docker attempts for ${run_group}: ${missing}"
    "${SCRIPT_DIR}/run_docker_batch.sh" \
      --scenarios "${missing}" \
      --repeats "${REPEATS_PER_ATTEMPT}" \
      --allow-incomplete \
      --run-group-id "${next_run_group}"
  done
  echo "valid Docker success20 mirror: ${run_group}"
}

main() {
  wait_for_unit "rsync-convergence-docker-mirror-queue.service"
  wait_for_unit "rsync-convergence-zipf-docker-n20-attempts2.service"

  ensure_success_batch \
    "convergence-controlled-docker-n20" \
    "kua-dc-v3-16-convergence-controlled-s2000-seed001,kua-dc-v3-32-convergence-controlled-s2000-seed001"

  ensure_success_batch \
    "convergence-zipf-docker-n20" \
    "kua-dc-v3-16-convergence-zipf-s2000-seed001,kua-dc-v3-32-convergence-zipf-s2000-seed001"

  ensure_success_batch \
    "convergence-uniform-docker-n20" \
    "kua-dc-v3-16-convergence-uniform-s2000-seed001,kua-dc-v3-32-convergence-uniform-s2000-seed001"
}

main "$@"
