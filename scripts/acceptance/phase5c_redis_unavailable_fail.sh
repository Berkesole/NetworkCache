#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-rsync-router:phase5c-redis-default-cache}"
TOPOLOGY_PATH="${TOPOLOGY_PATH:-/app/config/acceptance/bridge-4node-redis.yaml}"

BUILD_ARGS=(--build-arg ENABLE_REDIS=ON --build-arg FETCH_REDIS_DEPS=ON)
if [[ "${BUILD_NO_CACHE:-1}" == "1" ]]; then
  BUILD_ARGS=(--no-cache "${BUILD_ARGS[@]}")
fi

BUILD_LOG="$(mktemp)"
RUN_LOG="$(mktemp)"

cleanup() {
  rm -f "$BUILD_LOG" "$RUN_LOG"
}
trap cleanup EXIT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "FAIL: missing required command: $1" >&2
    exit 1
  }
}

step() {
  echo
  echo "== $1 =="
}

pass() {
  echo "PASS: $1"
}

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

require_log() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if grep -Fq "$pattern" "$file"; then
    pass "$label"
  else
    echo "--- LOG ($file) ---" >&2
    cat "$file" >&2
    fail "$label"
  fi
}

reject_log() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if grep -Fq "$pattern" "$file"; then
    echo "--- LOG ($file) ---" >&2
    cat "$file" >&2
    fail "$label"
  else
    pass "$label"
  fi
}

require_cmd docker

step "git baseline"
cd "$ROOT_DIR"
echo "branch=$(git branch --show-current)"
echo "commit=$(git rev-parse --short HEAD)"
pass "git metadata collected"

step "docker build"
echo "+ docker build ${BUILD_ARGS[*]} -t $IMAGE_TAG $ROOT_DIR"
docker build "${BUILD_ARGS[@]}" -t "$IMAGE_TAG" "$ROOT_DIR" >"$BUILD_LOG" 2>&1 || {
  cat "$BUILD_LOG" >&2
  fail "docker build"
}
tail -n 20 "$BUILD_LOG"
pass "docker build"

step "run rsync-node-dev without Redis service"
set +e
docker run --rm \
  --entrypoint /app/build/rsync-node-dev \
  "$IMAGE_TAG" \
  --transport mock \
  --node-id 1 \
  --topology "$TOPOLOGY_PATH" \
  --iterations 0 >"$RUN_LOG" 2>&1
RUN_EXIT=$?
set -e
cat "$RUN_LOG"

if [[ "$RUN_EXIT" -eq 0 ]]; then
  fail "expected non-zero exit when Redis is unavailable"
fi
pass "non-zero exit observed: $RUN_EXIT"

step "assert failure behavior"
require_log "$RUN_LOG" '[rsync-node-dev] cache init failed:' 'startup reported cache initialization failure'
require_log "$RUN_LOG" 'RedisCache init failed [UNIX(/run/redis/redis.sock)' 'failure output names the Redis socket'
reject_log "$RUN_LOG" 'cache backend: in-memory' 'startup did not silently fall back to in-memory cache'

step "result"
echo "PASS: phase5c Redis unavailable failure path behaved explicitly"
