#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAB_DIR="${LAB_DIR:-${ROOT_DIR}/kathara/phase5f-linear-smoke}"
LOG_DIR="${LOG_DIR:-${LAB_DIR}/shared/logs}"
NODE1_LOG="${NODE1_LOG:-${LOG_DIR}/node1.log}"
NODE2_LOG="${NODE2_LOG:-${LOG_DIR}/node2.log}"
NODE3_LOG="${NODE3_LOG:-${LOG_DIR}/node3.log}"
NODE4_LOG="${NODE4_LOG:-${LOG_DIR}/node4.log}"
VERIFY_WAIT_SECONDS="${VERIFY_WAIT_SECONDS:-26}"
NODE1_IP="${NODE1_IP:-10.50.0.1}"
NODE2_IP="${NODE2_IP:-10.50.0.2}"
NODE3_IP="${NODE3_IP:-10.50.0.3}"
NODE4_IP="${NODE4_IP:-10.50.0.4}"
NODE1_PORT="${NODE1_PORT:-42001}"
NODE2_PORT="${NODE2_PORT:-42002}"
NODE3_PORT="${NODE3_PORT:-42003}"
NODE4_PORT="${NODE4_PORT:-42004}"
LAB_STARTED=0

cleanup() {
  if [[ "$LAB_STARTED" == "1" ]]; then
    "${ROOT_DIR}/scripts/kathara/phase5f_linear_down.sh" >/dev/null 2>&1 || true
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
"${ROOT_DIR}/scripts/kathara/phase5f_linear_up.sh"
LAB_STARTED=1
pass "kathara lab started"

step "wait for device startup"
kathara exec -d "$LAB_DIR" --wait node4 true
kathara exec -d "$LAB_DIR" --wait node3 true
kathara exec -d "$LAB_DIR" --wait node2 true
kathara exec -d "$LAB_DIR" --wait node1 true
sleep "$VERIFY_WAIT_SECONDS"
pass "device startup completed"

step "capture logs"
[[ -f "$NODE1_LOG" ]] || fail "missing node1 log: $NODE1_LOG"
[[ -f "$NODE2_LOG" ]] || fail "missing node2 log: $NODE2_LOG"
[[ -f "$NODE3_LOG" ]] || fail "missing node3 log: $NODE3_LOG"
[[ -f "$NODE4_LOG" ]] || fail "missing node4 log: $NODE4_LOG"

echo "--- NODE1 KEY LOGS ---"
grep -E 'App target|sent REQUEST|received DATA|Received data|listening on|tcp transport initialized' "$NODE1_LOG" || true

echo "--- NODE2 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent REQUEST|received DATA|sent DATA|Forwarding DATA|forward REQUEST|listening on|tcp transport initialized' "$NODE2_LOG" || true

echo "--- NODE3 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent REQUEST|received DATA|sent DATA|Forwarding DATA|forward REQUEST|listening on|tcp transport initialized' "$NODE3_LOG" || true

echo "--- NODE4 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent DATA|satisfied locally|listening on|tcp transport initialized' "$NODE4_LOG" || true
pass "logs captured"

step "assert acceptance"
require_log "$NODE1_LOG" '[rsync-node-dev] startup configuration' 'node1 started rsync-node-dev'
require_log "$NODE2_LOG" '[rsync-node-dev] startup configuration' 'node2 started rsync-node-dev'
require_log "$NODE3_LOG" '[rsync-node-dev] startup configuration' 'node3 started rsync-node-dev'
require_log "$NODE4_LOG" '[rsync-node-dev] startup configuration' 'node4 started rsync-node-dev'
require_log "$NODE1_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node1 sent REQUEST to node2 over TCP'
require_log "$NODE2_LOG" 'received REQUEST packet from Node 1' 'node2 received REQUEST from node1'
require_log "$NODE2_LOG" 'handleRequest(): stage REQUEST for Node 3 via broadcast-minus-prev-hop' 'node2 forwarded downstream without bouncing upstream'
require_log "$NODE2_LOG" 'sent REQUEST packet to Node 3 over TCP' 'node2 sent REQUEST to node3 over TCP'
require_log "$NODE3_LOG" 'received REQUEST packet from Node 2' 'node3 received REQUEST from node2'
require_log "$NODE3_LOG" 'handleRequest(): stage REQUEST for Node 4 via broadcast-minus-prev-hop' 'node3 forwarded downstream without bouncing upstream'
require_log "$NODE3_LOG" 'sent REQUEST packet to Node 4 over TCP' 'node3 sent REQUEST to node4 over TCP'
require_log "$NODE4_LOG" 'received REQUEST packet from Node 3' 'node4 received REQUEST from node3'
require_log "$NODE4_LOG" 'sent DATA packet to Node 3 over TCP' 'node4 sent DATA to node3 over TCP'
require_log "$NODE3_LOG" 'received DATA packet from Node 4 to Node 3 over TCP' 'node3 received DATA from node4'
require_log "$NODE3_LOG" 'sent DATA packet to Node 2 over TCP' 'node3 relayed DATA to node2 over TCP'
require_log "$NODE2_LOG" 'received DATA packet from Node 3 to Node 2 over TCP' 'node2 received DATA from node3'
require_log "$NODE2_LOG" 'sent DATA packet to Node 1 over TCP' 'node2 relayed DATA to node1 over TCP'
require_log "$NODE1_LOG" 'received DATA packet from Node 2 to Node 1 over TCP' 'node1 received relayed DATA from node2'
require_log "$NODE1_LOG" '[App] Received data for key=demo_key, size=16, content="DEMO_VALUE_4NODE"' 'application received DEMO_VALUE_4NODE'
require_log "$NODE1_LOG" "listening on ${NODE1_IP}:${NODE1_PORT}" 'node1 uses kathara loopback endpoint'
require_log "$NODE2_LOG" "listening on ${NODE2_IP}:${NODE2_PORT}" 'node2 uses kathara loopback endpoint'
require_log "$NODE3_LOG" "listening on ${NODE3_IP}:${NODE3_PORT}" 'node3 uses kathara loopback endpoint'
require_log "$NODE4_LOG" "listening on ${NODE4_IP}:${NODE4_PORT}" 'node4 uses kathara loopback endpoint'
reject_log "$NODE1_LOG" 'received REQUEST packet from Node 2' 'node1 did not receive a bounced request from node2'
reject_log "$NODE2_LOG" 'sent REQUEST packet to Node 1 over TCP' 'node2 did not bounce the request back to node1'
reject_log "$NODE3_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node3 did not bounce the request back to node2'
reject_log "$NODE1_LOG" 'sent DATA packet to Node 2 over TCP' 'node1 did not bounce DATA back to node2'
reject_log "$NODE2_LOG" 'sent DATA packet to Node 3 over TCP' 'node2 did not bounce DATA back to node3'

step "result"
echo "PASS: phase5f Kathara linear smoke succeeded"
