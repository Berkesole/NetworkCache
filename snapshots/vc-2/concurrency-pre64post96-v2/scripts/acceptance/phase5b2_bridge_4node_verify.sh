#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-rsync-router:phase5b2-bridge}"
NETWORK_NAME="${NETWORK_NAME:-rsync-phase5b2}"
SUBNET="${SUBNET:-172.30.20.0/24}"
NODE1_NAME="${NODE1_NAME:-rsync-phase5b2-node1}"
NODE2_NAME="${NODE2_NAME:-rsync-phase5b2-node2}"
NODE3_NAME="${NODE3_NAME:-rsync-phase5b2-node3}"
NODE4_NAME="${NODE4_NAME:-rsync-phase5b2-node4}"
NODE1_IP="${NODE1_IP:-172.30.20.11}"
NODE2_IP="${NODE2_IP:-172.30.20.12}"
NODE3_IP="${NODE3_IP:-172.30.20.13}"
NODE4_IP="${NODE4_IP:-172.30.20.14}"
NODE1_PORT="${NODE1_PORT:-42001}"
NODE2_PORT="${NODE2_PORT:-42002}"
NODE3_PORT="${NODE3_PORT:-42003}"
NODE4_PORT="${NODE4_PORT:-42004}"
TOPOLOGY_FILE="${TOPOLOGY_FILE:-${ROOT_DIR}/config/acceptance/bridge-4node.yaml}"
TOPOLOGY_CONTAINER_PATH="/app/config/bridge-4node.yaml"
NODE_LOOP_ITERATIONS="${NODE_LOOP_ITERATIONS:-600}"
NODE_LOOP_SLEEP_MS="${NODE_LOOP_SLEEP_MS:-50}"
NODE1_REQUEST_KEYS="${NODE1_REQUEST_KEYS:-demo_key}"
NODE4_SEED_DATA="${NODE4_SEED_DATA:-demo_key=DEMO_VALUE_4NODE}"
VERIFY_WAIT_SECONDS="${VERIFY_WAIT_SECONDS:-26}"
NO_CACHE_FLAG=""
if [[ "${BUILD_NO_CACHE:-1}" == "1" ]]; then
  NO_CACHE_FLAG="--no-cache"
fi

NODE1_LOG="$(mktemp)"
NODE2_LOG="$(mktemp)"
NODE3_LOG="$(mktemp)"
NODE4_LOG="$(mktemp)"
BUILD_LOG="$(mktemp)"
RENDERED_TOPOLOGY_FILE="$(mktemp)"
NETWORK_CREATED=0
NODE1_CREATED=0
NODE2_CREATED=0
NODE3_CREATED=0
NODE4_CREATED=0

