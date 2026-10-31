#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-rsync-router:phase3-bridge}"
NETWORK_NAME="${NETWORK_NAME:-rsync-phase3}"
SUBNET="${SUBNET:-172.30.10.0/24}"
NODE1_NAME="${NODE1_NAME:-rsync-phase3-node1}"
NODE2_NAME="${NODE2_NAME:-rsync-phase3-node2}"
NODE1_IP="${NODE1_IP:-172.30.10.11}"
NODE2_IP="${NODE2_IP:-172.30.10.12}"
TOPOLOGY_FILE="${TOPOLOGY_FILE:-${ROOT_DIR}/config/acceptance/bridge-2node.yaml}"
TOPOLOGY_CONTAINER_PATH="/app/config/bridge-2node.yaml"
NODE_LOOP_ITERATIONS="${NODE_LOOP_ITERATIONS:-400}"
NODE_LOOP_SLEEP_MS="${NODE_LOOP_SLEEP_MS:-50}"
NODE1_REQUEST_KEYS="${NODE1_REQUEST_KEYS:-demo_key}"
NODE2_SEED_DATA="${NODE2_SEED_DATA:-demo_key=DEMO_VALUE}"
VERIFY_WAIT_SECONDS="${VERIFY_WAIT_SECONDS:-22}"
NO_CACHE_FLAG=""
if [[ "${BUILD_NO_CACHE:-1}" == "1" ]]; then
  NO_CACHE_FLAG="--no-cache"
fi

NODE1_LOG="$(mktemp)"
NODE2_LOG="$(mktemp)"
BUILD_LOG="$(mktemp)"
NETWORK_CREATED=0
NODE1_CREATED=0
NODE2_CREATED=0

cleanup() {
  docker rm -f "$NODE1_NAME" "$NODE2_NAME" >/dev/null 2>&1 || true
  if [[ "$NETWORK_CREATED" == "1" ]]; then
    docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
  fi
  rm -f "$NODE1_LOG" "$NODE2_LOG" "$BUILD_LOG"
}
trap cleanup EXIT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "FAIL: missing required command: $1" >&2
    exit 1
  }
}

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

require_cmd docker
[[ -f "$TOPOLOGY_FILE" ]] || fail "missing topology file: $TOPOLOGY_FILE"

step "git baseline"
cd "$ROOT_DIR"
echo "branch=$(git branch --show-current)"
echo "commit=$(git rev-parse --short HEAD)"
pass "git metadata collected"

step "docker build"
echo "+ docker build $NO_CACHE_FLAG -t $IMAGE_TAG $ROOT_DIR"
docker build $NO_CACHE_FLAG -t "$IMAGE_TAG" "$ROOT_DIR" >"$BUILD_LOG" 2>&1 || {
  cat "$BUILD_LOG" >&2
  fail "docker build"
}
tail -n 20 "$BUILD_LOG"
pass "docker build"

step "bridge network"
docker rm -f "$NODE1_NAME" "$NODE2_NAME" >/dev/null 2>&1 || true
docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
echo "+ docker network create --driver bridge --subnet $SUBNET $NETWORK_NAME"
docker network create --driver bridge --subnet "$SUBNET" "$NETWORK_NAME" >/dev/null
NETWORK_CREATED=1
pass "bridge network created"

step "start node2"
echo "+ docker run -d --name $NODE2_NAME --network $NETWORK_NAME --ip $NODE2_IP ..."
docker run -d --name "$NODE2_NAME" \
  --network "$NETWORK_NAME" --ip "$NODE2_IP" \
  -v "$TOPOLOGY_FILE:$TOPOLOGY_CONTAINER_PATH:ro" \
  -e RSYNC_APP_TARGET=rsync-node-dev \
  -e RSYNC_TRANSPORT=tcp \
  -e RSYNC_NODE_ID=2 \
  -e RSYNC_TOPOLOGY_CONFIG="$TOPOLOGY_CONTAINER_PATH" \
  -e RSYNC_LOOP_ITERATIONS="$NODE_LOOP_ITERATIONS" \
  -e RSYNC_LOOP_SLEEP_MS="$NODE_LOOP_SLEEP_MS" \
  -e RSYNC_SEED_DATA="$NODE2_SEED_DATA" \
  "$IMAGE_TAG" >/dev/null
NODE2_CREATED=1
sleep 2
pass "node2 started"

step "start node1"
echo "+ docker run -d --name $NODE1_NAME --network $NETWORK_NAME --ip $NODE1_IP ..."
docker run -d --name "$NODE1_NAME" \
  --network "$NETWORK_NAME" --ip "$NODE1_IP" \
  -v "$TOPOLOGY_FILE:$TOPOLOGY_CONTAINER_PATH:ro" \
  -e RSYNC_APP_TARGET=rsync-node-dev \
  -e RSYNC_TRANSPORT=tcp \
  -e RSYNC_NODE_ID=1 \
  -e RSYNC_TOPOLOGY_CONFIG="$TOPOLOGY_CONTAINER_PATH" \
  -e RSYNC_LOOP_ITERATIONS="$NODE_LOOP_ITERATIONS" \
  -e RSYNC_LOOP_SLEEP_MS="$NODE_LOOP_SLEEP_MS" \
  -e RSYNC_REQUEST_KEYS="$NODE1_REQUEST_KEYS" \
  "$IMAGE_TAG" >/dev/null
NODE1_CREATED=1
sleep "$VERIFY_WAIT_SECONDS"
pass "node1 started"

step "capture logs"
docker logs "$NODE1_NAME" >"$NODE1_LOG" 2>&1 || true
docker logs "$NODE2_NAME" >"$NODE2_LOG" 2>&1 || true

echo "--- NODE1 KEY LOGS ---"
grep -E 'App target|sent REQUEST|received DATA|Received data|listening on|tcp transport initialized' "$NODE1_LOG" || true

echo "--- NODE2 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent DATA|satisfied locally|listening on|tcp transport initialized' "$NODE2_LOG" || true
pass "logs captured"

step "verify states"
docker inspect "$NODE1_NAME" --format 'node1 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
docker inspect "$NODE2_NAME" --format 'node2 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
pass "state inspection"

step "assert acceptance"
require_log "$NODE1_LOG" '应用目标 / App target: rsync-node-dev' 'node1 selected rsync-node-dev'
require_log "$NODE2_LOG" '应用目标 / App target: rsync-node-dev' 'node2 selected rsync-node-dev'
require_log "$NODE1_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node1 sent REQUEST over TCP'
require_log "$NODE2_LOG" 'received REQUEST packet from Node 1' 'node2 received REQUEST over TCP'
require_log "$NODE2_LOG" 'sent DATA packet to Node 1 over TCP' 'node2 sent DATA over TCP'
require_log "$NODE1_LOG" 'received DATA packet from Node 2 to Node 1 over TCP' 'node1 received DATA over TCP'
require_log "$NODE1_LOG" '[App] Received data for key=demo_key, size=10, content="DEMO_VALUE"' 'application received DEMO_VALUE'
require_log "$NODE1_LOG" "listening on ${NODE1_IP}:41001" 'node1 uses bridge endpoint'
require_log "$NODE2_LOG" "listening on ${NODE2_IP}:41002" 'node2 uses bridge endpoint'

step "result"
echo "PASS: phase3 bridge verification succeeded"
