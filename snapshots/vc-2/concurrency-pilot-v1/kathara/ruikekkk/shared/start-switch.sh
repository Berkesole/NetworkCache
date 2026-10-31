#!/bin/sh
set -eu

node_name="$1"
node_id="$2"
network_script="$3"

startup_log="/shared/logs/${node_name}.startup.log"

: >"${startup_log}"

log_startup() {
  printf '%s\n' "$1" >>"${startup_log}"
}

if [ ! -f "${network_script}" ]; then
  log_startup "[startup] missing network script: ${network_script}"
  exit 1
fi

# shellcheck disable=SC1090
. "${network_script}"

if ! command -v configure_network_node >/dev/null 2>&1; then
  log_startup "[startup] network script missing configure_network_node()"
  exit 1
fi

configure_network_node
sysctl -w net.ipv4.ip_forward=1 >/dev/null

log_startup "[startup] configured forwarding switch node_id=${node_id} loopback=${NODE_LOOPBACK_IP:-unknown} link_delay_ms=${NODE_LINK_DELAY_MS:-0}"
