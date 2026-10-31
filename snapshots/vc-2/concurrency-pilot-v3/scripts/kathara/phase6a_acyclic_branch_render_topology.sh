#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE_TOPOLOGY="${SOURCE_TOPOLOGY:-${ROOT_DIR}/config/acceptance/acyclic-5node-redis.yaml}"
LAB_DIR="${LAB_DIR:-${ROOT_DIR}/kathara/phase6a-acyclic-branch-smoke}"
OUTPUT_TOPOLOGY="${OUTPUT_TOPOLOGY:-${LAB_DIR}/shared/phase6a-acyclic-branch-topology.yaml}"
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

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
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

require_cmd awk
[[ -f "$SOURCE_TOPOLOGY" ]] || fail "missing topology file: $SOURCE_TOPOLOGY"
mkdir -p "${LAB_DIR}/shared"
render_topology "$SOURCE_TOPOLOGY" "$OUTPUT_TOPOLOGY"
echo "rendered_topology=${OUTPUT_TOPOLOGY}"
cat "$OUTPUT_TOPOLOGY"
