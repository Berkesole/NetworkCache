#!/usr/bin/env bash
# Real rsync-node-dev fault chain (gap 6).
#
# Uses an isolated Redis, a controlled RESP proxy, and the ACTUAL application
# process. The application itself produces the failure evidence; the driver
# never fabricates fatal files.
#
#   A. runtime fault: the app initializes against Redis through the proxy, enters
#      the serving loop, then the proxy drops the reply of a real owner GET.
#      The app must exit non-zero with control/fatal/...backend-indeterminate and
#      the validator must reject with that reason.
#   B. initialization seeding fault: the connection succeeds (PING forwarded),
#      then the proxy drops the reply of the seeding SET. The app must NOT enter
#      normal service, must exit non-zero, and must leave the same fatal marker.
#   C. response-loss window: because the proxy forwards the command BEFORE
#      dropping the reply, the proxy log proves Redis executed it, and an
#      independent connection verifies the server-side state. This is the
#      "executed but response lost" window, not a generic broken pipe.
#
# Usage: run_node_fault_chain.sh <build_dir> <acceptance_dir> <source_sha> <image_id>

set -euo pipefail

BUILD_DIR="${1:-/app/build}"
ACCEPT_ROOT="${2:-/acceptance}"
SOURCE_SHA="${3:-unknown}"
IMAGE_ID="${4:-unknown}"
IDENTITY="${RSYNC_REDIS_INSTANCE_ID:-finite-cache-exp}"
TAG="$(date -u +%Y%m%dT%H%M%SZ)-node-${SOURCE_SHA:0:8}"
EVIDENCE="$ACCEPT_ROOT/$TAG"
if [[ -e "$EVIDENCE" ]]; then
    echo "refusing to overwrite existing evidence directory: $EVIDENCE" >&2
    exit 3
fi
mkdir -p "$EVIDENCE"
OUT="$EVIDENCE/node_fault_chain.txt"
: > "$OUT"

TMP_DIR="$(mktemp -d /tmp/rsync-node-chain.XXXXXX)"
REDIS_SOCK="$TMP_DIR/redis.sock"
PROXY_SOCK="$TMP_DIR/proxy.sock"
CONF="$TMP_DIR/redis.conf"
REDIS_PID=""
PROXY_PID=""
NODE_PID=""
FAILURES=0

