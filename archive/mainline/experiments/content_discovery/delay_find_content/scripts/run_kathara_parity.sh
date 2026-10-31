#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd -- "${EXP_DIR}/../../.." && pwd)
DEFAULT_RESULTS_ROOT="${EXP_DIR}/results"
RESULTS_ROOT="${RESULTS_ROOT:-${DEFAULT_RESULTS_ROOT}}"

usage() {
  cat <<'USAGE'
Usage: run_kathara_parity.sh <scenario>

Scenarios:
  line-4
  binary-tree-7
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
  kua-dc-v3-16-convergence-controlled-s2000-seed001
  kua-dc-v3-32-convergence-controlled-s2000-seed001
  kua-dc-v3-16-convergence-zipf-s2000-seed001
  kua-dc-v3-32-convergence-zipf-s2000-seed001
  kua-dc-v3-16-convergence-zipf-long-s2000-seed001
  kua-dc-v3-32-convergence-zipf-long-s2000-seed001
  kua-dc-v3-16-convergence-uniform-s2000-seed001
  kua-dc-v3-32-convergence-uniform-s2000-seed001
  kua-dc-v3-16-uniform-seed001
  kua-dc-v3-32-uniform-seed001

This parity driver intentionally reuses existing Kathara scenario scripts and
keeps the output shape aligned with the Docker driver. Kathara app logs are
expected to carry ISO UTC timestamp prefixes so the existing log analyzer can
compute latency without protocol instrumentation.
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
  line-4)
    TOPOLOGY="line-4"
    WARM_TYPE="cold"
    CONFIG_FILE="${EXP_DIR}/configs/line-4-cold.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/acceptance/phase5f_kathara_linear_smoke.sh")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/phase5f-linear-smoke/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=35
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=500
    KATHARA_LINK_DELAY_MS_DEFAULT=0
    CLEAN_RUIKEKKK=0
    ;;
  binary-tree-7)
    TOPOLOGY="binary-tree-7"
    WARM_TYPE="path_materialization"
    CONFIG_FILE="${EXP_DIR}/configs/binary-tree-7-path-warm.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "binary-tree-7-warm")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=35
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=500
    KATHARA_LINK_DELAY_MS_DEFAULT=0
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-16-warm)
    TOPOLOGY="kua-dc-16"
    WARM_TYPE="path_materialization"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/kua-dc-16-warm.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "kua-dc-16-warm")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=60
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=50
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-32-warm)
    TOPOLOGY="kua-dc-32"
    WARM_TYPE="path_materialization"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/kua-dc-32-warm.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "kua-dc-32-warm")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=60
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=50
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-16-zipf-seed001|kua-dc-16-uniform-seed001)
    TOPOLOGY="kua-dc-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=210
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=100
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-32-zipf-seed001|kua-dc-32-uniform-seed001)
    TOPOLOGY="kua-dc-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=230
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=100
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-warm)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="path_materialization"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=90
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=50
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-warm)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="path_materialization"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=100
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=50
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-zipf-seed001|kua-dc-v3-16-uniform-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=240
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=100
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-zipf-seed001|kua-dc-v3-32-uniform-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=260
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=100
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-zipf-s300-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=120
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-zipf-s300-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=130
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-zipf-s500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=170
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-zipf-s500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=180
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-zipf-light-s500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=110
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-zipf-light-s500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=120
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-zipf-paired-s700-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=150
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-zipf-paired-s700-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=160
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-zipf-pairblock-s1500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=170
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-zipf-pairblock-s1500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=180
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-zipf-pairblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=260
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-zipf-pairblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=270
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-zipf-distanceblock-s2000-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=230
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-zipf-distanceblock-s2000-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=240
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-zipf-distanceblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=280
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-zipf-distanceblock-s2500-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=290
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-convergence-controlled-s2000-seed001|\
  kua-dc-v3-16-convergence-zipf-s2000-seed001|\
  kua-dc-v3-16-convergence-uniform-s2000-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=330
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-convergence-zipf-long-s2000-seed001)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=570
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-convergence-controlled-s2000-seed001|\
  kua-dc-v3-32-convergence-zipf-s2000-seed001|\
  kua-dc-v3-32-convergence-uniform-s2000-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=350
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-convergence-zipf-long-s2000-seed001)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=590
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-16-latency-v2-*)
    TOPOLOGY="kua-dc-v3-16"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=650
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  kua-dc-v3-32-latency-v2-*)
    TOPOLOGY="kua-dc-v3-32"
    WARM_TYPE="auto"
    CONFIG_FILE="${REPO_ROOT}/kathara/ruikekkk/scenarios/${SCENARIO}.yaml"
    RUN_CMD=("${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh" "${SCENARIO}")
    SOURCE_LOG_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/logs"
    KATHARA_WAIT_SECONDS_DEFAULT=670
    KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT=25
    KATHARA_LINK_DELAY_MS_DEFAULT=2
    CLEAN_RUIKEKKK=1
    ;;
  *)
    echo "unknown Kathara scenario: ${SCENARIO}" >&2
    usage >&2
    exit 2
    ;;
