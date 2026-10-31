#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAB_DIR="${LAB_DIR:-${ROOT_DIR}/kathara/ruikekkk}"
IMAGE_TAG="${IMAGE_TAG:-rsync-router:ruikekkk-kathara}"
ENABLE_REDIS="${ENABLE_REDIS:-ON}"
FETCH_REDIS_DEPS="${FETCH_REDIS_DEPS:-ON}"
BUILD_LOG="$(mktemp)"
BUILD_ARGS=(--build-arg "ENABLE_REDIS=${ENABLE_REDIS}" --build-arg "FETCH_REDIS_DEPS=${FETCH_REDIS_DEPS}")
if [[ -n "${DOCKERFILE_PATH:-}" ]]; then
  BUILD_ARGS=(-f "${DOCKERFILE_PATH}" "${BUILD_ARGS[@]}")
fi

cleanup() {
  rm -f "${BUILD_LOG}"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  scripts/kathara/run_ruikekkk_scenario.sh <scenario>
  scripts/kathara/run_ruikekkk_scenario.sh --list
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

scenario_arg="${1:-}"
[[ -n "${scenario_arg}" ]] || {
  usage >&2
  exit 1
}

if [[ "${scenario_arg}" == "--list" ]]; then
  "${ROOT_DIR}/scripts/kathara/render_ruikekkk_scenario.sh" --list
  exit 0
fi

require_cmd docker
require_cmd kathara

export LAB_DIR IMAGE_TAG
"${ROOT_DIR}/scripts/kathara/render_ruikekkk_scenario.sh" "${scenario_arg}"

echo "+ kathara lstart -d ${LAB_DIR} --print"
kathara lstart -d "${LAB_DIR}" --print >/dev/null

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  if [[ "${BUILD_NO_CACHE:-1}" == "1" ]]; then
    BUILD_ARGS=(--no-cache "${BUILD_ARGS[@]}")
  fi

  echo "+ docker build ${BUILD_ARGS[*]} -t ${IMAGE_TAG} ${ROOT_DIR}"
  docker build "${BUILD_ARGS[@]}" -t "${IMAGE_TAG}" "${ROOT_DIR}" >"${BUILD_LOG}" 2>&1 || {
    cat "${BUILD_LOG}" >&2
    fail "docker build"
  }
  tail -n 20 "${BUILD_LOG}"
fi

if [[ -n "${RUNTIME_PROVENANCE_FILE:-}" ]]; then
  provenance_tmp="${RUNTIME_PROVENANCE_FILE}.tmp.$$"
  docker image inspect --format '{{.Id}}' "${IMAGE_TAG}" >"${provenance_tmp}"
  mv -f -- "${provenance_tmp}" "${RUNTIME_PROVENANCE_FILE}"
fi

kathara lclean -d "${LAB_DIR}" >/dev/null 2>&1 || true
mkdir -p "${LAB_DIR}/shared/logs" "${LAB_DIR}/shared/state"
rm -f "${LAB_DIR}/shared/logs"/*.log
if [[ "${RSYNC_EVENT_TRACE:-0}" != "0" && "${RSYNC_EVENT_TRACE:-0}" != "false" && "${RSYNC_EVENT_TRACE:-0}" != "FALSE" ]]; then
  mkdir -p "${LAB_DIR}/shared/traces"
  rm -f "${LAB_DIR}/shared/traces"/*.jsonl
fi
echo "+ kathara lstart -d ${LAB_DIR} --noterminals"
kathara lstart -d "${LAB_DIR}" --noterminals

active_scenario="$(cat "${LAB_DIR}/shared/active-scenario.txt")"
echo "active_scenario=${active_scenario}"
echo "lab_dir=${LAB_DIR}"
echo "rendered_topology=${LAB_DIR}/shared/ruikekkk-topology.yaml"
echo "logs_dir=${LAB_DIR}/shared/logs"
echo "traces_dir=${LAB_DIR}/shared/traces"
