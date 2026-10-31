#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-rsync-router:phase5e-acyclic-branch}"
NETWORK_NAME="${NETWORK_NAME:-rsync-phase5e}"
SUBNET="${SUBNET:-172.31.22.0/24}"
NODE1_NAME="${NODE1_NAME:-rsync-phase5e-node1}"
NODE2_NAME="${NODE2_NAME:-rsync-phase5e-node2}"
NODE3_NAME="${NODE3_NAME:-rsync-phase5e-node3}"
NODE4_NAME="${NODE4_NAME:-rsync-phase5e-node4}"
NODE5_NAME="${NODE5_NAME:-rsync-phase5e-node5}"
NODE1_IP="${NODE1_IP:-172.31.22.11}"
NODE2_IP="${NODE2_IP:-172.31.22.12}"
NODE3_IP="${NODE3_IP:-172.31.22.13}"
NODE4_IP="${NODE4_IP:-172.31.22.14}"
NODE5_IP="${NODE5_IP:-172.31.22.15}"
NODE1_PORT="${NODE1_PORT:-42201}"
NODE2_PORT="${NODE2_PORT:-42202}"
NODE3_PORT="${NODE3_PORT:-42203}"
NODE4_PORT="${NODE4_PORT:-42204}"
NODE5_PORT="${NODE5_PORT:-42205}"
TOPOLOGY_FILE="${TOPOLOGY_FILE:-${ROOT_DIR}/config/acceptance/acyclic-5node-redis.yaml}"
TOPOLOGY_CONTAINER_PATH="/app/config/acyclic-5node-redis.yaml"
NODE_LOOP_ITERATIONS="${NODE_LOOP_ITERATIONS:-700}"
NODE_LOOP_SLEEP_MS="${NODE_LOOP_SLEEP_MS:-50}"
CACHE_ASSERT_KEY="${CACHE_ASSERT_KEY:-demo_key}"
CACHE_ASSERT_VALUE="${CACHE_ASSERT_VALUE:-DEMO_VALUE_5NODE_BRANCH}"
NODE1_REQUEST_KEYS="${NODE1_REQUEST_KEYS:-${CACHE_ASSERT_KEY}}"
NODE5_SEED_DATA="${NODE5_SEED_DATA:-${CACHE_ASSERT_KEY}=${CACHE_ASSERT_VALUE}}"
VERIFY_WAIT_SECONDS="${VERIFY_WAIT_SECONDS:-32}"
REDIS_SOCKET_PATH="${REDIS_SOCKET_PATH:-/run/redis/redis.sock}"

BUILD_ARGS=(--build-arg ENABLE_REDIS=ON --build-arg FETCH_REDIS_DEPS=ON)
if [[ "${BUILD_NO_CACHE:-1}" == "1" ]]; then
  BUILD_ARGS=(--no-cache "${BUILD_ARGS[@]}")
fi
SKIP_BUILD="${SKIP_BUILD:-0}"

NODE1_LOG="$(mktemp)"
NODE2_LOG="$(mktemp)"
NODE3_LOG="$(mktemp)"
NODE4_LOG="$(mktemp)"
NODE5_LOG="$(mktemp)"
BUILD_LOG="$(mktemp)"
RENDERED_TOPOLOGY_FILE="$(mktemp)"
NETWORK_CREATED=0

