#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE_BIN="${1:-${ROOT_DIR}/build/rsync-node-dev}"
TOPOLOGY_PATH="${2:-${ROOT_DIR}/config/3-node-topology.yaml}"
TEST_REDIS_SOCKET="${TEST_REDIS_SOCKET:-}"

require_file() {
  local path="$1"
  local label="$2"
  [[ -e "$path" ]] || {
    echo "FAIL: missing ${label}: $path" >&2
    exit 1
  }
}

require_log() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if grep -Fq "$pattern" "$file"; then
    echo "PASS: $label"
  else
    echo "--- LOG ($file) ---" >&2
    cat "$file" >&2
    echo "FAIL: $label" >&2
    exit 1
  fi
}

reject_log() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if grep -Fq "$pattern" "$file"; then
    echo "--- LOG ($file) ---" >&2
    cat "$file" >&2
    echo "FAIL: $label" >&2
    exit 1
  else
    echo "PASS: $label"
  fi
}

[[ -n "$TEST_REDIS_SOCKET" ]] || {
  echo "FAIL: TEST_REDIS_SOCKET must be set to a live Unix socket path" >&2
  exit 1
}

require_file "$NODE_BIN" "rsync-node-dev binary"
require_file "$TOPOLOGY_PATH" "topology file"

SUCCESS_LOG="$(mktemp)"
FAILURE_LOG="$(mktemp)"
MISSING_SOCKET="/tmp/rsync-phase5d-runtime-missing-$$.sock"

cleanup() {
  rm -f "$SUCCESS_LOG" "$FAILURE_LOG"
}
trap cleanup EXIT

echo "== runtime default backend: success path =="
echo "+ REDIS_SOCKET_PATH=$TEST_REDIS_SOCKET $NODE_BIN --transport mock --node-id 1 --topology $TOPOLOGY_PATH --iterations 0"
REDIS_SOCKET_PATH="$TEST_REDIS_SOCKET" \
  "$NODE_BIN" \
  --transport mock \
  --node-id 1 \
  --topology "$TOPOLOGY_PATH" \
  --iterations 0 >"$SUCCESS_LOG" 2>&1
cat "$SUCCESS_LOG"

require_log "$SUCCESS_LOG" "[rsync-node-dev] cache backend: redis (default), socket=$TEST_REDIS_SOCKET" \
  "runtime path reports Redis as the default cache backend"
reject_log "$SUCCESS_LOG" "cache backend: in-memory" \
  "runtime success path never reports in-memory fallback"

echo
echo "== runtime default backend: failure path =="
echo "+ REDIS_SOCKET_PATH=$MISSING_SOCKET $NODE_BIN --transport mock --node-id 1 --topology $TOPOLOGY_PATH --iterations 0"
set +e
REDIS_SOCKET_PATH="$MISSING_SOCKET" \
  "$NODE_BIN" \
  --transport mock \
  --node-id 1 \
  --topology "$TOPOLOGY_PATH" \
  --iterations 0 >"$FAILURE_LOG" 2>&1
RUN_EXIT=$?
set -e
cat "$FAILURE_LOG"

if [[ "$RUN_EXIT" -eq 0 ]]; then
  echo "FAIL: runtime should exit non-zero when Redis is unavailable" >&2
  exit 1
fi
echo "PASS: runtime failed explicitly with exit code $RUN_EXIT"

require_log "$FAILURE_LOG" "[rsync-node-dev] cache init failed:" \
  "runtime failure path reports cache initialization failure"
require_log "$FAILURE_LOG" "RedisCache init failed [UNIX($MISSING_SOCKET)" \
  "runtime failure path names the unavailable Redis socket"
reject_log "$FAILURE_LOG" "cache backend: in-memory" \
  "runtime failure path does not silently fall back to in-memory"

echo
echo "PASS: Redis runtime default-backend contract verified"
