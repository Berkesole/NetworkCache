#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd -- "${EXP_DIR}/../../.." && pwd)
CONFIG_DIR="${EXP_DIR}/configs"
DEFAULT_RESULTS_ROOT="${EXP_DIR}/results"
RESULTS_ROOT="${RESULTS_ROOT:-${DEFAULT_RESULTS_ROOT}}"

usage() {
  cat <<'USAGE'
Usage: run_docker_bridge.sh [--list] <scenario>

Scenarios:
  line-3-cold
  line-4-cold
  line-5-cold
  binary-tree-7-path-warm
  kua-dc-16-warm
  kua-dc-32-warm
  kua-dc-16-zipf-seed001
  kua-dc-32-zipf-seed001
  kua-dc-16-uniform-seed001
  kua-dc-32-uniform-seed001
  kua-dc-v3-16-warm
  kua-dc-v3-32-warm
  kua-dc-v3-16-zipf-seed001
  kua-dc-v3-32-zipf-seed001
  kua-dc-v3-16-zipf-s300-seed001
  kua-dc-v3-32-zipf-s300-seed001
  kua-dc-v3-16-zipf-s500-seed001
  kua-dc-v3-32-zipf-s500-seed001
  kua-dc-v3-16-zipf-light-s500-seed001
  kua-dc-v3-32-zipf-light-s500-seed001
  kua-dc-v3-16-zipf-paired-s700-seed001
  kua-dc-v3-32-zipf-paired-s700-seed001
  kua-dc-v3-16-zipf-pairblock-s1500-seed001
  kua-dc-v3-32-zipf-pairblock-s1500-seed001
  kua-dc-v3-16-zipf-pairblock-s2500-seed001
  kua-dc-v3-32-zipf-pairblock-s2500-seed001
  kua-dc-v3-16-zipf-distanceblock-s2000-seed001
  kua-dc-v3-32-zipf-distanceblock-s2000-seed001
  kua-dc-v3-16-zipf-distanceblock-s2500-seed001
  kua-dc-v3-32-zipf-distanceblock-s2500-seed001
  kua-dc-v3-16-uniform-seed001
  kua-dc-v3-32-uniform-seed001
  kua-dc-v3-16-convergence-controlled-s2000-seed001
  kua-dc-v3-32-convergence-controlled-s2000-seed001
  kua-dc-v3-16-convergence-zipf-s2000-seed001
  kua-dc-v3-32-convergence-zipf-s2000-seed001
  kua-dc-v3-16-convergence-uniform-s2000-seed001
  kua-dc-v3-32-convergence-uniform-s2000-seed001

Environment knobs:
  RUN_ID=...                  Override run id.
  RESULTS_ROOT=...            Override result root directory.
  NODE_LOOP_ITERATIONS=...    Keep nodes alive long enough for scheduled requests.
  NODE_LOOP_SLEEP_MS=...      Tick sleep for rsync-node-dev.
  RSYNC_OBSERVABILITY=1       Enable observability-only structured stdout logs.
  RUN_SECONDS=...             Override scenario wait time.
  BUILD_IMAGE=0               Reuse an existing networkcache-exp:<git-sha> image.
  KEEP_CONTAINERS=1           Preserve containers/network after the run.
USAGE
}

if [[ "${1:-}" == "--list" ]]; then
  usage
  exit 0
fi

SCENARIO="${1:-}"
if [[ -z "${SCENARIO}" ]]; then
  usage >&2
  exit 2
fi

