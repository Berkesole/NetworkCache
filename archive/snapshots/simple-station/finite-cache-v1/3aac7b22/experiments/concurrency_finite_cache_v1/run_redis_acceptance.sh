#!/usr/bin/env bash
# Real-Redis acceptance for the finite-cache experiment.
#
# Starts a DEDICATED redis-server in a private temp directory:
#   * Unix socket only (port 0), never a public TCP listener;
#   * maxmemory 0 / maxmemory-policy noeviction (the application owns the budget);
#   * no persistence; every key belongs to this instance only.
# It never contacts a system/default Redis, never flushes another instance, and
# stops only the PID it started (no pkill / no broad cleanup).
#
# Usage: run_redis_acceptance.sh [build_dir] [acceptance_dir]
# Typically executed inside the experiment image, where redis-server and the
# compiled binaries are present.

set -euo pipefail

BUILD_DIR="${1:-/app/build}"
ACCEPT_DIR="${2:-/acceptance}"
IDENTITY="${RSYNC_REDIS_INSTANCE_ID:-finite-cache-exp}"
EVIDENCE="$ACCEPT_DIR/redis_acceptance.jsonl"
SUMMARY="$ACCEPT_DIR/redis_acceptance_summary.txt"

mkdir -p "$ACCEPT_DIR"
: > "$EVIDENCE"
: > "$SUMMARY"

TMP_DIR="$(mktemp -d /tmp/rsync-redis-accept.XXXXXX)"
SOCKET="$TMP_DIR/redis.sock"
CONF="$TMP_DIR/redis.conf"
PIDFILE="$TMP_DIR/redis.pid"
LOG="$TMP_DIR/redis.log"
REDIS_PID=""

cleanup() {
    if [[ -n "$REDIS_PID" ]] && kill -0 "$REDIS_PID" 2>/dev/null; then
        kill "$REDIS_PID" 2>/dev/null || true
        wait "$REDIS_PID" 2>/dev/null || true
    fi
    # Only our own temp directory is removed; acceptance evidence is preserved.
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

cat > "$CONF" <<EOF
port 0
unixsocket $SOCKET
unixsocketperm 770
bind 127.0.0.1
protected-mode no
daemonize no
supervised no
dir $TMP_DIR
pidfile $PIDFILE
logfile $LOG
save ""
appendonly no
maxmemory 0
maxmemory-policy noeviction
EOF

start_redis() {
    redis-server "$CONF" &
    REDIS_PID=$!
    for _ in $(seq 1 100); do
        if [[ -S "$SOCKET" ]]; then
            return 0
        fi
        sleep 0.1
    done
    echo "redis-server did not create $SOCKET" >&2
    cat "$LOG" >&2 || true
    return 1
}

echo "target redis-server: $(command -v redis-server)" | tee -a "$SUMMARY"
echo "socket: $SOCKET" | tee -a "$SUMMARY"
start_redis
echo "redis_version: $(redis-cli -s "$SOCKET" INFO server | grep -m1 '^redis_version:' || true)" \
    | tee -a "$SUMMARY"

# 1. Existing component contract test against this exact instance.
TEST_REDIS_SOCKET="$SOCKET" "$BUILD_DIR/test_redis_cache_contract" \
    | tee -a "$SUMMARY"

# 2. Live acceptance driver: startup rules, namespaces, LRU/budget, accounting.
"$BUILD_DIR/test_redis_acceptance_live" run "$SOCKET" "$IDENTITY" "$EVIDENCE" \
    | tee -a "$SUMMARY"

BASELINE_RUN_ID="$(redis-cli -s "$SOCKET" INFO server | grep -m1 '^run_id:' | cut -d: -f2 | tr -d '\r')"
BASELINE_EVICTED="$(redis-cli -s "$SOCKET" INFO stats | grep -m1 '^evicted_keys:' | cut -d: -f2 | tr -d '\r')"
echo "baseline run_id=$BASELINE_RUN_ID evicted_keys=$BASELINE_EVICTED" | tee -a "$SUMMARY"

# 3. Restart the instance and confirm the monitor detects it.
kill "$REDIS_PID"
wait "$REDIS_PID" 2>/dev/null || true
rm -f "$SOCKET"
start_redis

"$BUILD_DIR/test_redis_acceptance_live" restart-check "$SOCKET" "$IDENTITY" \
    "$BASELINE_RUN_ID" "$BASELINE_EVICTED" "$EVIDENCE" \
    | tee -a "$SUMMARY"

echo "REDIS_ACCEPTANCE_PASS" | tee -a "$SUMMARY"