cleanup() {
  docker rm -f "$NODE1_NAME" "$NODE2_NAME" "$NODE3_NAME" "$NODE4_NAME" >/dev/null 2>&1 || true
  if [[ "$NETWORK_CREATED" == "1" ]]; then
    docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
  fi
  rm -f "$NODE1_LOG" "$NODE2_LOG" "$NODE3_LOG" "$NODE4_LOG" "$BUILD_LOG" "$RENDERED_TOPOLOGY_FILE"
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

render_topology() {
  local source_file="$1"
  local output_file="$2"

  awk \
    -v node1_ip="$NODE1_IP" \
    -v node2_ip="$NODE2_IP" \
    -v node3_ip="$NODE3_IP" \
    -v node4_ip="$NODE4_IP" \
    -v node1_port="$NODE1_PORT" \
    -v node2_port="$NODE2_PORT" \
    -v node3_port="$NODE3_PORT" \
    -v node4_port="$NODE4_PORT" '
    BEGIN {
      node_host[1] = node1_ip
      node_host[2] = node2_ip
      node_host[3] = node3_ip
      node_host[4] = node4_ip
      node_port[1] = node1_port
      node_port[2] = node2_port
      node_port[3] = node3_port
      node_port[4] = node4_port
      current_id = ""
    }
    /^[[:space:]]*-[[:space:]]*id:[[:space:]]*[0-9]+[[:space:]]*$/ {
      current_id = $0
      sub(/^[[:space:]]*-[[:space:]]*id:[[:space:]]*/, "", current_id)
      sub(/[[:space:]]*$/, "", current_id)
      current_id += 0
      print
      next
    }
    /^[[:space:]]*id:[[:space:]]*[0-9]+[[:space:]]*$/ {
      current_id = $0
      sub(/^[[:space:]]*id:[[:space:]]*/, "", current_id)
      sub(/[[:space:]]*$/, "", current_id)
      current_id += 0
      print
      next
    }
    /^[[:space:]]*host:[[:space:]]*.*$/ {
      if (!(current_id in node_host)) {
        printf "render_topology: host encountered before a supported node id in %s\n", FILENAME > "/dev/stderr"
        exit 1
      }
      host_seen[current_id] = 1
      indent = $0
      sub(/host:.*/, "", indent)
      printf "%shost: %s\n", indent, node_host[current_id]
      next
    }
    /^[[:space:]]*port:[[:space:]]*.*$/ {
      if (!(current_id in node_port)) {
        printf "render_topology: port encountered before a supported node id in %s\n", FILENAME > "/dev/stderr"
        exit 1
      }
      port_seen[current_id] = 1
      indent = $0
      sub(/port:.*/, "", indent)
      printf "%sport: %s\n", indent, node_port[current_id]
      next
    }
    { print }
    END {
      for (node_id = 1; node_id <= 4; ++node_id) {
        if (!host_seen[node_id]) {
          printf "render_topology: missing host for node %d in %s\n", node_id, FILENAME > "/dev/stderr"
          exit 1
        }
        if (!port_seen[node_id]) {
          printf "render_topology: missing port for node %d in %s\n", node_id, FILENAME > "/dev/stderr"
          exit 1
        }
      }
    }
  ' "$source_file" >"$output_file"
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

require_cmd docker
require_cmd awk
[[ -f "$TOPOLOGY_FILE" ]] || fail "missing topology file: $TOPOLOGY_FILE"

step "git baseline"
cd "$ROOT_DIR"
echo "branch=$(git branch --show-current)"
echo "commit=$(git rev-parse --short HEAD)"
pass "git metadata collected"

step "render topology"
echo "source topology=$TOPOLOGY_FILE"
echo "rendered topology=$RENDERED_TOPOLOGY_FILE"
echo "node1 endpoint=${NODE1_IP}:${NODE1_PORT}"
echo "node2 endpoint=${NODE2_IP}:${NODE2_PORT}"
echo "node3 endpoint=${NODE3_IP}:${NODE3_PORT}"
echo "node4 endpoint=${NODE4_IP}:${NODE4_PORT}"
render_topology "$TOPOLOGY_FILE" "$RENDERED_TOPOLOGY_FILE" || fail "render topology"
cat "$RENDERED_TOPOLOGY_FILE"
pass "topology rendered"

step "docker build"
echo "+ docker build $NO_CACHE_FLAG -t $IMAGE_TAG $ROOT_DIR"
docker build $NO_CACHE_FLAG -t "$IMAGE_TAG" "$ROOT_DIR" >"$BUILD_LOG" 2>&1 || {
  cat "$BUILD_LOG" >&2
  fail "docker build"
}
tail -n 20 "$BUILD_LOG"
pass "docker build"

step "bridge network"
docker rm -f "$NODE1_NAME" "$NODE2_NAME" "$NODE3_NAME" "$NODE4_NAME" >/dev/null 2>&1 || true
docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
echo "+ docker network create --driver bridge --subnet $SUBNET $NETWORK_NAME"
docker network create --driver bridge --subnet "$SUBNET" "$NETWORK_NAME" >/dev/null
NETWORK_CREATED=1
pass "bridge network created"

step "start node4"
echo "+ docker run -d --name $NODE4_NAME --network $NETWORK_NAME --ip $NODE4_IP ..."
docker run -d --name "$NODE4_NAME" \
  --network "$NETWORK_NAME" --ip "$NODE4_IP" \
  -v "$RENDERED_TOPOLOGY_FILE:$TOPOLOGY_CONTAINER_PATH:ro" \
  -e RSYNC_APP_TARGET=rsync-node-dev \
  -e RSYNC_TRANSPORT=tcp \
  -e RSYNC_NODE_ID=4 \
  -e RSYNC_TOPOLOGY_CONFIG="$TOPOLOGY_CONTAINER_PATH" \
  -e RSYNC_LOOP_ITERATIONS="$NODE_LOOP_ITERATIONS" \
  -e RSYNC_LOOP_SLEEP_MS="$NODE_LOOP_SLEEP_MS" \
  -e RSYNC_SEED_DATA="$NODE4_SEED_DATA" \
  "$IMAGE_TAG" >/dev/null
NODE4_CREATED=1
sleep 2
pass "node4 started"

step "start node3"
echo "+ docker run -d --name $NODE3_NAME --network $NETWORK_NAME --ip $NODE3_IP ..."
docker run -d --name "$NODE3_NAME" \
  --network "$NETWORK_NAME" --ip "$NODE3_IP" \
  -v "$RENDERED_TOPOLOGY_FILE:$TOPOLOGY_CONTAINER_PATH:ro" \
  -e RSYNC_APP_TARGET=rsync-node-dev \
  -e RSYNC_TRANSPORT=tcp \
  -e RSYNC_NODE_ID=3 \
  -e RSYNC_TOPOLOGY_CONFIG="$TOPOLOGY_CONTAINER_PATH" \
  -e RSYNC_LOOP_ITERATIONS="$NODE_LOOP_ITERATIONS" \
  -e RSYNC_LOOP_SLEEP_MS="$NODE_LOOP_SLEEP_MS" \
  "$IMAGE_TAG" >/dev/null
NODE3_CREATED=1
sleep 2
pass "node3 started"

step "start node2"
echo "+ docker run -d --name $NODE2_NAME --network $NETWORK_NAME --ip $NODE2_IP ..."
docker run -d --name "$NODE2_NAME" \
  --network "$NETWORK_NAME" --ip "$NODE2_IP" \
  -v "$RENDERED_TOPOLOGY_FILE:$TOPOLOGY_CONTAINER_PATH:ro" \
  -e RSYNC_APP_TARGET=rsync-node-dev \
  -e RSYNC_TRANSPORT=tcp \
  -e RSYNC_NODE_ID=2 \
  -e RSYNC_TOPOLOGY_CONFIG="$TOPOLOGY_CONTAINER_PATH" \
  -e RSYNC_LOOP_ITERATIONS="$NODE_LOOP_ITERATIONS" \
  -e RSYNC_LOOP_SLEEP_MS="$NODE_LOOP_SLEEP_MS" \
  "$IMAGE_TAG" >/dev/null
NODE2_CREATED=1
sleep 2
pass "node2 started"

step "start node1"
echo "+ docker run -d --name $NODE1_NAME --network $NETWORK_NAME --ip $NODE1_IP ..."
docker run -d --name "$NODE1_NAME" \
  --network "$NETWORK_NAME" --ip "$NODE1_IP" \
  -v "$RENDERED_TOPOLOGY_FILE:$TOPOLOGY_CONTAINER_PATH:ro" \
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
docker logs "$NODE3_NAME" >"$NODE3_LOG" 2>&1 || true
docker logs "$NODE4_NAME" >"$NODE4_LOG" 2>&1 || true

echo "--- NODE1 KEY LOGS ---"
grep -E 'App target|sent REQUEST|received DATA|Received data|listening on|tcp transport initialized' "$NODE1_LOG" || true

echo "--- NODE2 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent REQUEST|received DATA|sent DATA|Forwarding DATA|forward REQUEST|listening on|tcp transport initialized' "$NODE2_LOG" || true

echo "--- NODE3 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent REQUEST|received DATA|sent DATA|Forwarding DATA|forward REQUEST|listening on|tcp transport initialized' "$NODE3_LOG" || true

echo "--- NODE4 KEY LOGS ---"
grep -E 'App target|received REQUEST|sent DATA|satisfied locally|listening on|tcp transport initialized' "$NODE4_LOG" || true
pass "logs captured"

step "verify states"
docker inspect "$NODE1_NAME" --format 'node1 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
docker inspect "$NODE2_NAME" --format 'node2 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
docker inspect "$NODE3_NAME" --format 'node3 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
docker inspect "$NODE4_NAME" --format 'node4 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
pass "state inspection"

step "assert acceptance"
require_log "$NODE1_LOG" '应用目标 / App target: rsync-node-dev' 'node1 selected rsync-node-dev'
require_log "$NODE2_LOG" '应用目标 / App target: rsync-node-dev' 'node2 selected rsync-node-dev'
require_log "$NODE3_LOG" '应用目标 / App target: rsync-node-dev' 'node3 selected rsync-node-dev'
require_log "$NODE4_LOG" '应用目标 / App target: rsync-node-dev' 'node4 selected rsync-node-dev'
require_log "$NODE1_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node1 sent REQUEST to node2 over TCP'
require_log "$NODE2_LOG" 'received REQUEST packet from Node 1' 'node2 received REQUEST from node1'
require_log "$NODE2_LOG" 'handleRequest(): no SyncTable hit, forward REQUEST to Node 3 while excluding upstream Node 1' 'node2 forwarded downstream without bouncing upstream'
require_log "$NODE2_LOG" 'sent REQUEST packet to Node 3 over TCP' 'node2 sent REQUEST to node3 over TCP'
require_log "$NODE3_LOG" 'received REQUEST packet from Node 2' 'node3 received REQUEST from node2'
require_log "$NODE3_LOG" 'handleRequest(): no SyncTable hit, forward REQUEST to Node 4 while excluding upstream Node 2' 'node3 forwarded downstream without bouncing upstream'
require_log "$NODE3_LOG" 'sent REQUEST packet to Node 4 over TCP' 'node3 sent REQUEST to node4 over TCP'
require_log "$NODE4_LOG" 'received REQUEST packet from Node 3' 'node4 received REQUEST from node3'
require_log "$NODE4_LOG" 'sent DATA packet to Node 3 over TCP' 'node4 sent DATA to node3 over TCP'
require_log "$NODE3_LOG" 'received DATA packet from Node 4 to Node 3 over TCP' 'node3 received DATA from node4'
require_log "$NODE3_LOG" 'sent DATA packet to Node 2 over TCP' 'node3 relayed DATA to node2 over TCP'
require_log "$NODE2_LOG" 'received DATA packet from Node 3 to Node 2 over TCP' 'node2 received DATA from node3'
require_log "$NODE2_LOG" 'sent DATA packet to Node 1 over TCP' 'node2 relayed DATA to node1 over TCP'
require_log "$NODE1_LOG" 'received DATA packet from Node 2 to Node 1 over TCP' 'node1 received relayed DATA from node2'
require_log "$NODE1_LOG" '[App] Received data for key=demo_key, size=16, content="DEMO_VALUE_4NODE"' 'application received DEMO_VALUE_4NODE'
require_log "$NODE1_LOG" "listening on ${NODE1_IP}:${NODE1_PORT}" 'node1 uses bridge endpoint'
require_log "$NODE2_LOG" "listening on ${NODE2_IP}:${NODE2_PORT}" 'node2 uses bridge endpoint'
require_log "$NODE3_LOG" "listening on ${NODE3_IP}:${NODE3_PORT}" 'node3 uses bridge endpoint'
require_log "$NODE4_LOG" "listening on ${NODE4_IP}:${NODE4_PORT}" 'node4 uses bridge endpoint'
reject_log "$NODE1_LOG" 'received REQUEST packet from Node 2' 'node1 did not receive a bounced request from node2'
reject_log "$NODE2_LOG" 'sent REQUEST packet to Node 1 over TCP' 'node2 did not bounce the request back to node1'
reject_log "$NODE3_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node3 did not bounce the request back to node2'
reject_log "$NODE1_LOG" 'sent DATA packet to Node 2 over TCP' 'node1 did not bounce DATA back to node2'
reject_log "$NODE2_LOG" 'sent DATA packet to Node 3 over TCP' 'node2 did not bounce DATA back to node3'

step "result"
echo "PASS: phase5b2 4-node bridge verification succeeded"