case "${SCENARIO}" in
  line-3-cold)
    TOPOLOGY="line-3"
    CONFIG_FILE="${CONFIG_DIR}/line-3-cold.yaml"
    SUBNET="172.31.10.0/24"
    NODE_IDS=(3 2 1)
    RUN_SECONDS_DEFAULT=20
    NODE_LOOP_ITERATIONS_DEFAULT=400
    WARM_TYPE="cold"
    ;;
  line-4-cold)
    TOPOLOGY="line-4"
    CONFIG_FILE="${CONFIG_DIR}/line-4-cold.yaml"
    SUBNET="172.31.20.0/24"
    NODE_IDS=(4 3 2 1)
    RUN_SECONDS_DEFAULT=25
    NODE_LOOP_ITERATIONS_DEFAULT=400
    WARM_TYPE="cold"
    ;;
  line-5-cold)
    TOPOLOGY="line-5"
    CONFIG_FILE="${CONFIG_DIR}/line-5-cold.yaml"
    SUBNET="172.31.30.0/24"
    NODE_IDS=(5 4 3 2 1)
    RUN_SECONDS_DEFAULT=30
    NODE_LOOP_ITERATIONS_DEFAULT=400
    WARM_TYPE="cold"
    ;;
  binary-tree-7-path-warm)
    TOPOLOGY="binary-tree-7"
    CONFIG_FILE="${CONFIG_DIR}/binary-tree-7-path-warm.yaml"
    SUBNET="172.31.40.0/24"
    NODE_IDS=(7 6 3 1 2 4 5)
    RUN_SECONDS_DEFAULT=45
    NODE_LOOP_ITERATIONS_DEFAULT=400
    WARM_TYPE="path_materialization"
    ;;
  kua-dc-16-warm)
    TOPOLOGY="kua-dc-16"
    CONFIG_FILE="${CONFIG_DIR}/kua-dc-16-warm.yaml"
    SUBNET="172.31.50.0/24"
    NODE_IDS=(13 14 15 16 10 11 12 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=35
    NODE_LOOP_ITERATIONS_DEFAULT=2400
    WARM_TYPE="path_materialization"
    ;;
  kua-dc-32-warm)
    TOPOLOGY="kua-dc-32"
    CONFIG_FILE="${CONFIG_DIR}/kua-dc-32-warm.yaml"
    SUBNET="172.31.60.0/24"
    NODE_IDS=(29 30 31 32 25 26 27 28 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=45
    NODE_LOOP_ITERATIONS_DEFAULT=2400
    WARM_TYPE="path_materialization"
    ;;
  kua-dc-16-zipf-seed001|kua-dc-16-uniform-seed001)
    TOPOLOGY="kua-dc-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    if [[ "${SCENARIO}" == *"-zipf-"* ]]; then
      SUBNET="172.31.70.0/24"
    else
      SUBNET="172.31.90.0/24"
    fi
    NODE_IDS=(13 14 15 16 10 11 12 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=190
    NODE_LOOP_ITERATIONS_DEFAULT=2600
    NODE_LOOP_SLEEP_MS_DEFAULT=100
    WARM_TYPE="auto"
    ;;
  kua-dc-32-zipf-seed001|kua-dc-32-uniform-seed001)
    TOPOLOGY="kua-dc-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    if [[ "${SCENARIO}" == *"-zipf-"* ]]; then
      SUBNET="172.31.80.0/24"
    else
      SUBNET="172.31.100.0/24"
    fi
    NODE_IDS=(29 30 31 32 25 26 27 28 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=210
    NODE_LOOP_ITERATIONS_DEFAULT=2600
    NODE_LOOP_SLEEP_MS_DEFAULT=100
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-warm)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.150.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=70
    NODE_LOOP_ITERATIONS_DEFAULT=2400
    WARM_TYPE="path_materialization"
    ;;
  kua-dc-v3-32-warm)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.160.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=80
    NODE_LOOP_ITERATIONS_DEFAULT=2400
    WARM_TYPE="path_materialization"
    ;;
  kua-dc-v3-16-zipf-seed001|kua-dc-v3-16-uniform-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    if [[ "${SCENARIO}" == *"-zipf-"* ]]; then
      SUBNET="172.31.110.0/24"
    else
      SUBNET="172.31.130.0/24"
    fi
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=190
    NODE_LOOP_ITERATIONS_DEFAULT=2600
    NODE_LOOP_SLEEP_MS_DEFAULT=100
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-zipf-seed001|kua-dc-v3-32-uniform-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    if [[ "${SCENARIO}" == *"-zipf-"* ]]; then
      SUBNET="172.31.120.0/24"
    else
      SUBNET="172.31.140.0/24"
    fi
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=210
    NODE_LOOP_ITERATIONS_DEFAULT=2600
    NODE_LOOP_SLEEP_MS_DEFAULT=100
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-zipf-s300-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.170.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=100
    NODE_LOOP_ITERATIONS_DEFAULT=2800
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-zipf-s300-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.180.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=110
    NODE_LOOP_ITERATIONS_DEFAULT=2800
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-zipf-s500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.190.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=150
    NODE_LOOP_ITERATIONS_DEFAULT=4400
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-zipf-s500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.200.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=160
    NODE_LOOP_ITERATIONS_DEFAULT=4400
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-zipf-light-s500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.210.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=180
    NODE_LOOP_ITERATIONS_DEFAULT=8000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-zipf-light-s500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.220.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=180
    NODE_LOOP_ITERATIONS_DEFAULT=8000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-zipf-paired-s700-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.230.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=210
    NODE_LOOP_ITERATIONS_DEFAULT=9000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-zipf-paired-s700-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.240.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=210
    NODE_LOOP_ITERATIONS_DEFAULT=9000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-zipf-pairblock-s1500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.250.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=190
    NODE_LOOP_ITERATIONS_DEFAULT=7600
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-zipf-pairblock-s1500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.251.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=200
    NODE_LOOP_ITERATIONS_DEFAULT=7600
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-zipf-pairblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.252.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=280
    NODE_LOOP_ITERATIONS_DEFAULT=11000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-zipf-pairblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.253.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=290
    NODE_LOOP_ITERATIONS_DEFAULT=11000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-zipf-distanceblock-s2000-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.254.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=250
    NODE_LOOP_ITERATIONS_DEFAULT=10000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-zipf-distanceblock-s2000-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.31.255.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=260
    NODE_LOOP_ITERATIONS_DEFAULT=10000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-zipf-distanceblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.30.10.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=300
    NODE_LOOP_ITERATIONS_DEFAULT=12000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-zipf-distanceblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.30.20.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=310
    NODE_LOOP_ITERATIONS_DEFAULT=12000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-convergence-controlled-s2000-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.30.30.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=330
    NODE_LOOP_ITERATIONS_DEFAULT=13000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-convergence-controlled-s2000-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.30.40.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=340
    NODE_LOOP_ITERATIONS_DEFAULT=13000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-convergence-zipf-s2000-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.30.50.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=330
    NODE_LOOP_ITERATIONS_DEFAULT=13000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-convergence-zipf-s2000-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.30.60.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=340
    NODE_LOOP_ITERATIONS_DEFAULT=13000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-16-convergence-uniform-s2000-seed001)
    TOPOLOGY="kua-dc-v3-16"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.30.70.0/24"
    NODE_IDS=(16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=330
    NODE_LOOP_ITERATIONS_DEFAULT=13000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  kua-dc-v3-32-convergence-uniform-s2000-seed001)
    TOPOLOGY="kua-dc-v3-32"
    CONFIG_FILE="${CONFIG_DIR}/${SCENARIO}.yaml"
    SUBNET="172.30.80.0/24"
    NODE_IDS=(32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1)
    RUN_SECONDS_DEFAULT=340
    NODE_LOOP_ITERATIONS_DEFAULT=13000
    NODE_LOOP_SLEEP_MS_DEFAULT=25
    WARM_TYPE="auto"
    ;;
  *)
    echo "unknown scenario: ${SCENARIO}" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "missing topology config: ${CONFIG_FILE}" >&2
  exit 1