esac

require_cmd kathara
require_cmd docker
[[ -f "${CONFIG_FILE}" ]] || fail "missing scenario config: ${CONFIG_FILE}"
[[ -x "${RUN_CMD[0]}" ]] || fail "missing executable scenario runner: ${RUN_CMD[0]}"

GIT_SHA=$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)
GIT_COMMIT=$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)
KATHARA_VERSION=$(kathara --version 2>/dev/null || echo unavailable)
DOCKER_VERSION=$(docker --version 2>/dev/null || echo unavailable)
KATHARA_NETWORK_PLUGIN=$(python3 - <<'PYCONF' 2>/dev/null || echo unknown
import json
from pathlib import Path
path = Path.home() / '.config' / 'kathara.conf'
print(json.loads(path.read_text()).get('network_plugin', 'unknown'))
PYCONF
)
RUN_ID="${RUN_ID:-kathara-${SCENARIO}-$(date -u +%Y%m%dT%H%M%SZ)-${GIT_SHA}}"
RUN_DIR="${RESULTS_ROOT}/${RUN_ID}"
LOGS_DIR="${RUN_DIR}/logs"
TRACES_DIR="${RUN_DIR}/traces"
KATHARA_WAIT_SECONDS="${KATHARA_WAIT_SECONDS:-${KATHARA_WAIT_SECONDS_DEFAULT}}"
PARITY_BUILD_NO_CACHE="${BUILD_NO_CACHE:-0}"
PARITY_ENABLE_REDIS="${ENABLE_REDIS:-OFF}"
PARITY_FETCH_REDIS_DEPS="${FETCH_REDIS_DEPS:-OFF}"
PARITY_NODE_LOOP_SLEEP_MS="${KATHARA_NODE_LOOP_SLEEP_MS:-${KATHARA_NODE_LOOP_SLEEP_MS_DEFAULT}}"
PARITY_LINK_DELAY_MS="${KATHARA_LINK_DELAY_MS:-${KATHARA_LINK_DELAY_MS_DEFAULT}}"
PARITY_SKIP_BUILD="${SKIP_BUILD:-0}"
PARITY_OBSERVABILITY="${RSYNC_OBSERVABILITY:-0}"
PARITY_EVENT_TRACE="${RSYNC_EVENT_TRACE:-0}"
PARITY_EVENT_TRACE_FLUSH_EVERY="${RSYNC_EVENT_TRACE_FLUSH_EVERY:-64}"
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
mkdir -p "${LOGS_DIR}"
if [[ -d "${SOURCE_LOG_DIR}" ]]; then
  find "${SOURCE_LOG_DIR}" -maxdepth 1 -type f -name '*.log' -delete
