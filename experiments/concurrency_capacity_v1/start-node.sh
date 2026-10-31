#!/bin/sh
set -eu

node_name="$1"
node_id="$2"
network_script="$3"

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
  if [ ! -f "${network_script}" ]; then
    log_startup "[startup] missing network script: ${network_script}"
    exit 1
  fi

  # shellcheck disable=SC1090
  . "${network_script}"

  if [ -z "${NODE_LOOPBACK_IP:-}" ]; then
    log_startup "[startup] network script did not export NODE_LOOPBACK_IP"
    exit 1
  fi
  if [ -z "${NODE_LINK_DELAY_MS:-}" ]; then
    NODE_LINK_DELAY_MS=0
  fi

  if ! command -v configure_network_node >/dev/null 2>&1; then
    log_startup "[startup] network script missing configure_network_node()"
    exit 1
  fi

  configure_network_node
  log_startup "[startup] configured network loopback=${NODE_LOOPBACK_IP} link_delay_ms=${NODE_LINK_DELAY_MS}"
}

launch_redis() {
  socket_dir="$(dirname "${redis_socket_path}")"
  mkdir -p "${socket_dir}" /data/redis /data/logs /data/tmp /data/state

  if [ -f "${redis_conf_template}" ]; then
    sed "s|__REDIS_SOCKET_PATH__|${redis_socket_path}|g" \
      "${redis_conf_template}" >"${redis_conf}"
    if [ -n "${RSYNC_REDIS_MAXMEMORY:-}" ]; then
      printf '\nmaxmemory %s\n' "${RSYNC_REDIS_MAXMEMORY}" >>"${redis_conf}"
    fi
    if [ -n "${RSYNC_REDIS_MAXMEMORY_POLICY:-}" ]; then
      printf 'maxmemory-policy %s\n' "${RSYNC_REDIS_MAXMEMORY_POLICY}" >>"${redis_conf}"
    fi
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

  if [ "${RSYNC_REDIS_REQUIRE_EMPTY:-0}" = "1" ]; then
    db_size="$(redis-cli -s "${redis_socket_path}" DBSIZE 2>/dev/null || printf error)"
    if [ "${db_size}" != "0" ]; then
      log_startup "[startup] redis DB is not empty: dbsize=${db_size}"
      exit 1
    fi
  fi

  log_startup "[startup] started redis pid=${redis_pid} socket=${redis_socket_path}"
}

launch_app() {
  export RSYNC_APP_TARGET=rsync-node-dev
  export RSYNC_TRANSPORT=tcp
  export RSYNC_NODE_ID="${node_id}"
  export RSYNC_TOPOLOGY_CONFIG="${RSYNC_TOPOLOGY_CONFIG:-/shared/ruikekkk-topology.yaml}"
  export RSYNC_LOOP_SLEEP_MS="${RSYNC_LOOP_SLEEP_MS:-50}"
  export RSYNC_OBSERVABILITY="${RSYNC_OBSERVABILITY:-0}"
  export RSYNC_EVENT_TRACE="${RSYNC_EVENT_TRACE:-0}"
  export RSYNC_EVENT_TRACE_FLUSH_EVERY="${RSYNC_EVENT_TRACE_FLUSH_EVERY:-64}"
  if [ "${RSYNC_EVENT_TRACE}" != "0" ] && [ "${RSYNC_EVENT_TRACE}" != "false" ] && [ "${RSYNC_EVENT_TRACE}" != "FALSE" ]; then
    mkdir -p /shared/traces
    export RSYNC_EVENT_TRACE_PATH="${RSYNC_EVENT_TRACE_PATH:-/shared/traces/${node_name}.jsonl}"
    export RSYNC_EVENT_TRACE_RUN_ID="${RSYNC_EVENT_TRACE_RUN_ID:-${scenario_name}}"
  elif [ -n "${RSYNC_EVENT_TRACE_PATH:-}" ]; then
    mkdir -p "$(dirname "${RSYNC_EVENT_TRACE_PATH}")"
    export RSYNC_EVENT_TRACE_PATH
    export RSYNC_EVENT_TRACE_RUN_ID="${RSYNC_EVENT_TRACE_RUN_ID:-${scenario_name}}"
  else
    unset RSYNC_EVENT_TRACE_PATH || true
    unset RSYNC_EVENT_TRACE_RUN_ID || true
  fi

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

  # Preserve every human-readable log byte without a per-line subprocess/pipe.
  # Authoritative epoch/monotonic timestamps remain in the structured trace.
  log_startup "[startup] stdout_mode=direct-file trace_clocks=epoch-and-monotonic"
  nohup /app/build/rsync-node-dev >"${app_log}" 2>&1 &
  app_pid="$!"
  printf '%s\n' "${app_pid}" >"${pid_file}"
  sleep 1

  if ! kill -0 "${app_pid}" 2>/dev/null; then
    log_startup "[startup] rsync-node-dev exited early for ${node_name}"
    exit 1
  fi

  log_startup "[startup] started rsync-node-dev pid=${app_pid} node_id=${node_id} observability=${RSYNC_OBSERVABILITY} event_trace=${RSYNC_EVENT_TRACE} trace_path=${RSYNC_EVENT_TRACE_PATH:-}"
}

scenario_name="unknown"
if [ -f /shared/active-scenario.txt ]; then
  scenario_name="$(cat /shared/active-scenario.txt 2>/dev/null || printf 'unknown')"
fi

log_startup "[startup] configuring ${node_name} scenario=${scenario_name}"
configure_network
if [ "${RSYNC_SKIP_APP:-0}" = "1" ]; then
  log_startup "[startup] RSYNC_SKIP_APP=1; network configured without redis or rsync-node-dev"
  exit 0
fi
launch_redis
launch_app
