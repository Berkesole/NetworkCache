#!/usr/bin/env bash
# Minimal real-process fault chain for the finite-cache experiment.
#
# It distinguishes two very different outcomes:
#   * the HARNESS failed (assertions did not hold, readiness timed out, the
#     process was killed/timed out, a command is missing, the config is wrong,
#     or the validator rejected for an unrelated reason) -> the whole script
#     FAILS;
#   * the harness CORRECTLY OBSERVED the target failing (exact expected exit
#     code, all required assertions pass, the expected fatal marker exists, and
#     the validator rejects WITH that marker as the reason) -> PASS.
#
# Modes:
#   positive (default)  the expected fault path must be observed and PASS
#   reverse             a required assertion is deliberately forced to fail; the
#                       chain must then FAIL (guards against false-pass)
#
# Usage: run_process_fault_chain.sh [build_dir] [acceptance_dir] [positive|reverse]

set -euo pipefail

BUILD_DIR="${1:-/app/build}"
ACCEPT_DIR="${2:-/acceptance}"
MODE="${3:-positive}"
IDENTITY="${RSYNC_REDIS_INSTANCE_ID:-finite-cache-exp}"
OUT="$ACCEPT_DIR/process_fault_chain_${MODE}.txt"
EVIDENCE="$ACCEPT_DIR/process_fault_chain_${MODE}.jsonl"
mkdir -p "$ACCEPT_DIR"
: > "$OUT"
: > "$EVIDENCE"

TMP_DIR="$(mktemp -d /tmp/rsync-fault-chain.XXXXXX)"
SOCKET="$TMP_DIR/redis.sock"
CONF="$TMP_DIR/redis.conf"
RUN_DIR="$TMP_DIR/run"
REDIS_PID=""
CHAIN_PID=""
CHAIN_RC=""
FAILURES=0

cleanup() {
    if [[ -n "$CHAIN_PID" ]] && kill -0 "$CHAIN_PID" 2>/dev/null; then
        kill -9 "$CHAIN_PID" 2>/dev/null || true
        wait "$CHAIN_PID" 2>/dev/null || true
    fi
    if [[ -n "$REDIS_PID" ]] && kill -0 "$REDIS_PID" 2>/dev/null; then
        kill "$REDIS_PID" 2>/dev/null || true
        wait "$REDIS_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

fail() { echo "CHAIN_HARNESS_FAILURE: $1" | tee -a "$OUT"; FAILURES=$((FAILURES + 1)); }

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
    for _ in $(seq 1 100); do [[ -S "$SOCKET" ]] && return 0; sleep 0.1; done
    return 1
}

# --------------------------- A. initialization failure --------------------- #
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
[[ "$INIT_RC" -eq 1 ]] || fail "init expected exit 1, got $INIT_RC"
grep -q "cache init failed" "$TMP_DIR/init.log" \
    || fail "init did not report a cache init failure"
tail -3 "$TMP_DIR/init.log" | tee -a "$OUT"

# ------------------------------ B. runtime failure ------------------------- #
echo "== B. runtime failure (real Redis killed while the cache process serves) ==" | tee -a "$OUT"
mkdir -p "$RUN_DIR/control/fatal" "$RUN_DIR/traces"
start_redis || fail "dedicated redis did not start"

export RSYNC_FAULT_CHAIN_FORCE_FAIL=""
if [[ "$MODE" == "reverse" ]]; then
    export RSYNC_FAULT_CHAIN_FORCE_FAIL="store_poisoned"
    echo "reverse mode: forcing assertion failure: $RSYNC_FAULT_CHAIN_FORCE_FAIL" | tee -a "$OUT"
fi

"$BUILD_DIR/test_redis_acceptance_live" fault-chain "$SOCKET" "$IDENTITY" \
    "$RUN_DIR" "$EVIDENCE" > "$TMP_DIR/chain.log" 2>&1 &
CHAIN_PID=$!

# Readiness must be proven, not assumed.
READY=0
for _ in $(seq 1 200); do
    if [[ -f "$RUN_DIR/control/ready" ]]; then READY=1; break; fi
    if ! kill -0 "$CHAIN_PID" 2>/dev/null; then break; fi
    sleep 0.1
