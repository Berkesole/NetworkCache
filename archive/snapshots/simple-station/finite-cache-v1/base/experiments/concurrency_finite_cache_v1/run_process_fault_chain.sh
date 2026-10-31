#!/usr/bin/env bash
# Minimal real-process fault chain for the finite-cache experiment.
#
# Verifies, with real processes (no static inspection):
#   A. initialization failure: rsync-node-dev with an unreachable Redis exits
#      non-zero and records the failure evidence;
#   B. runtime failure: rsync-node-dev is driven through experiment control
#      commands while the dedicated Redis instance is killed, so a backend
#      fault surfaces as a Node exception, a control/fatal marker and a
#      non-zero exit;
#   C. validator rejection: the backend gate refuses the run even when 100%
#      request-completion records are present.
#
# It uses only a private temp Redis (port 0, unix socket, maxmemory 0,
# noeviction) and stops only PIDs it started.
#
# Usage: run_process_fault_chain.sh [build_dir] [acceptance_dir]

set -euo pipefail

BUILD_DIR="${1:-/app/build}"
ACCEPT_DIR="${2:-/acceptance}"
IDENTITY="${RSYNC_REDIS_INSTANCE_ID:-finite-cache-exp}"
OUT="$ACCEPT_DIR/process_fault_chain.txt"
mkdir -p "$ACCEPT_DIR"
: > "$OUT"

TMP_DIR="$(mktemp -d /tmp/rsync-fault-chain.XXXXXX)"
SOCKET="$TMP_DIR/redis.sock"
CONF="$TMP_DIR/redis.conf"
RUN_DIR="$TMP_DIR/run"
REDIS_PID=""
NODE_PID=""

cleanup() {
    for pid in "$NODE_PID" "$REDIS_PID"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
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
logfile $TMP_DIR/redis.log
save ""
appendonly no
maxmemory 0
maxmemory-policy noeviction
EOF

start_redis() {
    redis-server "$CONF" &
    REDIS_PID=$!
    for _ in $(seq 1 100); do
        [[ -S "$SOCKET" ]] && return 0
        sleep 0.1
    done
    echo "redis-server did not create $SOCKET" >&2
    return 1
}

echo "== A. initialization failure (unreachable Redis) ==" | tee -a "$OUT"
set +e
REDIS_SOCKET_PATH="$TMP_DIR/missing.sock" RSYNC_CACHE_POLICY=lru \
    RSYNC_REPLICA_BUDGET_BYTES=1048576 \
    "$BUILD_DIR/rsync-node-dev" --node-id 1 --transport mock \
    --topology /app/config/3-node-topology.yaml --iterations 1 \
    --seed init-owner=seeded-value > "$TMP_DIR/init.log" 2>&1
INIT_RC=$?
set -e
echo "init_exit_code=$INIT_RC" | tee -a "$OUT"
tail -3 "$TMP_DIR/init.log" | tee -a "$OUT"
[[ "$INIT_RC" -ne 0 ]] && echo "INIT_FAILURE_EVIDENCE=nonzero_exit" | tee -a "$OUT"

echo "== B. runtime failure (real Redis killed while a real cache process serves) ==" | tee -a "$OUT"
mkdir -p "$RUN_DIR/control/fatal" "$RUN_DIR/traces"
start_redis

REDIS_SOCKET_PATH="$SOCKET" "$BUILD_DIR/test_redis_acceptance_live" \
    fault-chain "$SOCKET" "$IDENTITY" "$RUN_DIR" "$ACCEPT_DIR/process_fault_chain.jsonl" \
    > "$TMP_DIR/chain.log" 2>&1 &
NODE_PID=$!

# Wait until the process has seeded an owner key and is about to serve it, then
# kill only our dedicated Redis and let the read hit the dead backend.
for _ in $(seq 1 200); do
    [[ -f "$RUN_DIR/control/ready" ]] && break
    sleep 0.1
done
kill "$REDIS_PID"
wait "$REDIS_PID" 2>/dev/null || true
REDIS_PID=""
rm -f "$SOCKET"
touch "$RUN_DIR/control/kill-redis"

set +e
for _ in $(seq 1 60); do
    if ! kill -0 "$NODE_PID" 2>/dev/null; then break; fi
    sleep 0.5
done
if kill -0 "$NODE_PID" 2>/dev/null; then
    kill "$NODE_PID" 2>/dev/null || true
    wait "$NODE_PID" 2>/dev/null || true
    NODE_RC=124
else
    wait "$NODE_PID"
    NODE_RC=$?
fi
NODE_PID=""
set -e
echo "runtime_exit_code=$NODE_RC" | tee -a "$OUT"
tail -8 "$TMP_DIR/chain.log" | tee -a "$OUT"
ls -1 "$RUN_DIR/control/fatal" 2>/dev/null | tee -a "$OUT" || true
[[ "$NODE_RC" -ne 0 ]] && echo "RUNTIME_FAILURE_EVIDENCE=nonzero_exit" | tee -a "$OUT"

echo "== C. validator rejection ==" | tee -a "$OUT"
# 100% completion evidence must not override the failure.
printf '{"event":"experiment_request_start","experiment_request_index":"req-1","key":"fault-key","node":1}\n' \
    >> "$RUN_DIR/traces/node1.jsonl"
printf '{"event":"app_receive","key":"fault-key","value_bytes":"64","node":1}\n' \
    >> "$RUN_DIR/traces/node1.jsonl"
printf '{"event":"experiment_complete","completed":"100","node":1}\n' \
    >> "$RUN_DIR/traces/node1.jsonl"
set +e
python3 /app/experiments/concurrency_finite_cache_v1/validate_run.py \
    --backend-gate "$RUN_DIR" | tee -a "$OUT"
GATE_RC=${PIPESTATUS[0]}
set -e
echo "backend_gate_exit_code=$GATE_RC" | tee -a "$OUT"
[[ "$GATE_RC" -eq 2 ]] && echo "VALIDATOR_REJECTED=1" | tee -a "$OUT"

if [[ "$INIT_RC" -ne 0 && "$NODE_RC" -ne 0 && "$GATE_RC" -eq 2 ]]; then
    echo "PROCESS_FAULT_CHAIN_PASS" | tee -a "$OUT"
    exit 0
fi
echo "PROCESS_FAULT_CHAIN_FAIL" | tee -a "$OUT"
exit 1