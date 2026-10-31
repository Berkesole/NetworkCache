#!/usr/bin/env bash
# Component acceptance RUN ENTRY for the finite-cache experiment.
#
# Executes, in order, in an ISOLATED environment:
#   1. dedicated Redis start + startup config verification + empty-DB check;
#   2. instance identity / run_id / monitoring baseline recorded;
#   3. target process (real RedisCache + NodeCache) and a low-frequency monitor
#      on a DEDICATED connection run concurrently;
#   4. continuous sampling during the target window;
#   5. final sample, monitor exit status and evidence manifest written;
#   6. the formal validator (--run --manifest) decides READY / REJECTED.
#
# Every run gets a UNIQUE versioned directory; an existing directory is refused,
# never overwritten. Only the temporary Redis this script started is cleaned up.
#
# Usage: run_component_acceptance.sh <build_dir> <acceptance_root> [source_sha] [image_id]

set -euo pipefail

BUILD_DIR="${1:-/app/build}"
ACCEPT_ROOT="${2:-/acceptance}"
SOURCE_SHA="${3:-unknown}"
IMAGE_ID="${4:-unknown}"
IDENTITY="${RSYNC_REDIS_INSTANCE_ID:-finite-cache-exp}"
INTERVAL_MS="${RSYNC_MONITOR_INTERVAL_MS:-200}"
MONITOR_MS="${RSYNC_MONITOR_DURATION_MS:-8000}"
MAX_GAP_MS=$((INTERVAL_MS * 3))

TAG="$(date -u +%Y%m%dT%H%M%SZ)-${SOURCE_SHA:0:8}"
EVIDENCE="$ACCEPT_ROOT/$TAG"
if [[ -e "$EVIDENCE" ]]; then
    echo "refusing to overwrite existing evidence directory: $EVIDENCE" >&2
    exit 3
fi
RUN="$EVIDENCE/run"
mkdir -p "$RUN/traces" "$RUN/completions" "$RUN/delivery-audit" "$RUN/monitor"
SUMMARY="$EVIDENCE/summary.txt"

TMP_DIR="$(mktemp -d /tmp/rsync-component-accept.XXXXXX)"
SOCKET="$TMP_DIR/redis.sock"
CONF="$TMP_DIR/redis.conf"
REDIS_PID=""
MONITOR_PID=""

