#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAB_DIR="${LAB_DIR:-${ROOT_DIR}/kathara/phase6a-acyclic-branch-smoke}"
LOG_DIR="${LOG_DIR:-${LAB_DIR}/shared/logs}"
NODE1_LOG="${NODE1_LOG:-${LOG_DIR}/node1.log}"
NODE2_LOG="${NODE2_LOG:-${LOG_DIR}/node2.log}"
NODE3_LOG="${NODE3_LOG:-${LOG_DIR}/node3.log}"
NODE4_LOG="${NODE4_LOG:-${LOG_DIR}/node4.log}"
NODE5_LOG="${NODE5_LOG:-${LOG_DIR}/node5.log}"
NODE4_STARTUP_LOG="${NODE4_STARTUP_LOG:-${LOG_DIR}/node4.startup.log}"
VERIFY_WAIT_SECONDS="${VERIFY_WAIT_SECONDS:-32}"
NODE1_IP="${NODE1_IP:-10.50.0.1}"
NODE2_IP="${NODE2_IP:-10.50.0.2}"
NODE3_IP="${NODE3_IP:-10.50.0.3}"
NODE4_IP="${NODE4_IP:-10.50.0.4}"
NODE5_IP="${NODE5_IP:-10.50.0.5}"
NODE1_PORT="${NODE1_PORT:-42201}"
NODE2_PORT="${NODE2_PORT:-42202}"
NODE3_PORT="${NODE3_PORT:-42203}"
NODE4_PORT="${NODE4_PORT:-42204}"
NODE5_PORT="${NODE5_PORT:-42205}"
CACHE_ASSERT_KEY="${CACHE_ASSERT_KEY:-demo_key}"
CACHE_ASSERT_VALUE="${CACHE_ASSERT_VALUE:-DEMO_VALUE_5NODE_BRANCH}"
DISABLE_NODE4_APP_FILE="${DISABLE_NODE4_APP_FILE:-${LAB_DIR}/shared/disable-node4-app}"
LAB_STARTED=0

