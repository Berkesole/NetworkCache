#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CACHE_TEST_BIN="${1:-${ROOT_DIR}/build/test_redis_cache_contract}"
NODE_BIN="${2:-${ROOT_DIR}/build/rsync-node-dev}"
REDIS_SERVER_BIN="${REDIS_SERVER_BIN:-redis-server}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "FAIL: missing required command: $1" >&2
    exit 1
  }
}

require_file() {
  local path="$1"
  local label="$2"
  [[ -e "$path" ]] || {
    echo "FAIL: missing ${label}: $path" >&2
    exit 1
  }
}

TMP_DIR="$(mktemp -d)"
REDIS_CONF="$TMP_DIR/redis.conf"
REDIS_PID="$TMP_DIR/redis.pid"
REDIS_LOG="$TMP_DIR/redis.log"
TEST_REDIS_SOCKET="$TMP_DIR/redis.sock"

cleanup() {
  if [[ -f "$REDIS_PID" ]]; then
    kill "$(cat "$REDIS_PID")" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

require_cmd "$REDIS_SERVER_BIN"
require_file "$CACHE_TEST_BIN" "Redis cache contract test binary"
require_file "$NODE_BIN" "rsync-node-dev binary"

cat >"$REDIS_CONF" <<EOF
port 0
bind 127.0.0.1
protected-mode no
save ""
appendonly no
daemonize yes
pidfile $REDIS_PID
logfile $REDIS_LOG
dir $TMP_DIR
unixsocket $TEST_REDIS_SOCKET
unixsocketperm 700
EOF

echo "== start isolated Redis test server =="
echo "+ $REDIS_SERVER_BIN $REDIS_CONF"
"$REDIS_SERVER_BIN" "$REDIS_CONF"

for _ in $(seq 1 50); do
  if [[ -S "$TEST_REDIS_SOCKET" ]]; then
    break
  fi
  sleep 0.1
done

[[ -S "$TEST_REDIS_SOCKET" ]] || {
  echo "--- redis log ---" >&2
  cat "$REDIS_LOG" >&2 || true
  echo "FAIL: Redis test socket did not appear: $TEST_REDIS_SOCKET" >&2
  exit 1
}

echo
echo "== run Redis cache contract binary =="
echo "+ TEST_REDIS_SOCKET=$TEST_REDIS_SOCKET REDIS_SOCKET_PATH=$TEST_REDIS_SOCKET $CACHE_TEST_BIN"
TEST_REDIS_SOCKET="$TEST_REDIS_SOCKET" \
REDIS_SOCKET_PATH="$TEST_REDIS_SOCKET" \
  "$CACHE_TEST_BIN"

echo
echo "== run runtime default-backend verification =="
echo "+ TEST_REDIS_SOCKET=$TEST_REDIS_SOCKET $ROOT_DIR/scripts/tests/verify_redis_runtime_default.sh $NODE_BIN"
TEST_REDIS_SOCKET="$TEST_REDIS_SOCKET" \
  "$ROOT_DIR/scripts/tests/verify_redis_runtime_default.sh" "$NODE_BIN"

echo
echo "PASS: Redis suite completed successfully"