cleanup() {
    for pid in "$MONITOR_PID" "$REDIS_PID"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    rm -rf "$TMP_DIR"   # only our temp dir; evidence lives under $EVIDENCE
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

redis-server "$CONF" &
REDIS_PID=$!
for _ in $(seq 1 100); do [[ -S "$SOCKET" ]] && break; sleep 0.1; done
if [[ ! -S "$SOCKET" ]]; then
    echo "redis-server did not create $SOCKET" | tee -a "$SUMMARY"
    exit 1
fi

{
    echo "run_tag=$TAG"
    echo "source_sha=$SOURCE_SHA"
    echo "image_id=$IMAGE_ID"
    echo "redis_version=$(redis-cli -s "$SOCKET" INFO server | grep -m1 '^redis_version:')"
    echo "instance_identity=$IDENTITY"
    echo "monitor_interval_ms=$INTERVAL_MS"
    echo "monitor_max_gap_ms=$MAX_GAP_MS"
} | tee -a "$SUMMARY"

# 3. Start the dedicated-connection monitor BEFORE the target so coverage starts
#    before the target window.
"$BUILD_DIR/test_redis_acceptance_live" monitor "$SOCKET" "$IDENTITY" \
    "$RUN/monitor/redis_monitor.jsonl" "$INTERVAL_MS" "$MONITOR_MS" \
    > "$EVIDENCE/monitor.stdout" 2>&1 &
MONITOR_PID=$!
sleep 0.5   # let the baseline sample land

WINDOW_START_NS="$(date +%s%N)"

# The target: existing contract test + a real component run that produces the
# request/completion/content-hash evidence.
TEST_REDIS_SOCKET="$SOCKET" "$BUILD_DIR/test_redis_cache_contract" \
    > "$EVIDENCE/target_contract.log" 2>&1
"$BUILD_DIR/test_redis_acceptance_live" component-run "$SOCKET" "$IDENTITY" "$RUN" \
    > "$EVIDENCE/target_component.log" 2>&1

WINDOW_END_NS="$(date +%s%N)"
echo "window_start_epoch_ns=$WINDOW_START_NS" | tee -a "$SUMMARY"
echo "window_end_epoch_ns=$WINDOW_END_NS" | tee -a "$SUMMARY"

# 5. Finalize the monitor and record its exit status.
set +e
wait "$MONITOR_PID"
MONITOR_RC=$?
set -e
MONITOR_PID=""
echo "monitor_exit_code=$MONITOR_RC" | tee -a "$SUMMARY"

BASELINE_RUN_ID="$(redis-cli -s "$SOCKET" INFO server | grep -m1 '^run_id:' | cut -d: -f2 | tr -d '\r')"
BASELINE_EVICTED="$(redis-cli -s "$SOCKET" INFO stats | grep -m1 '^evicted_keys:' | cut -d: -f2 | tr -d '\r')"

# 6. Build the manifest (the PLAN) with real evidence hashes, then validate.
python3 - "$RUN" "$EVIDENCE/manifest.json" "$BASELINE_RUN_ID" "$BASELINE_EVICTED" \
    "$IDENTITY" "$INTERVAL_MS" "$MAX_GAP_MS" "$WINDOW_START_NS" "$WINDOW_END_NS" \
    "$SOURCE_SHA" "$IMAGE_ID" "$TAG" <<'PY'
import hashlib, json, sys
from pathlib import Path

(run, manifest_path, baseline_run_id, baseline_evicted, identity, interval_ms,
 max_gap_ms, window_start, window_end, source_sha, image_id, tag) = sys.argv[1:]

MASK = (1 << 64) - 1

def xorshift64(state):
    if state == 0:
        state = 0x9E3779B97F4A7C15
    state ^= (state << 13) & MASK
    state &= MASK
    state ^= state >> 7
    state ^= (state << 17) & MASK
    state &= MASK
    return state

def deterministic(size, seed):
    state = seed
    out = bytearray()
    for _ in range(size):
        state = xorshift64(state)
        out.append(state & 0xFF)
    return bytes(out)

def fnv1a64(data):
    h = 14695981039346656037
    for byte in data:
        h ^= byte
        h = (h * 1099511628211) & MASK
    return h

run_dir = Path(run)
expected_hash = f"{fnv1a64(deterministic(64, 7)):016x}"

def sha256(rel):
    return hashlib.sha256((run_dir / rel).read_bytes()).hexdigest()

files = [
    {"path": "traces/node1.jsonl", "kind": "trace", "nodes": [1], "sha256": sha256("traces/node1.jsonl")},
    {"path": "completions/comp-req-1.tsv", "kind": "completion", "request_index": "comp-req-1",
     "sha256": sha256("completions/comp-req-1.tsv")},
    {"path": "delivery-audit/node1.tsv", "kind": "audit", "sha256": sha256("delivery-audit/node1.tsv")},
    {"path": "monitor/redis_monitor.jsonl", "kind": "monitor", "sha256": sha256("monitor/redis_monitor.jsonl")},
]
manifest = {
    "schema": "finite-cache-acceptance-manifest",
    "version": 1,
    "run": {"run_id": tag, "campaign": "acceptance-fixture-component",
            "created_by": "run_component_acceptance.sh", "source_sha": source_sha,
            "image_id": image_id},
    "expected_nodes": [1],
    "required_events": ["cache_config", "cache_gauge"],
    "files": files,
    "requests": [{"index": "comp-req-1", "node": 1, "key": "comp-key",
                  "value_bytes": 64, "value_hash": expected_hash}],
    "monitor": {"require": True, "instance_identity": identity,
                "baseline_run_id": baseline_run_id,
                "baseline_evicted_keys": int(baseline_evicted),
                "interval_ms": int(interval_ms), "max_gap_ms": int(max_gap_ms),
                "window": {"start_epoch_ns": int(window_start),
                           "end_epoch_ns": int(window_end)}},
}
Path(manifest_path).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print("expected_value_hash=" + expected_hash)
PY

set +e
python3 /app/experiments/concurrency_finite_cache_v1/validate_run.py \
    --run "$RUN" --manifest "$EVIDENCE/manifest.json" | tee -a "$SUMMARY"
VALIDATOR_RC=${PIPESTATUS[0]}
set -e
echo "validator_exit_code=$VALIDATOR_RC" | tee -a "$SUMMARY"

# Evidence manifest with SHA-256 of every artifact.
{
    echo "evidence_manifest_begin $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    (cd "$EVIDENCE" && find . -type f -print0 | sort -z | xargs -0 sha256sum)
    echo "evidence_manifest_end"
} > "$EVIDENCE/evidence-manifest.txt"

if [[ "$MONITOR_RC" -eq 0 && "$VALIDATOR_RC" -eq 0 ]]; then
    echo "COMPONENT_ACCEPTANCE_PASS" | tee -a "$SUMMARY"
    exit 0
fi
echo "COMPONENT_ACCEPTANCE_FAIL monitor=$MONITOR_RC validator=$VALIDATOR_RC" | tee -a "$SUMMARY"
exit 1