cleanup() {
  rm -f "$DISABLE_NODE4_APP_FILE"
  if [[ "$LAB_STARTED" == "1" ]]; then
    "${ROOT_DIR}/scripts/kathara/phase6a_acyclic_branch_down.sh" >/dev/null 2>&1 || true
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

step "prepare isolation scenario (disable node4 app)"
mkdir -p "${LAB_DIR}/shared"
: >"$DISABLE_NODE4_APP_FILE"
pass "node4 app disabled"

step "start kathara lab"
"${ROOT_DIR}/scripts/kathara/phase6a_acyclic_branch_up.sh"
LAB_STARTED=1
pass "kathara lab started"

step "wait for device startup"
kathara exec -d "$LAB_DIR" --wait node5 true
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
[[ -f "$NODE5_LOG" ]] || fail "missing node5 log: $NODE5_LOG"
[[ -f "$NODE4_STARTUP_LOG" ]] || fail "missing node4 startup log: $NODE4_STARTUP_LOG"

echo "--- NODE1 KEY LOGS ---"
grep -E 'App target|sent REQUEST|received DATA|Received data|listening on|tcp transport initialized' "$NODE1_LOG" || true

echo "--- NODE2 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent REQUEST|received DATA|sent DATA|Forwarding DATA|forward REQUEST|excluding upstream|connect|Connection refused|listening on|tcp transport initialized' "$NODE2_LOG" || true

echo "--- NODE3 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent REQUEST|received DATA|sent DATA|Forwarding DATA|forward REQUEST|excluding upstream|listening on|tcp transport initialized' "$NODE3_LOG" || true

echo "--- NODE4 STARTUP LOGS ---"
cat "$NODE4_STARTUP_LOG" || true

echo "--- NODE4 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent DATA|received DATA|satisfied locally|listening on|tcp transport initialized' "$NODE4_LOG" || true

echo "--- NODE5 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent DATA|received DATA|satisfied locally|listening on|tcp transport initialized' "$NODE5_LOG" || true
pass "logs captured"

step "assert isolation acceptance"
require_log "$NODE1_LOG" '[rsync-node-dev] startup configuration' 'node1 started rsync-node-dev'
require_log "$NODE2_LOG" '[rsync-node-dev] startup configuration' 'node2 started rsync-node-dev'
require_log "$NODE3_LOG" '[rsync-node-dev] startup configuration' 'node3 started rsync-node-dev'
require_log "$NODE5_LOG" '[rsync-node-dev] startup configuration' 'node5 started rsync-node-dev'
require_log "$NODE4_STARTUP_LOG" '[startup] app launch disabled for node4' 'node4 fault was injected'

require_log "$NODE1_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node1 sent REQUEST to node2 over TCP'
require_log "$NODE2_LOG" 'received REQUEST packet from Node 1' 'node2 received REQUEST from node1'
require_log "$NODE2_LOG" 'handleRequest(): no SyncTable hit, forward REQUEST to Node 3 while excluding upstream Node 1' 'node2 forwarded toward node3'
require_log "$NODE2_LOG" 'handleRequest(): no SyncTable hit, forward REQUEST to Node 4 while excluding upstream Node 1' 'node2 still exercised the bad branch'
require_log "$NODE3_LOG" 'received REQUEST packet from Node 2' 'node3 received REQUEST from node2'
require_log "$NODE3_LOG" 'handleRequest(): no SyncTable hit, forward REQUEST to Node 5 while excluding upstream Node 2' 'node3 forwarded toward node5'
require_log "$NODE5_LOG" 'received REQUEST packet from Node 3' 'node5 received REQUEST from node3'
require_log "$NODE5_LOG" 'handleRequest(): satisfied locally, stop forwarding request' 'node5 still satisfied the request locally'
require_log "$NODE5_LOG" 'sent DATA packet to Node 3 over TCP' 'node5 sent DATA to node3 over TCP'
require_log "$NODE3_LOG" 'received DATA packet from Node 5 to Node 3 over TCP' 'node3 received DATA from node5'
require_log "$NODE3_LOG" 'sent DATA packet to Node 2 over TCP' 'node3 relayed DATA to node2 over TCP'
require_log "$NODE2_LOG" 'received DATA packet from Node 3 to Node 2 over TCP' 'node2 received DATA from node3'
require_log "$NODE2_LOG" 'sent DATA packet to Node 1 over TCP' 'node2 relayed DATA to node1 over TCP'
require_log "$NODE1_LOG" 'received DATA packet from Node 2 to Node 1 over TCP' 'node1 received relayed DATA from node2'
require_log "$NODE1_LOG" "[App] Received data for key=${CACHE_ASSERT_KEY}, size=${#CACHE_ASSERT_VALUE}, content=\"${CACHE_ASSERT_VALUE}\"" 'application still received expected data'

require_log "$NODE1_LOG" "listening on ${NODE1_IP}:${NODE1_PORT}" 'node1 uses kathara loopback endpoint'
require_log "$NODE2_LOG" "listening on ${NODE2_IP}:${NODE2_PORT}" 'node2 uses kathara loopback endpoint'
require_log "$NODE3_LOG" "listening on ${NODE3_IP}:${NODE3_PORT}" 'node3 uses kathara loopback endpoint'
require_log "$NODE5_LOG" "listening on ${NODE5_IP}:${NODE5_PORT}" 'node5 uses kathara loopback endpoint'

reject_log "$NODE4_LOG" '[rsync-node-dev] startup configuration' 'node4 app did not start'
reject_log "$NODE4_LOG" 'listening on' 'node4 never listened on TCP'
reject_log "$NODE4_LOG" 'handleRequest(): satisfied locally' 'node4 did not fake local success'
reject_log "$NODE4_LOG" 'sent DATA packet' 'node4 did not fabricate DATA'
reject_log "$NODE4_LOG" 'received DATA packet' 'node4 did not receive DATA'
reject_log "$NODE4_LOG" '[App] Received data for key=' 'node4 did not report application success'
reject_log "$NODE1_LOG" 'received REQUEST packet from Node 2' 'node1 did not receive a bounced request from node2'
reject_log "$NODE2_LOG" 'sent REQUEST packet to Node 1 over TCP' 'node2 did not bounce the request back to node1'
reject_log "$NODE3_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node3 did not bounce the request back to node2'
reject_log "$NODE5_LOG" 'sent REQUEST packet to Node 3 over TCP' 'node5 did not forward the request beyond the target leaf'
reject_log "$NODE1_LOG" 'sent DATA packet to Node 2 over TCP' 'node1 did not bounce DATA back to node2'
reject_log "$NODE2_LOG" 'sent DATA packet to Node 3 over TCP' 'node2 did not bounce DATA back to node3'
reject_log "$NODE2_LOG" 'sent DATA packet to Node 4 over TCP' 'node2 did not misroute DATA to the bad branch'
reject_log "$NODE3_LOG" 'sent DATA packet to Node 5 over TCP' 'node3 did not bounce DATA back to node5'

step "result"
echo "PASS: phase6c Kathara acyclic branch isolation smoke succeeded"