fi
SOURCE_TRACE_DIR="${REPO_ROOT}/kathara/ruikekkk/shared/traces"
if [[ -d "${SOURCE_TRACE_DIR}" ]]; then
  find "${SOURCE_TRACE_DIR}" -maxdepth 1 -type f -name '*.jsonl' -delete
fi

cp "${CONFIG_FILE}" "${RUN_DIR}/topology.yaml"
if [[ -n "${SCHEDULE_METADATA_FILE}" ]]; then
  cp "${SCHEDULE_METADATA_FILE}" "${RUN_DIR}/schedule_metadata.json"
fi
cat > "${RUN_DIR}/metadata.json" <<META
{
  "run_id": "${RUN_ID}",
  "scenario": "${SCENARIO}",
  "env": "kathara",
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
  "kathara_version": "${KATHARA_VERSION}",
  "docker_version": "${DOCKER_VERSION}",
  "kathara_network_plugin": "${KATHARA_NETWORK_PLUGIN}",
  "kathara_wait_seconds": "${KATHARA_WAIT_SECONDS}",
  "build_no_cache": "${PARITY_BUILD_NO_CACHE}",
  "skip_build": "${PARITY_SKIP_BUILD}",
  "enable_redis": "${PARITY_ENABLE_REDIS}",
  "fetch_redis_deps": "${PARITY_FETCH_REDIS_DEPS}",
  "kathara_node_loop_sleep_ms": "${PARITY_NODE_LOOP_SLEEP_MS}",
  "kathara_link_delay_ms": "${PARITY_LINK_DELAY_MS}",
  "rsync_observability": "${PARITY_OBSERVABILITY}",
  "trace_enabled": "${PARITY_EVENT_TRACE}",
  "trace_flush_every": "${PARITY_EVENT_TRACE_FLUSH_EVERY}",
  "trace_clock": "monotonic_ns",
  "note": "Kathara run reuses existing lab scripts with timestamped app logs and the shared analyzer; legacy line-4 parity is qualitative phase5f demo_key only."
}
META

set +e
env \
  BUILD_NO_CACHE="${PARITY_BUILD_NO_CACHE}" \
  SKIP_BUILD="${PARITY_SKIP_BUILD}" \
  ENABLE_REDIS="${PARITY_ENABLE_REDIS}" \
  FETCH_REDIS_DEPS="${PARITY_FETCH_REDIS_DEPS}" \
  KATHARA_NODE_LOOP_SLEEP_MS="${PARITY_NODE_LOOP_SLEEP_MS}" \
  KATHARA_LINK_DELAY_MS="${PARITY_LINK_DELAY_MS}" \
  RSYNC_OBSERVABILITY="${PARITY_OBSERVABILITY}" \
  RSYNC_EVENT_TRACE="${PARITY_EVENT_TRACE}" \
  RSYNC_EVENT_TRACE_FLUSH_EVERY="${PARITY_EVENT_TRACE_FLUSH_EVERY}" \
  RSYNC_EVENT_TRACE_RUN_ID="${RUN_ID}" \
  "${RUN_CMD[@]}" >"${RUN_DIR}/driver.stdout.log" 2>"${RUN_DIR}/driver.stderr.log"
driver_status=$?
set -e

if [[ "${driver_status}" -ne 0 ]]; then
  if [[ -d "${SOURCE_LOG_DIR}" ]]; then
    find "${SOURCE_LOG_DIR}" -maxdepth 1 -type f -name '*.log' -exec cp {} "${LOGS_DIR}/" \;
  fi
  if [[ "${PARITY_EVENT_TRACE}" != "0" && "${PARITY_EVENT_TRACE}" != "false" && "${PARITY_EVENT_TRACE}" != "FALSE" ]]; then
    mkdir -p "${TRACES_DIR}"
    if [[ -d "${SOURCE_TRACE_DIR}" ]]; then
      find "${SOURCE_TRACE_DIR}" -maxdepth 1 -type f -name '*.jsonl' -exec cp {} "${TRACES_DIR}/" \;
    fi
  fi
  if [[ "${CLEAN_RUIKEKKK}" == "1" ]]; then
    "${REPO_ROOT}/scripts/kathara/down_ruikekkk_scenario.sh" >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || \
      echo "warning: failed to clean Kathara ruikekkk lab" >&2
  fi
  echo "Kathara runner failed with exit status ${driver_status}; retained logs in ${RUN_DIR}" >&2
  exit "${driver_status}"