fi

GIT_SHA=$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)
GIT_COMMIT=$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)
IMAGE_TAG="networkcache-exp:${GIT_SHA}"
RUN_ID="${RUN_ID:-${SCENARIO}-$(date -u +%Y%m%dT%H%M%SZ)-${GIT_SHA}}"
SAFE_RUN_ID=$(printf '%s' "${RUN_ID}" | tr -c 'A-Za-z0-9_.-' '-')
NETWORK_NAME="networkcache-exp-${SAFE_RUN_ID}"
RUN_DIR="${RESULTS_ROOT}/${RUN_ID}"
LOGS_DIR="${RUN_DIR}/logs"
SUMMARY_CSV="${RUN_DIR}/summary.csv"
TOPOLOGY_COPY="${RUN_DIR}/topology.yaml"
RUN_SECONDS="${RUN_SECONDS:-${RUN_SECONDS_DEFAULT}}"
NODE_LOOP_ITERATIONS="${NODE_LOOP_ITERATIONS:-${NODE_LOOP_ITERATIONS_DEFAULT}}"
NODE_LOOP_SLEEP_MS="${NODE_LOOP_SLEEP_MS:-${NODE_LOOP_SLEEP_MS_DEFAULT:-50}}"
RSYNC_OBSERVABILITY_VALUE="${RSYNC_OBSERVABILITY:-0}"
BUILD_IMAGE="${BUILD_IMAGE:-1}"
KEEP_CONTAINERS="${KEEP_CONTAINERS:-0}"
SCHEDULE_METADATA_FILE=""
for candidate in \
  "${EXP_DIR}/workloads/kua-dc-v3/generated/${SCENARIO}.metadata.json" \
  "${EXP_DIR}/workloads/kua-dc-v2/generated/${SCENARIO}.metadata.json"; do
  if [[ -f "${candidate}" ]]; then
    SCHEDULE_METADATA_FILE="${candidate}"
    break
  fi