cleanup() {
  docker rm -f "$NODE1_NAME" "$NODE2_NAME" "$NODE3_NAME" "$NODE4_NAME" "$NODE5_NAME" >/dev/null 2>&1 || true
  if [[ "$NETWORK_CREATED" == "1" ]]; then
    docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
  fi
  rm -f "$NODE1_LOG" "$NODE2_LOG" "$NODE3_LOG" "$NODE4_LOG" "$NODE5_LOG" "$BUILD_LOG" "$RENDERED_TOPOLOGY_FILE"
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
    -v node5_ip="$NODE5_IP" \
    -v node1_port="$NODE1_PORT" \
    -v node2_port="$NODE2_PORT" \
    -v node3_port="$NODE3_PORT" \
    -v node4_port="$NODE4_PORT" \
    -v node5_port="$NODE5_PORT" '
    BEGIN {
      node_host[1] = node1_ip
      node_host[2] = node2_ip
      node_host[3] = node3_ip
      node_host[4] = node4_ip
      node_host[5] = node5_ip
      node_port[1] = node1_port
      node_port[2] = node2_port
      node_port[3] = node3_port
      node_port[4] = node4_port
      node_port[5] = node5_port
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
      for (node_id = 1; node_id <= 5; ++node_id) {
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

require_redis_value() {
  local container_name="$1"
  local key="$2"
  local expected_value="$3"
  local label="$4"
  local actual_value

  actual_value="$(docker exec "$container_name" redis-cli -s "$REDIS_SOCKET_PATH" GET "$key" | tr -d '\r')"
  if [[ "$actual_value" == "$expected_value" ]]; then
    pass "$label"
  else
    echo "FAIL: $label" >&2
    echo "  container=$container_name key=$key expected=$expected_value actual=$actual_value" >&2
    exit 1
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
echo "node5 endpoint=${NODE5_IP}:${NODE5_PORT}"
render_topology "$TOPOLOGY_FILE" "$RENDERED_TOPOLOGY_FILE" || fail "render topology"
cat "$RENDERED_TOPOLOGY_FILE"
pass "topology rendered"

if [[ "$SKIP_BUILD" == "1" ]]; then
  step "docker build"
  echo "SKIP_BUILD=1, reuse existing image: $IMAGE_TAG"
  docker image inspect "$IMAGE_TAG" >/dev/null 2>&1 || fail "missing prebuilt image: $IMAGE_TAG"
  pass "docker build skipped"
else
  step "docker build"
  echo "+ docker build ${BUILD_ARGS[*]} -t $IMAGE_TAG $ROOT_DIR"
  docker build "${BUILD_ARGS[@]}" -t "$IMAGE_TAG" "$ROOT_DIR" >"$BUILD_LOG" 2>&1 || {
    cat "$BUILD_LOG" >&2
    fail "docker build"
  }
  tail -n 20 "$BUILD_LOG"
  pass "docker build"
fi

step "bridge network"
docker rm -f "$NODE1_NAME" "$NODE2_NAME" "$NODE3_NAME" "$NODE4_NAME" "$NODE5_NAME" >/dev/null 2>&1 || true
docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
echo "+ docker network create --driver bridge --subnet $SUBNET $NETWORK_NAME"
docker network create --driver bridge --subnet "$SUBNET" "$NETWORK_NAME" >/dev/null
NETWORK_CREATED=1
pass "bridge network created"

step "start node5"
echo "+ docker run -d --name $NODE5_NAME --network $NETWORK_NAME --ip $NODE5_IP ..."
docker run -d --name "$NODE5_NAME" \
  --network "$NETWORK_NAME" --ip "$NODE5_IP" \
  -v "$RENDERED_TOPOLOGY_FILE:$TOPOLOGY_CONTAINER_PATH:ro" \
  -e RSYNC_APP_TARGET=rsync-node-dev \
  -e RSYNC_TRANSPORT=tcp \
  -e RSYNC_NODE_ID=5 \
  -e RSYNC_TOPOLOGY_CONFIG="$TOPOLOGY_CONTAINER_PATH" \
  -e RSYNC_LOOP_ITERATIONS="$NODE_LOOP_ITERATIONS" \
  -e RSYNC_LOOP_SLEEP_MS="$NODE_LOOP_SLEEP_MS" \
  -e RSYNC_SEED_DATA="$NODE5_SEED_DATA" \
  "$IMAGE_TAG" >/dev/null
sleep 2
pass "node5 started"

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
  "$IMAGE_TAG" >/dev/null
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
sleep "$VERIFY_WAIT_SECONDS"
pass "node1 started"

step "capture logs"
docker logs "$NODE1_NAME" >"$NODE1_LOG" 2>&1 || true
docker logs "$NODE2_NAME" >"$NODE2_LOG" 2>&1 || true
docker logs "$NODE3_NAME" >"$NODE3_LOG" 2>&1 || true
docker logs "$NODE4_NAME" >"$NODE4_LOG" 2>&1 || true
docker logs "$NODE5_NAME" >"$NODE5_LOG" 2>&1 || true

echo "--- NODE1 KEY LOGS ---"
grep -E 'App target|cache backend|sent REQUEST|received DATA|Received data|listening on|tcp transport initialized' "$NODE1_LOG" || true

echo "--- NODE2 KEY LOGS ---"
grep -E 'App target|cache backend|received REQUEST|sent REQUEST|received DATA|sent DATA|Forwarding DATA|forward REQUEST|excluding upstream|listening on|tcp transport initialized' "$NODE2_LOG" || true

echo "--- NODE3 KEY LOGS ---"
grep -E 'App target|cache backend|received REQUEST|sent REQUEST|received DATA|sent DATA|Forwarding DATA|forward REQUEST|excluding upstream|listening on|tcp transport initialized' "$NODE3_LOG" || true

echo "--- NODE4 KEY LOGS ---"
grep -E 'App target|cache backend|received REQUEST|sent REQUEST|sent DATA|received DATA|satisfied locally|no downstream neighbors|listening on|tcp transport initialized' "$NODE4_LOG" || true

echo "--- NODE5 KEY LOGS ---"
grep -E 'App target|cache backend|received REQUEST|sent REQUEST|sent DATA|received DATA|satisfied locally|listening on|tcp transport initialized' "$NODE5_LOG" || true
pass "logs captured"

step "verify states"
docker inspect "$NODE1_NAME" --format 'node1 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
docker inspect "$NODE2_NAME" --format 'node2 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
docker inspect "$NODE3_NAME" --format 'node3 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
docker inspect "$NODE4_NAME" --format 'node4 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
docker inspect "$NODE5_NAME" --format 'node5 network={{.HostConfig.NetworkMode}} ip={{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
pass "state inspection"

step "assert acceptance"
require_log "$NODE1_LOG" '应用目标 / App target: rsync-node-dev' 'node1 selected rsync-node-dev'
require_log "$NODE2_LOG" '应用目标 / App target: rsync-node-dev' 'node2 selected rsync-node-dev'
require_log "$NODE3_LOG" '应用目标 / App target: rsync-node-dev' 'node3 selected rsync-node-dev'
require_log "$NODE4_LOG" '应用目标 / App target: rsync-node-dev' 'node4 selected rsync-node-dev'
require_log "$NODE5_LOG" '应用目标 / App target: rsync-node-dev' 'node5 selected rsync-node-dev'
require_log "$NODE1_LOG" '[rsync-node-dev] cache backend: redis (default)' 'node1 defaulted to Redis cache'
require_log "$NODE2_LOG" '[rsync-node-dev] cache backend: redis (default)' 'node2 defaulted to Redis cache'
require_log "$NODE3_LOG" '[rsync-node-dev] cache backend: redis (default)' 'node3 defaulted to Redis cache'
require_log "$NODE4_LOG" '[rsync-node-dev] cache backend: redis (default)' 'node4 defaulted to Redis cache'
require_log "$NODE5_LOG" '[rsync-node-dev] cache backend: redis (default)' 'node5 defaulted to Redis cache'
require_log "$NODE1_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node1 sent REQUEST to node2 over TCP'
require_log "$NODE2_LOG" 'received REQUEST packet from Node 1' 'node2 received REQUEST from node1'
require_log "$NODE2_LOG" 'handleRequest(): no SyncTable hit, forward REQUEST to Node 3 while excluding upstream Node 1' 'node2 forwarded toward node3 without bouncing upstream'
require_log "$NODE2_LOG" 'sent REQUEST packet to Node 3 over TCP' 'node2 sent REQUEST to node3 over TCP'
require_log "$NODE2_LOG" 'handleRequest(): no SyncTable hit, forward REQUEST to Node 4 while excluding upstream Node 1' 'node2 also exercised the non-target branch toward node4'
require_log "$NODE2_LOG" 'sent REQUEST packet to Node 4 over TCP' 'node2 sent REQUEST to node4 over TCP'
require_log "$NODE3_LOG" 'received REQUEST packet from Node 2' 'node3 received REQUEST from node2'
require_log "$NODE3_LOG" 'handleRequest(): no SyncTable hit, forward REQUEST to Node 5 while excluding upstream Node 2' 'node3 forwarded toward target leaf node5'
require_log "$NODE3_LOG" 'sent REQUEST packet to Node 5 over TCP' 'node3 sent REQUEST to node5 over TCP'
require_log "$NODE4_LOG" 'received REQUEST packet from Node 2' 'node4 received the non-target branch request from node2'
require_log "$NODE4_LOG" 'handleRequest(): no SyncTable hit and no downstream neighbors after excluding upstream Node 2' 'node4 terminated the branch without loops'
require_log "$NODE5_LOG" 'received REQUEST packet from Node 3' 'node5 received REQUEST from node3'
require_log "$NODE5_LOG" 'handleRequest(): satisfied locally, stop forwarding request' 'node5 satisfied the request locally'
require_log "$NODE5_LOG" 'sent DATA packet to Node 3 over TCP' 'node5 sent DATA to node3 over TCP'
require_log "$NODE3_LOG" 'received DATA packet from Node 5 to Node 3 over TCP' 'node3 received DATA from node5'
require_log "$NODE3_LOG" 'sent DATA packet to Node 2 over TCP' 'node3 relayed DATA to node2 over TCP'
require_log "$NODE2_LOG" 'received DATA packet from Node 3 to Node 2 over TCP' 'node2 received DATA from node3'
require_log "$NODE2_LOG" 'sent DATA packet to Node 1 over TCP' 'node2 relayed DATA to node1 over TCP'
require_log "$NODE1_LOG" 'received DATA packet from Node 2 to Node 1 over TCP' 'node1 received relayed DATA from node2'
require_log "$NODE1_LOG" "[App] Received data for key=${CACHE_ASSERT_KEY}, size=${#CACHE_ASSERT_VALUE}, content=\"${CACHE_ASSERT_VALUE}\"" 'application received expected data'
require_log "$NODE1_LOG" "listening on ${NODE1_IP}:${NODE1_PORT}" 'node1 uses bridge endpoint'
require_log "$NODE2_LOG" "listening on ${NODE2_IP}:${NODE2_PORT}" 'node2 uses bridge endpoint'
require_log "$NODE3_LOG" "listening on ${NODE3_IP}:${NODE3_PORT}" 'node3 uses bridge endpoint'
require_log "$NODE4_LOG" "listening on ${NODE4_IP}:${NODE4_PORT}" 'node4 uses bridge endpoint'
require_log "$NODE5_LOG" "listening on ${NODE5_IP}:${NODE5_PORT}" 'node5 uses bridge endpoint'
reject_log "$NODE1_LOG" 'received REQUEST packet from Node 2' 'node1 did not receive a bounced request from node2'
reject_log "$NODE2_LOG" 'sent REQUEST packet to Node 1 over TCP' 'node2 did not bounce the request back to node1'
reject_log "$NODE3_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node3 did not bounce the request back to node2'
reject_log "$NODE4_LOG" 'sent REQUEST packet to Node 2 over TCP' 'node4 did not bounce the request back to node2'
reject_log "$NODE5_LOG" 'sent REQUEST packet to Node 3 over TCP' 'node5 did not forward the request beyond the target leaf'
reject_log "$NODE1_LOG" 'sent DATA packet to Node 2 over TCP' 'node1 did not bounce DATA back to node2'
reject_log "$NODE2_LOG" 'sent DATA packet to Node 3 over TCP' 'node2 did not bounce DATA back to node3'
reject_log "$NODE2_LOG" 'sent DATA packet to Node 4 over TCP' 'node2 did not misroute DATA onto the dead-end branch'
reject_log "$NODE3_LOG" 'sent DATA packet to Node 5 over TCP' 'node3 did not bounce DATA back to node5'
reject_log "$NODE4_LOG" 'sent DATA packet to Node' 'node4 did not fabricate DATA'
reject_log "$NODE4_LOG" 'received DATA packet from Node' 'node4 did not receive DATA on the non-target branch'
reject_log "$NODE4_LOG" 'handleRequest(): satisfied locally' 'node4 did not fake a local success'
reject_log "$NODE4_LOG" '[App] Received data for key=' 'node4 did not report application success'
reject_log "$NODE1_LOG" 'cache backend: in-memory' 'node1 did not fall back to in-memory cache'
reject_log "$NODE2_LOG" 'cache backend: in-memory' 'node2 did not fall back to in-memory cache'
reject_log "$NODE3_LOG" 'cache backend: in-memory' 'node3 did not fall back to in-memory cache'
reject_log "$NODE4_LOG" 'cache backend: in-memory' 'node4 did not fall back to in-memory cache'
reject_log "$NODE5_LOG" 'cache backend: in-memory' 'node5 did not fall back to in-memory cache'

step "verify Redis state"
docker exec "$NODE1_NAME" redis-cli -s "$REDIS_SOCKET_PATH" ping
docker exec "$NODE5_NAME" redis-cli -s "$REDIS_SOCKET_PATH" ping
require_redis_value "$NODE1_NAME" "$CACHE_ASSERT_KEY" "$CACHE_ASSERT_VALUE" 'node1 Redis contains the relayed data'
require_redis_value "$NODE5_NAME" "$CACHE_ASSERT_KEY" "$CACHE_ASSERT_VALUE" 'node5 Redis contains the seeded data'

step "result"
echo "PASS: phase5e acyclic branch smoke succeeded"
