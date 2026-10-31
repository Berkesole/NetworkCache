#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAB_DIR="${LAB_DIR:-${ROOT_DIR}/kathara/phase7a-3node-scenario-smoke}"
LOG_DIR="${LOG_DIR:-${LAB_DIR}/shared/logs}"
NODE1_LOG="${NODE1_LOG:-${LOG_DIR}/node1.log}"
NODE2_LOG="${NODE2_LOG:-${LOG_DIR}/node2.log}"
NODE3_LOG="${NODE3_LOG:-${LOG_DIR}/node3.log}"
VERIFY_WAIT_SECONDS="${VERIFY_WAIT_SECONDS:-10}"
NODE1_IP="${NODE1_IP:-10.60.0.1}"
NODE2_IP="${NODE2_IP:-10.60.0.2}"
NODE3_IP="${NODE3_IP:-10.60.0.3}"
NODE1_PORT="${NODE1_PORT:-43001}"
NODE2_PORT="${NODE2_PORT:-43002}"
NODE3_PORT="${NODE3_PORT:-43003}"
LAB_STARTED=0

cleanup() {
  if [[ "$LAB_STARTED" == "1" ]]; then
    "${ROOT_DIR}/scripts/kathara/phase7a_3node_scenario_down.sh" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

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

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
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

require_cmd kathara
require_cmd grep

step "git baseline"
cd "$ROOT_DIR"
echo "branch=$(git branch --show-current)"
echo "commit=$(git rev-parse --short HEAD)"
pass "git metadata collected"

step "start kathara lab"
"${ROOT_DIR}/scripts/kathara/phase7a_3node_scenario_up.sh"
LAB_STARTED=1
pass "kathara lab started"

step "wait for device startup"
kathara exec -d "$LAB_DIR" --wait node3 true
kathara exec -d "$LAB_DIR" --wait node2 true
kathara exec -d "$LAB_DIR" --wait node1 true
sleep "$VERIFY_WAIT_SECONDS"
pass "device startup completed"

step "capture logs"
[[ -f "$NODE1_LOG" ]] || fail "missing node1 log: $NODE1_LOG"
[[ -f "$NODE2_LOG" ]] || fail "missing node2 log: $NODE2_LOG"
[[ -f "$NODE3_LOG" ]] || fail "missing node3 log: $NODE3_LOG"

echo "--- NODE1 KEY LOGS ---"
grep -E 'startup configuration|protocol.max_hop_count|scheduled_scenario_requests|cache backend|tick=20|sent REQUEST|received DATA|Received data|listening on' "$NODE1_LOG" || true

echo "--- NODE2 KEY LOGS ---"
grep -E 'startup configuration|protocol.max_hop_count|cache backend|received REQUEST|stage REQUEST|flush AGGREGATE|sent REQUEST|received DATA|Forwarding DATA|sent DATA|listening on' "$NODE2_LOG" || true

echo "--- NODE3 KEY LOGS ---"
grep -E 'startup configuration|protocol.max_hop_count|seed_data_entries|seeded local key|cache backend|received REQUEST|sent DATA|listening on' "$NODE3_LOG" || true
pass "logs captured"

step "assert acceptance"
require_log "$NODE1_LOG" '[rsync-node-dev] startup configuration' 'node1 started rsync-node-dev'
require_log "$NODE2_LOG" '[rsync-node-dev] startup configuration' 'node2 started rsync-node-dev'
require_log "$NODE3_LOG" '[rsync-node-dev] startup configuration' 'node3 started rsync-node-dev'
require_log "$NODE1_LOG" 'protocol.max_hop_count: 4' 'node1 applied protocol max_hop_count from topology'
require_log "$NODE1_LOG" 'request_keys: 0' 'node1 did not use explicit startup request overrides'
require_log "$NODE1_LOG" 'scheduled_scenario_requests: 1' 'node1 derived one scheduled request from scenario'
require_log "$NODE1_LOG" '[rsync-node-dev] tick=20 firing scenario request for 1 key(s)' 'node1 fired the scheduled runtime request'
require_log "$NODE3_LOG" 'seed_data_entries: 1' 'node3 derived local seed data from scenario'
require_log "$NODE3_LOG" '[rsync-node-dev] seeded local key=demo_key value_size=16' 'node3 seeded demo_key from scenario'
require_log "$NODE1_LOG" '[rsync-node-dev] cache backend: redis (default)' 'node1 used Redis as cache backend'
require_log "$NODE2_LOG" '[rsync-node-dev] cache backend: redis (default)' 'node2 used Redis as cache backend'
require_log "$NODE3_LOG" '[rsync-node-dev] cache backend: redis (default)' 'node3 used Redis as cache backend'
require_log "$NODE1_LOG" "listening on ${NODE1_IP}:${NODE1_PORT}" 'node1 uses Kathara loopback endpoint'
require_log "$NODE2_LOG" "listening on ${NODE2_IP}:${NODE2_PORT}" 'node2 uses Kathara loopback endpoint'
require_log "$NODE3_LOG" "listening on ${NODE3_IP}:${NODE3_PORT}" 'node3 uses Kathara loopback endpoint'
require_log "$NODE1_LOG" 'Broadcast REQUEST for 1 key(s) (no SyncTable hit)' 'node1 used broadcast fallback on the first hop'
require_log "$NODE1_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node1 sent REQUEST to node2 over TCP'
require_log "$NODE2_LOG" 'received REQUEST packet from Node 1 to Node 2 over TCP' 'node2 received the first-hop REQUEST with its actual next-hop destination'
require_log "$NODE2_LOG" 'handleRequest(): stage REQUEST for Node 3 via broadcast-minus-prev-hop (hop=1)' 'node2 staged the downstream request during processOne'
require_log "$NODE2_LOG" 'flush AGGREGATE to Node 3 (count=1, hop=1)' 'node2 flushed the staged request toward node3'
require_log "$NODE2_LOG" 'sent REQUEST packet to Node 3 over TCP' 'node2 forwarded REQUEST to node3 over TCP'
require_log "$NODE3_LOG" 'received REQUEST packet from Node 2 to Node 3 over TCP' 'node3 received REQUEST from node2'
require_log "$NODE3_LOG" 'sent DATA packet to Node 2 over TCP' 'node3 sent DATA back to node2 over TCP'
require_log "$NODE2_LOG" 'received DATA packet from Node 3 to Node 2 over TCP' 'node2 received DATA from node3'
require_log "$NODE2_LOG" 'Forwarding DATA for key=demo_key to Node 1' 'node2 forwarded DATA back to node1'
require_log "$NODE2_LOG" 'sent DATA packet to Node 1 over TCP' 'node2 relayed DATA to node1 over TCP'
require_log "$NODE1_LOG" 'received DATA packet from Node 2 to Node 1 over TCP' 'node1 received relayed DATA from node2'
require_log "$NODE1_LOG" '[App] Received data for key=demo_key, size=16, content="DEMO_VALUE_3NODE"' 'application received the scenario value on node1'
reject_log "$NODE1_LOG" 'firing explicit startup request' 'node1 did not use explicit startup request mode'
reject_log "$NODE2_LOG" 'sent REQUEST packet to Node 1 over TCP' 'node2 did not bounce the request upstream'
reject_log "$NODE1_LOG" '[rsync-node-dev] cache backend: in-memory' 'node1 did not fall back to in-memory cache'

step "result"
echo "PASS: phase7a Kathara 3-node scenario smoke succeeded"