done

read_workload_metadata() {
  if [[ -z "${SCHEDULE_METADATA_FILE}" ]]; then
    printf '\n\n\n\n\n\n\n\n\n'
    return
  fi
  python3 - "${SCHEDULE_METADATA_FILE}" <<'PY'
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
]:
    print("" if data.get(key) is None else data.get(key))
PY
}

mapfile -t WORKLOAD_META < <(read_workload_metadata)
WORKLOAD_ID="${WORKLOAD_META[0]:-}"
WORKLOAD_TYPE="${WORKLOAD_META[1]:-}"
WORKLOAD_SEED="${WORKLOAD_META[2]:-}"
WORKLOAD_REQUEST_COUNT="${WORKLOAD_META[3]:-}"
WORKLOAD_KEY_COUNT="${WORKLOAD_META[4]:-}"
WORKLOAD_PROVIDER_COUNT="${WORKLOAD_META[5]:-}"
WORKLOAD_REQUESTER_COUNT="${WORKLOAD_META[6]:-}"
WORKLOAD_PHASE_POLICY="${WORKLOAD_META[7]:-}"
DOCKER_LINK_MODEL="docker bridge mirror; no explicit per-link delay"
DOCKER_MIRROR_MODEL=""
if [[ "${SCENARIO}" == kua-dc-v3-* ]]; then
  DOCKER_LINK_MODEL="docker bridge host-overlay mirror; no physical leaf/spine fabric and no explicit per-link delay"
  DOCKER_MIRROR_MODEL="host_overlay_no_physical_fabric"
fi

mkdir -p "${LOGS_DIR}"
cp "${CONFIG_FILE}" "${TOPOLOGY_COPY}"