done
if [[ "$READY" -ne 1 ]]; then
    fail "chain process never signalled readiness (timeout)"
else
    kill "$REDIS_PID"
    wait "$REDIS_PID" 2>/dev/null || true
    REDIS_PID=""
    rm -f "$SOCKET"
    touch "$RUN_DIR/control/kill-redis"
fi

set +e
for _ in $(seq 1 60); do
    if ! kill -0 "$CHAIN_PID" 2>/dev/null; then break; fi
    sleep 0.5
done
if kill -0 "$CHAIN_PID" 2>/dev/null; then
    kill -9 "$CHAIN_PID" 2>/dev/null || true
    wait "$CHAIN_PID" 2>/dev/null || true
    CHAIN_RC=137
else
    wait "$CHAIN_PID"
    CHAIN_RC=$?
fi
CHAIN_PID=""
set -e
echo "chain_exit_code=$CHAIN_RC" | tee -a "$OUT"
cat "$TMP_DIR/chain.log" | tee -a "$OUT"

# The harness must have reported a specific failure reason, and the driver must
# have exited with the agreed code for "observed target failure" (3). Anything
# else (1 harness failure, 137 SIGKILL, 127 missing command, timeout) is not an
# observed target failure.
if [[ "$FAILURES" -eq 0 ]]; then
    [[ "$CHAIN_RC" -eq 3 ]] || fail "chain expected exit 3 (observed failure), got $CHAIN_RC"
fi

# Every required assertion must be present AND pass.
for assertion in precondition_owner_servable orchestrator_killed_redis \
                 backend_fault_is_indeterminate store_poisoned second_call_refused; do
    if ! grep -q "\"name\":\"$assertion\",\"status\":\"pass\"" "$EVIDENCE"; then
        fail "required assertion missing or not passing: $assertion"
    fi
done
# The expected fault reason must be recorded.
grep -q "Broken pipe\|result unknown" "$EVIDENCE" \
    || fail "expected indeterminate reason not recorded"
[[ -f "$RUN_DIR/control/fatal/node1.backend-indeterminate" ]] \
    || fail "expected fatal marker not written by the target harness"
# ---------------------------- C. validator rejection ----------------------- #
echo "== C. validator rejection ==" | tee -a "$OUT"
printf '{"event":"experiment_request_start","experiment_request_index":"req-1","key":"fault-key","node":1}\n' \
    >> "$RUN_DIR/traces/node1.jsonl"
printf '{"event":"app_receive","key":"fault-key","value_bytes":"64","node":1}\n' \
    >> "$RUN_DIR/traces/node1.jsonl"
printf '{"event":"experiment_complete","completed":"100","node":1}\n' \
    >> "$RUN_DIR/traces/node1.jsonl"
set +e
python3 /app/experiments/concurrency_finite_cache_v1/validate_run.py \
    --backend-gate "$RUN_DIR" > "$TMP_DIR/gate.log" 2>&1
GATE_RC=$?
set -e
cat "$TMP_DIR/gate.log" | tee -a "$OUT"
echo "backend_gate_exit_code=$GATE_RC" | tee -a "$OUT"
[[ "$GATE_RC" -eq 2 ]] || fail "validator expected exit 2, got $GATE_RC"
# Rejection must carry the EXPECTED reason, not an unrelated one.
grep -q "fatal_marker:node1.backend-indeterminate" "$TMP_DIR/gate.log" \
    || fail "validator rejected without the expected fatal marker reason"

# ------------------------------- verdict ----------------------------------- #
if [[ "$MODE" == "reverse" ]]; then
    # A deliberately broken assertion must make the whole chain fail.
    if [[ "$FAILURES" -eq 0 ]]; then
        echo "REVERSE_FAILED: forced assertion failure did not fail the chain" | tee -a "$OUT"
        exit 1
    fi
    echo "REVERSE_PASS: forced assertion failure correctly failed the chain" | tee -a "$OUT"
    exit 0
fi

if [[ "$FAILURES" -eq 0 ]]; then
    echo "PROCESS_FAULT_CHAIN_PASS" | tee -a "$OUT"
    exit 0
fi
echo "PROCESS_FAULT_CHAIN_FAIL ($FAILURES checks)" | tee -a "$OUT"
exit 1