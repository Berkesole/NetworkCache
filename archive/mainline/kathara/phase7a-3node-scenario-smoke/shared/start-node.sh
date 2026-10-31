#!/bin/sh
set -eu

node_name="$1"
node_id="$2"
loopback_ip="$3"

startup_log="/shared/logs/${node_name}.startup.log"
app_log="/shared/logs/${node_name}.log"
redis_log="/shared/logs/${node_name}.redis.log"
pid_file="/shared/state/${node_name}.pid"
redis_pid_file="/shared/state/${node_name}.redis.pid"
redis_socket_path="${REDIS_SOCKET_PATH:-/run/redis/redis.sock}"
redis_conf_template="/etc/redis/redis.conf.template"
redis_conf="/tmp/${node_name}.redis.conf"

: >"${startup_log}"
: >"${app_log}"
: >"${redis_log}"

log_startup() {
  printf '%s\n' "$1" >>"${startup_log}"
}

configure_network() {
  ip link set dev lo up

  case "${node_name}" in
    node1)
      ip link set dev eth0 up
      ip address replace 172.22.12.1/30 dev eth0
      ip address replace "${loopback_ip}/32" dev lo
      ip route replace 10.60.0.2/32 via 172.22.12.2 dev eth0
      ip route replace 10.60.0.3/32 via 172.22.12.2 dev eth0
      ;;
    node2)
      ip link set dev eth0 up
      ip link set dev eth1 up
      ip address replace 172.22.12.2/30 dev eth0
      ip address replace 172.22.23.1/30 dev eth1
      ip address replace "${loopback_ip}/32" dev lo
      ip route replace 10.60.0.1/32 via 172.22.12.1 dev eth0
      ip route replace 10.60.0.3/32 via 172.22.23.2 dev eth1
      ;;
    node3)
      ip link set dev eth0 up
      ip address replace 172.22.23.2/30 dev eth0
      ip address replace "${loopback_ip}/32" dev lo
      ip route replace 10.60.0.1/32 via 172.22.23.1 dev eth0
      ip route replace 10.60.0.2/32 via 172.22.23.1 dev eth0
      ;;
    *)
      log_startup "[startup] unsupported node: ${node_name}"
      exit 1
      ;;
  esac
}

launch_redis() {
  socket_dir="$(dirname "${redis_socket_path}")"
  mkdir -p "${socket_dir}" /data/redis /data/logs /data/tmp /data/state

  if [ -f "${redis_conf_template}" ]; then
    sed "s|__REDIS_SOCKET_PATH__|${redis_socket_path}|g" \
      "${redis_conf_template}" >"${redis_conf}"
  else
    log_startup "[startup] missing redis config template: ${redis_conf_template}"
    exit 1
  fi

  nohup /usr/bin/redis-server "${redis_conf}" >"${redis_log}" 2>&1 &
  redis_pid="$!"
  printf '%s\n' "${redis_pid}" >"${redis_pid_file}"

  redis_ready=0
  for _ in $(seq 1 50); do
    if [ -S "${redis_socket_path}" ]; then
      redis_ready=1
      break
    fi
    sleep 0.1
  done

  if [ "${redis_ready}" != "1" ]; then
    log_startup "[startup] redis socket did not appear: ${redis_socket_path}"
    exit 1
  fi

  log_startup "[startup] started redis pid=${redis_pid} socket=${redis_socket_path}"
}

launch_app() {
  export RSYNC_APP_TARGET=rsync-node-dev
  export RSYNC_TRANSPORT=tcp
  export RSYNC_NODE_ID="${node_id}"
  export RSYNC_TOPOLOGY_CONFIG="${RSYNC_TOPOLOGY_CONFIG:-/shared/phase7a-3node-scenario-topology.yaml}"
  export RSYNC_LOOP_SLEEP_MS="${RSYNC_LOOP_SLEEP_MS:-50}"

  if [ -n "${RSYNC_LOOP_ITERATIONS:-}" ]; then
    export RSYNC_LOOP_ITERATIONS
  else
    unset RSYNC_LOOP_ITERATIONS || true
  fi

  if [ -n "${RSYNC_SEED_DATA:-}" ]; then
    export RSYNC_SEED_DATA
  else
    unset RSYNC_SEED_DATA || true
  fi

  if [ -n "${RSYNC_REQUEST_KEYS:-}" ]; then
    export RSYNC_REQUEST_KEYS
  else
    unset RSYNC_REQUEST_KEYS || true
  fi

  nohup /app/build/rsync-node-dev >"${app_log}" 2>&1 &
  app_pid="$!"
  printf '%s\n' "${app_pid}" >"${pid_file}"
  sleep 1

  if ! kill -0 "${app_pid}" 2>/dev/null; then
    log_startup "[startup] rsync-node-dev exited early for ${node_name}"
    exit 1
  fi

  log_startup "[startup] started rsync-node-dev pid=${app_pid} node_id=${node_id}"
}

log_startup "[startup] configuring ${node_name}"
configure_network
launch_redis
launch_app