CONTAINERS=()
BUILD_CONTEXT=""
cleanup() {
  if [[ -n "${BUILD_CONTEXT}" && -d "${BUILD_CONTEXT}" ]]; then
    rm -rf "${BUILD_CONTEXT}"
  fi
  if [[ "${KEEP_CONTAINERS}" == "1" ]]; then
    echo "keeping containers and network: ${NETWORK_NAME}"
    return
  fi
  for container in "${CONTAINERS[@]:-}"; do
    docker rm -f "${container}" >/dev/null 2>&1 || true
  done
  docker network rm "${NETWORK_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ "${BUILD_IMAGE}" == "1" ]]; then
  BUILD_CONTEXT=$(mktemp -d -t networkcache-exp-build.XXXXXX)
  tar -C "${REPO_ROOT}"     --exclude='./.git'     --exclude='./build'     --exclude='./.agents'     --exclude='./.codex'     --exclude='./experiments/content_discovery/delay_find_content/results'     --exclude='./experiments/content_discovery/delay_find_content/logs'     -cf - . | tar -C "${BUILD_CONTEXT}" -xf -
  docker build -t "${IMAGE_TAG}" "${BUILD_CONTEXT}"
fi

docker network rm "${NETWORK_NAME}" >/dev/null 2>&1 || true
docker network create --driver bridge --subnet "${SUBNET}" "${NETWORK_NAME}" >/dev/null

node_ip() {
  local node_id="$1"
  awk -v id="${node_id}" '
    $0 ~ /^  - id:/ {
      current=$3
    }
    current == id && $1 == "host:" {
      print $2
      exit
    }
  ' "${CONFIG_FILE}"
}

CONTAINER_TOPOLOGY_PATH="/app/experiments/content_discovery/delay_find_content/configs/$(basename "${CONFIG_FILE}")"

for node_id in "${NODE_IDS[@]}"; do
  ip=$(node_ip "${node_id}")
  if [[ -z "${ip}" ]]; then
    echo "could not resolve IP for node ${node_id} from ${CONFIG_FILE}" >&2
    exit 1
  fi
  name="${SAFE_RUN_ID}-node-${node_id}"
  app_args="--node-id ${node_id} --transport tcp --topology ${CONTAINER_TOPOLOGY_PATH} --iterations ${NODE_LOOP_ITERATIONS} --sleep-ms ${NODE_LOOP_SLEEP_MS}"
  CONTAINERS+=("${name}")
  docker run -d \
    --name "${name}" \
    --network "${NETWORK_NAME}" \
    --ip "${ip}" \
    -e RSYNC_APP_TARGET=rsync-node-dev \
    -e RSYNC_APP_ARGS="${app_args}" \
    -e RSYNC_TRANSPORT=tcp \
    -e RSYNC_NODE_ID="${node_id}" \
    -e RSYNC_TOPOLOGY_CONFIG="${CONTAINER_TOPOLOGY_PATH}" \
    -e RSYNC_LOOP_ITERATIONS="${NODE_LOOP_ITERATIONS}" \
    -e RSYNC_LOOP_SLEEP_MS="${NODE_LOOP_SLEEP_MS}" \
    -e RSYNC_OBSERVABILITY="${RSYNC_OBSERVABILITY_VALUE}" \
    "${IMAGE_TAG}" >/dev/null
  sleep 1
done

cat > "${RUN_DIR}/metadata.json" <<META
{
  "run_id": "${RUN_ID}",
  "scenario": "${SCENARIO}",
  "env": "docker",
  "topology": "${TOPOLOGY}",
  "warm_type": "${WARM_TYPE}",
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
  "image_tag": "${IMAGE_TAG}",
  "link_model": "${DOCKER_LINK_MODEL}",
  "docker_mirror_model": "${DOCKER_MIRROR_MODEL}",
  "network_name": "${NETWORK_NAME}",
  "subnet": "${SUBNET}",
  "run_seconds": ${RUN_SECONDS},
  "node_loop_iterations": ${NODE_LOOP_ITERATIONS},
  "node_loop_sleep_ms": ${NODE_LOOP_SLEEP_MS},
  "rsync_observability": "${RSYNC_OBSERVABILITY_VALUE}",
  "containers": [
$(for i in "${!CONTAINERS[@]}"; do
    container="${CONTAINERS[$i]}"
    node_id="${container##*-node-}"
    comma=","; [[ "${i}" == "$((${#CONTAINERS[@]} - 1))" ]] && comma=""
    printf '    {"node": %s, "name": "%s", "ip": "%s"}%s\n' "${node_id}" "${container}" "$(node_ip "${node_id}")" "${comma}"
  done)
  ]
}
META

sleep "${RUN_SECONDS}"

for container in "${CONTAINERS[@]}"; do
  node_id="${container##*-node-}"
  docker logs --timestamps "${container}" > "${LOGS_DIR}/node-${node_id}.log" 2>&1 || true
done

ANALYZE_ARGS=(
  "${SCRIPT_DIR}/analyze_logs.py"
  --logs-dir "${LOGS_DIR}"
  --output "${SUMMARY_CSV}"
  --scenario "${SCENARIO}"
  --env docker
  --topology "${TOPOLOGY}"
  --topology-file "${TOPOLOGY_COPY}"
  --warm-type "${WARM_TYPE}"
)
if [[ -n "${SCHEDULE_METADATA_FILE}" ]]; then
  ANALYZE_ARGS+=(--schedule-metadata "${SCHEDULE_METADATA_FILE}")
fi
python3 "${ANALYZE_ARGS[@]}"

echo "run directory: ${RUN_DIR}"
echo "summary: ${SUMMARY_CSV}"