fi

if [[ "${driver_status}" -eq 0 && "${CLEAN_RUIKEKKK}" == "1" ]]; then
  echo "waiting ${KATHARA_WAIT_SECONDS}s for Kathara scenario ticks to complete" >>"${RUN_DIR}/driver.stdout.log"
  sleep "${KATHARA_WAIT_SECONDS}"
fi

if [[ -d "${SOURCE_LOG_DIR}" ]]; then
  find "${SOURCE_LOG_DIR}" -maxdepth 1 -type f -name '*.log' -exec cp {} "${LOGS_DIR}/" \;
else
  echo "warning: Kathara log directory not found: ${SOURCE_LOG_DIR}" >&2
fi
if [[ "${PARITY_EVENT_TRACE}" != "0" && "${PARITY_EVENT_TRACE}" != "false" && "${PARITY_EVENT_TRACE}" != "FALSE" ]]; then
  mkdir -p "${TRACES_DIR}"
  if [[ -d "${SOURCE_TRACE_DIR}" ]]; then
    find "${SOURCE_TRACE_DIR}" -maxdepth 1 -type f -name '*.jsonl' -exec cp {} "${TRACES_DIR}/" \;
  else
    echo "warning: Kathara trace directory not found: ${SOURCE_TRACE_DIR}" >&2
  fi
fi

if [[ "${CLEAN_RUIKEKKK}" == "1" ]]; then
  "${REPO_ROOT}/scripts/kathara/down_ruikekkk_scenario.sh" >>"${RUN_DIR}/driver.stdout.log" 2>>"${RUN_DIR}/driver.stderr.log" || \
    echo "warning: failed to clean Kathara ruikekkk lab" >&2
fi

ANALYZE_ARGS=(
  "${SCRIPT_DIR}/analyze_logs.py"
  --logs-dir "${LOGS_DIR}"
  --output "${RUN_DIR}/summary.csv"
  --scenario "${SCENARIO}"
  --env kathara
  --topology "${TOPOLOGY}"
  --topology-file "${RUN_DIR}/topology.yaml"
  --warm-type "${WARM_TYPE}"
)
if [[ -n "${SCHEDULE_METADATA_FILE}" ]]; then
  ANALYZE_ARGS+=(--schedule-metadata "${SCHEDULE_METADATA_FILE}")
fi
python3 "${ANALYZE_ARGS[@]}"

if [[ -d "${TRACES_DIR}" ]] && find "${TRACES_DIR}" -maxdepth 1 -type f -name '*.jsonl' | grep -q .; then
  TRACE_ANALYZE_ARGS=(
    "${SCRIPT_DIR}/analyze_trace_events.py"
    --traces-dir "${TRACES_DIR}"
    --summary-output "${RUN_DIR}/trace_summary.csv"
    --breakdown-output "${RUN_DIR}/trace_breakdown.csv"
    --by-class-output "${RUN_DIR}/trace_breakdown_by_class.csv"
    --scenario "${SCENARIO}"
    --env kathara
    --topology "${TOPOLOGY}"
    --topology-file "${RUN_DIR}/topology.yaml"
    --warm-type "${WARM_TYPE}"
  )
  if [[ -n "${SCHEDULE_METADATA_FILE}" ]]; then
    TRACE_ANALYZE_ARGS+=(--schedule-metadata "${SCHEDULE_METADATA_FILE}")
  fi
  python3 "${TRACE_ANALYZE_ARGS[@]}"
fi

echo "run directory: ${RUN_DIR}"
echo "summary: ${RUN_DIR}/summary.csv"