cleanup() {
    for pid in "$NODE_PID" "$PROXY_PID" "$REDIS_PID"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() { echo "NODE_CHAIN_HARNESS_FAILURE: $1" | tee -a "$OUT"; FAILURES=$((FAILURES + 1)); }

cat > "$CONF" <<EOF
port 0
unixsocket $REDIS_SOCK
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

redis-server "$CONF" &
REDIS_PID=$!
for _ in $(seq 1 100); do [[ -S "$REDIS_SOCK" ]] && break; sleep 0.1; done
[[ -S "$REDIS_SOCK" ]] || { fail "dedicated redis did not start"; exit 1; }

start_proxy() {
    local control_dir="$1" log="$2"
    mkdir -p "$control_dir"
    # Remove a stale socket from a previous proxy first: merely testing -S would
    # accept the old file and let the app connect before the new proxy binds.
    rm -f "$PROXY_SOCK"
    python3 /app/experiments/concurrency_finite_cache_v1/redis_fault_proxy.py \
        "$PROXY_SOCK" "$REDIS_SOCK" "$control_dir" "$log" &
    PROXY_PID=$!
    for _ in $(seq 1 100); do
        if [[ -S "$PROXY_SOCK" ]] && grep -q "proxy_started" "$log" 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

node_env_common=(
    "REDIS_SOCKET_PATH=$PROXY_SOCK"
    "RSYNC_CACHE_POLICY=lru"
    "RSYNC_REPLICA_BUDGET_BYTES=1048576"
)

# ============================ A. runtime fault ============================== #
echo "== A. runtime fault through a real owner GET ==" | tee -a "$OUT"
A_DIR="$EVIDENCE/A_runtime"
A_CONTROL="$A_DIR/proxycontrol"
mkdir -p "$A_DIR/run/control/commands/node1" "$A_DIR/run/traces"
start_proxy "$A_CONTROL" "$A_DIR/proxy.jsonl" || fail "proxy did not start"

HASH="node-chain-hash"
KEY="fault-owner"
env "${node_env_common[@]}" \
    RSYNC_EXPERIMENT_CONTROL_DIR="$A_DIR/run/control" \
    RSYNC_EXPERIMENT_SCHEDULE_HASH="$HASH" \
    RSYNC_EVENT_TRACE=1 RSYNC_EVENT_TRACE_PATH="$A_DIR/run/traces/node1.jsonl" \
    "$BUILD_DIR/rsync-node-dev" --node-id 1 --transport mock \
    --topology /app/config/3-node-topology.yaml --iterations 400 --sleep-ms 50 \
    --seed "$KEY=seeded-value" > "$A_DIR/node.log" 2>&1 &
NODE_PID=$!

READY=0
for _ in $(seq 1 200); do
    [[ -f "$A_DIR/run/control/ready/node1.ready" ]] && { READY=1; break; }
    if ! kill -0 "$NODE_PID" 2>/dev/null; then break; fi
    sleep 0.1
done
[[ "$READY" -eq 1 ]] || fail "A: app never entered the serving loop"
if [[ "$READY" -eq 1 ]]; then
    echo "A: app entered serving loop" | tee -a "$OUT"
    touch "$A_CONTROL/drop_on_command:GET"
    printf '%s\tnc\t%s\t1\t12\t0123456789abcdef\t%s\n' \
        "req-A" "$KEY" "$HASH" > "$A_DIR/run/control/commands/node1/0001.tsv"
fi

set +e
for _ in $(seq 1 120); do kill -0 "$NODE_PID" 2>/dev/null || break; sleep 0.5; done
if kill -0 "$NODE_PID" 2>/dev/null; then
    kill -9 "$NODE_PID" 2>/dev/null || true; wait "$NODE_PID" 2>/dev/null || true; A_RC=137
else
    wait "$NODE_PID"; A_RC=$?
fi
NODE_PID=""
set -e
echo "A_exit_code=$A_RC" | tee -a "$OUT"
kill "$PROXY_PID" 2>/dev/null || true; wait "$PROXY_PID" 2>/dev/null || true; PROXY_PID=""
cp "$A_DIR/node.log" "$A_DIR/node.log.keep" 2>/dev/null || true

[[ "$A_RC" -ne 0 && "$A_RC" -ne 137 ]] || fail "A: expected a clean non-zero exit, got $A_RC"
[[ -f "$A_DIR/run/control/fatal/node1.backend-indeterminate" ]] \
    || fail "A: app did not write the expected fatal marker"
grep -q "\"command\": \"GET\", \"forwarded\": true, \"reply_dropped\": true" "$A_DIR/proxy.jsonl" \
    || fail "A: proxy did not forward a GET and drop its reply"
if grep -q "User wants file: $KEY" "$A_DIR/node.log"; then :; else
    # requestFile prints this; absence means the command never fired.
    fail "A: the trigger request was not observed by the app"
fi

# ======================= B. initialization seeding fault ==================== #
echo "== B. initialization seeding fault ==" | tee -a "$OUT"
B_DIR="$EVIDENCE/B_seeding"
B_CONTROL="$B_DIR/proxycontrol"
mkdir -p "$B_DIR/run/control"
start_proxy "$B_CONTROL" "$B_DIR/proxy.jsonl" || fail "proxy did not start"
touch "$B_CONTROL/drop_on_command:SET"

SEED_KEY="seed-key"
env "${node_env_common[@]}" \
    RSYNC_EXPERIMENT_CONTROL_DIR="$B_DIR/run/control" \
    RSYNC_EXPERIMENT_SCHEDULE_HASH="$HASH" \
    "$BUILD_DIR/rsync-node-dev" --node-id 1 --transport mock \
    --topology /app/config/3-node-topology.yaml --iterations 20 --sleep-ms 50 \
    --seed "$SEED_KEY=seeded-value" > "$B_DIR/node.log" 2>&1 &
NODE_PID=$!
set +e
for _ in $(seq 1 120); do kill -0 "$NODE_PID" 2>/dev/null || break; sleep 0.5; done
if kill -0 "$NODE_PID" 2>/dev/null; then
    kill -9 "$NODE_PID" 2>/dev/null || true; wait "$NODE_PID" 2>/dev/null || true; B_RC=137
else
    wait "$NODE_PID"; B_RC=$?
fi
NODE_PID=""
set -e
kill "$PROXY_PID" 2>/dev/null || true; wait "$PROXY_PID" 2>/dev/null || true; PROXY_PID=""
echo "B_exit_code=$B_RC" | tee -a "$OUT"
[[ "$B_RC" -ne 0 && "$B_RC" -ne 137 ]] || fail "B: expected a clean non-zero exit, got $B_RC"
[[ -f "$B_DIR/run/control/fatal/node1.backend-indeterminate" ]] \
    || fail "B: app did not write the expected fatal marker"
[[ -f "$B_DIR/run/control/ready/node1.ready" ]] \
    && fail "B: app must not enter normal service after a seeding fault"
grep -q "\"command\": \"SET\", \"forwarded\": true, \"reply_dropped\": true" "$B_DIR/proxy.jsonl" \
    || fail "B: proxy did not forward a SET and drop its reply"

# ===================== C. response-loss window proof ======================== #
echo "== C. response-loss window: server executed, reply dropped ==" | tee -a "$OUT"
# The proxy forwarded the SET before dropping the reply, so Redis executed it.
SEEDED_VALUE="$(redis-cli -s "$REDIS_SOCK" GET "owner:$SEED_KEY" | tr -d '\r')"
echo "independent_GET_owner_$SEED_KEY=$SEEDED_VALUE" | tee -a "$OUT"
if [[ "$SEEDED_VALUE" == "seeded-value" ]]; then
    echo "RESPONSE_LOSS_WINDOW_VERIFIED=1" | tee -a "$OUT"
else
    fail "C: Redis did not execute the SET whose reply was dropped"
fi

# validator rejects with the expected marker reason for both A and B
for case_dir in "$A_DIR" "$B_DIR"; do
    set +e
    python3 /app/experiments/concurrency_finite_cache_v1/validate_run.py \
        --backend-gate "$case_dir/run" > "$case_dir/gate.log" 2>&1
    rc=$?
    set -e
    [[ "$rc" -eq 2 ]] || fail "$(basename "$case_dir"): validator did not reject (rc=$rc)"
    grep -q "fatal_marker:node1.backend-indeterminate" "$case_dir/gate.log" \
        || fail "$(basename "$case_dir"): rejection lacks the expected reason"
done

{
    echo "evidence_manifest_begin $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    (cd "$EVIDENCE" && find . -type f -print0 | sort -z | xargs -0 sha256sum)
    echo "evidence_manifest_end"
} > "$EVIDENCE/evidence-manifest.txt"

if [[ "$FAILURES" -eq 0 ]]; then
    echo "NODE_FAULT_CHAIN_PASS" | tee -a "$OUT"
    exit 0
fi
echo "NODE_FAULT_CHAIN_FAIL ($FAILURES checks)" | tee -a "$OUT"
exit 1