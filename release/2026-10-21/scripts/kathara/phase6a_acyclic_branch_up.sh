#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAB_DIR="${LAB_DIR:-${ROOT_DIR}/kathara/phase6a-acyclic-branch-smoke}"
IMAGE_TAG="${IMAGE_TAG:-rsync-router:phase6a-kathara-acyclic-branch}"
BUILD_LOG="$(mktemp)"
BUILD_ARGS=(--build-arg ENABLE_REDIS=ON --build-arg FETCH_REDIS_DEPS=ON)

cleanup() {
  rm -f "$BUILD_LOG"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

require_cmd docker
require_cmd kathara
"${ROOT_DIR}/scripts/kathara/phase6a_acyclic_branch_render_topology.sh"
echo "+ kathara lstart -d $LAB_DIR --print"
kathara lstart -d "$LAB_DIR" --print >/dev/null

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  if [[ "${BUILD_NO_CACHE:-1}" == "1" ]]; then
    BUILD_ARGS=(--no-cache "${BUILD_ARGS[@]}")
  fi

  echo "+ docker build ${BUILD_ARGS[*]} -t $IMAGE_TAG $ROOT_DIR"
  docker build "${BUILD_ARGS[@]}" -t "$IMAGE_TAG" "$ROOT_DIR" >"$BUILD_LOG" 2>&1 || {
    cat "$BUILD_LOG" >&2
    fail "docker build"
  }
  tail -n 20 "$BUILD_LOG"
fi

kathara lclean -d "$LAB_DIR" >/dev/null 2>&1 || true
echo "+ kathara lstart -d $LAB_DIR --noterminals"
kathara lstart -d "$LAB_DIR" --noterminals
