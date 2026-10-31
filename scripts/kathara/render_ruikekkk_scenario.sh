#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAB_DIR="${LAB_DIR:-${ROOT_DIR}/kathara/ruikekkk}"
SCENARIOS_DIR="${SCENARIOS_DIR:-${LAB_DIR}/scenarios}"
SHARED_DIR="${SHARED_DIR:-${LAB_DIR}/shared}"
GENERATED_DIR="${GENERATED_DIR:-${SHARED_DIR}/generated}"
OUTPUT_TOPOLOGY="${OUTPUT_TOPOLOGY:-${SHARED_DIR}/ruikekkk-topology.yaml}"
ACTIVE_SCENARIO_FILE="${ACTIVE_SCENARIO_FILE:-${SHARED_DIR}/active-scenario.txt}"
IMAGE_TAG="${IMAGE_TAG:-rsync-router:ruikekkk-kathara}"
LOOPBACK_PREFIX="${LOOPBACK_PREFIX:-10.90.0}"
PORT_BASE="${PORT_BASE:-44000}"
LINK_OCTET2="${LINK_OCTET2:-31}"
LINK_OCTET3_BASE="${LINK_OCTET3_BASE:-16}"
LINKS_PER_OCTET3="${LINKS_PER_OCTET3:-64}"
LAB_VERSION="${LAB_VERSION:-1.0}"
NODE_LOOP_SLEEP_MS="${KATHARA_NODE_LOOP_SLEEP_MS:-50}"
NODE_LOOP_ITERATIONS_VALUE="${RSYNC_LOOP_ITERATIONS:-}"
LINK_DELAY_MS="${KATHARA_LINK_DELAY_MS:-0}"
LINK_RATE_MBIT="${KATHARA_LINK_RATE_MBIT:-0}"
LINK_QUEUE_PACKETS="${KATHARA_LINK_QUEUE_PACKETS:-1000}"
LINK_MTU="${KATHARA_LINK_MTU:-1500}"
RSYNC_OBSERVABILITY_VALUE="${RSYNC_OBSERVABILITY:-0}"
RSYNC_SKIP_APP_VALUE="${RSYNC_SKIP_APP:-0}"
RSYNC_ORACLE_UNICAST_VALUE="${RSYNC_ORACLE_UNICAST:-0}"
RSYNC_ORACLE_UNICAST_SCHEDULE_DIR_VALUE="${RSYNC_ORACLE_UNICAST_SCHEDULE_DIR:-}"
RSYNC_EVENT_TRACE_VALUE="${RSYNC_EVENT_TRACE:-0}"
RSYNC_EVENT_TRACE_RUN_ID_VALUE="${RSYNC_EVENT_TRACE_RUN_ID:-}"
RSYNC_EVENT_TRACE_FLUSH_EVERY_VALUE="${RSYNC_EVENT_TRACE_FLUSH_EVERY:-64}"
RSYNC_WIRE_VERSION_VALUE="${RSYNC_WIRE_VERSION:-1}"
RSYNC_BLOOM_BITS_VALUE="${RSYNC_BLOOM_BITS:-1024}"
RSYNC_BLOOM_HASH_COUNT_VALUE="${RSYNC_BLOOM_HASH_COUNT:-1}"
RSYNC_STAGED_FALLBACK_VALUE="${RSYNC_STAGED_FALLBACK:-0}"
RSYNC_FALLBACK_DELAY_MS_VALUE="${RSYNC_FALLBACK_DELAY_MS:-1500}"
RSYNC_EXPERIMENT_CONTROL_DIR_VALUE="${RSYNC_EXPERIMENT_CONTROL_DIR:-}"
RSYNC_EXPERIMENT_DATA_DIR_VALUE="${RSYNC_EXPERIMENT_DATA_DIR:-}"
RSYNC_EXPERIMENT_SCHEDULE_HASH_VALUE="${RSYNC_EXPERIMENT_SCHEDULE_HASH:-}"
RSYNC_REDIS_TIMEOUT_MS_VALUE="${RSYNC_REDIS_TIMEOUT_MS:-100}"
RSYNC_REDIS_MAXMEMORY_VALUE="${RSYNC_REDIS_MAXMEMORY:-}"
RSYNC_REDIS_MAXMEMORY_POLICY_VALUE="${RSYNC_REDIS_MAXMEMORY_POLICY:-}"
RSYNC_REDIS_REQUIRE_EMPTY_VALUE="${RSYNC_REDIS_REQUIRE_EMPTY:-0}"
RSYNC_QUERY_TIMEOUT_MS_VALUE="${RSYNC_QUERY_TIMEOUT_MS:-500}"
RSYNC_FEDERATION_METHOD_VALUE="${RSYNC_FEDERATION_METHOD:-}"
RSYNC_CACHE_POLICY_VALUE="${RSYNC_CACHE_POLICY:-}"
RSYNC_REPLICA_BUDGET_BYTES_VALUE="${RSYNC_REPLICA_BUDGET_BYTES:-}"

declare -a NODE_IDS=()
declare -a SORTED_NODE_IDS=()
declare -a EDGES=()
declare -a DEVICE_IDS=()
declare -a SORTED_DEVICE_IDS=()
declare -a PHYSICAL_EDGES=()
declare -A NODE_SEEN=()
declare -A DEVICE_SEEN=()
declare -A NEIGHBORS_CSV=()
declare -A PHYSICAL_NEIGHBORS_CSV=()
declare -A DEVICE_ROLE=()
declare -A LINK_NAME=()
declare -A LINK_LOCAL_IP=()
declare -A LINK_PEER_IP=()
declare -A NEXT_HOP=()
declare -A HOST_SERVICE_NEIGHBOR=()
LEAF_COUNT=0
SPINE_COUNT=0
PROTOCOL_MAX_HOP_COUNT=8

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  scripts/kathara/render_ruikekkk_scenario.sh <scenario>
  scripts/kathara/render_ruikekkk_scenario.sh --list
EOF
}

list_scenarios() {
  local file
  for file in "${SCENARIOS_DIR}"/*.yaml; do
    [[ -e "${file}" ]] || continue
    basename "${file}" .yaml
  done | sort
}

normalize_scenario_name() {
  local value="$1"
  value="${value%.yaml}"
  printf '%s\n' "${value}"
}

require_scenario_arg() {
  [[ $# -eq 1 ]] || {
    usage >&2
    exit 1
  }
}

sorted_neighbors() {
  local node_id="$1"
  local neighbors="${NEIGHBORS_CSV[${node_id}]:-}"
  [[ -n "${neighbors}" ]] || return 0
  printf '%s\n' "${neighbors}" | tr ',' '\n' | sed '/^$/d' | sort -n
}

sorted_physical_neighbors() {
  local device_id="$1"
  local neighbors="${PHYSICAL_NEIGHBORS_CSV[${device_id}]:-}"
  [[ -n "${neighbors}" ]] || return 0
  printf '%s\n' "${neighbors}" | tr ',' '\n' | sed '/^$/d' | sort -n
}

append_physical_neighbor() {
  local left="$1"
  local right="$2"
  if [[ -n "${PHYSICAL_NEIGHBORS_CSV[${left}]:-}" ]]; then
    PHYSICAL_NEIGHBORS_CSV["${left}"]+=",${right}"
  else
    PHYSICAL_NEIGHBORS_CSV["${left}"]="${right}"
  fi
}

is_v3_scenario() {
  local scenario_name="$1"
  [[ "${scenario_name}" == kua-dc-v3-* || "${scenario_name}" == kua-dc-v4-* ]]
}

is_federation_scenario() {
  local scenario_name="$1"
  [[ "${scenario_name}" == kua-dc-v3-federation-v1-* ||
     "${scenario_name}" == kua-dc-v3-federation-v2-* ||
     "${scenario_name}" == kua-dc-v3-federation-v3-pilot-* ||
     "${scenario_name}" == kua-dc-v3-federation-v3-bf*-h3-fallback-pilot-* ||
     "${scenario_name}" == kua-dc-v3-federation-v3-bf*-h3-pilot-* ]]
}

loopback_ip() {
  local node_id="$1"
  if ((node_id <= 254)); then
    printf '%s.%s\n' "${LOOPBACK_PREFIX}" "${node_id}"
  else
    # Federation-v1 uses disjoint switch IDs 301-404. Protocol hosts remain
    # in 10.90.0.1/24; internal switch loopbacks use a legal management range.
    printf '10.91.%s.%s\n' "$((node_id / 256))" "$((node_id % 256))"
  fi
}

node_port() {
  local node_id="$1"
  printf '%s\n' "$((PORT_BASE + node_id))"
}

parse_topology() {
  local scenario_file="$1"
  local current_id=""
  local line=""

  while IFS= read -r line; do
    if [[ "${line}" =~ ^[[:space:]]*-[[:space:]]*id:[[:space:]]*([0-9]+)[[:space:]]*$ ]]; then
      current_id="${BASH_REMATCH[1]}"
      [[ -z "${NODE_SEEN[${current_id}]:-}" ]] || fail "duplicate node id: ${current_id}"
      NODE_IDS+=("${current_id}")
      NODE_SEEN["${current_id}"]=1
      continue
    fi

    if [[ "${line}" =~ ^[[:space:]]*id:[[:space:]]*([0-9]+)[[:space:]]*$ ]]; then
      current_id="${BASH_REMATCH[1]}"
      [[ -z "${NODE_SEEN[${current_id}]:-}" ]] || fail "duplicate node id: ${current_id}"
      NODE_IDS+=("${current_id}")
      NODE_SEEN["${current_id}"]=1
      continue
    fi

    if [[ "${line}" =~ ^[[:space:]]*neighbors:[[:space:]]*\[(.*)\][[:space:]]*$ ]]; then
      [[ -n "${current_id}" ]] || fail "neighbors encountered before node id in ${scenario_file}"
      NEIGHBORS_CSV["${current_id}"]="${BASH_REMATCH[1]// /}"
    fi
  done <"${scenario_file}"

  ((${#NODE_IDS[@]} > 0)) || fail "no nodes found in ${scenario_file}"
  mapfile -t SORTED_NODE_IDS < <(printf '%s\n' "${NODE_IDS[@]}" | sort -n)
}

build_edges() {
  local node_id=""
  local neighbor=""
  local neighbor_line=""

  for node_id in "${SORTED_NODE_IDS[@]}"; do
    [[ -n "${NEIGHBORS_CSV[${node_id}]:-}" ]] || fail "missing neighbors for node ${node_id}"
    while IFS= read -r neighbor_line; do
      [[ -n "${neighbor_line}" ]] || continue
      neighbor="${neighbor_line}"
      [[ -n "${NODE_SEEN[${neighbor}]:-}" ]] || fail "node ${node_id} references missing neighbor ${neighbor}"
      if ! sorted_neighbors "${neighbor}" | grep -Fxq "${node_id}"; then
        fail "neighbor relationship is not reciprocal between node ${node_id} and node ${neighbor}"
      fi
      if ((node_id < neighbor)); then
        EDGES+=("${node_id}:${neighbor}")
      fi
    done < <(sorted_neighbors "${node_id}")
  done

  ((${#EDGES[@]} > 0)) || fail "no edges found"
}

add_device() {
  local device_id="$1"
  local role="$2"
  [[ -z "${DEVICE_SEEN[${device_id}]:-}" ]] || return 0
  DEVICE_IDS+=("${device_id}")
  DEVICE_SEEN["${device_id}"]=1
  DEVICE_ROLE["${device_id}"]="${role}"
  PHYSICAL_NEIGHBORS_CSV["${device_id}"]=""
}

build_legacy_physical_topology() {
  local node_id=""

  for node_id in "${SORTED_NODE_IDS[@]}"; do
    add_device "${node_id}" "host"
    PHYSICAL_NEIGHBORS_CSV["${node_id}"]="${NEIGHBORS_CSV[${node_id}]}"
  done
}

build_v3_physical_topology() {
  local host_count="${#SORTED_NODE_IDS[@]}"
  local host_id=""
  local leaf_id=""
  local spine_id=""
  local rack_index=0

  if ((host_count != 16 && host_count != 32)); then
    fail "Kua-DC v3/v4 scenarios must have 16 or 32 protocol hosts, got ${host_count}"
  fi
  if ((host_count % 4 != 0)); then
    fail "Kua-DC v3/v4 host count must be divisible by 4"
  fi

  LEAF_COUNT=$((host_count / 4))
  SPINE_COUNT=4

  for host_id in "${SORTED_NODE_IDS[@]}"; do
    add_device "${host_id}" "host"
  done
  for rack_index in $(seq 1 "${LEAF_COUNT}"); do
    add_device "$((100 + rack_index))" "switch"
  done
  for spine_id in $(seq 201 $((200 + SPINE_COUNT))); do
    add_device "${spine_id}" "switch"
  done

  for host_id in "${SORTED_NODE_IDS[@]}"; do
    rack_index=$(((host_id - 1) / 4 + 1))
    leaf_id=$((100 + rack_index))
    HOST_SERVICE_NEIGHBOR["${host_id}"]="${leaf_id}"
    append_physical_neighbor "${host_id}" "${leaf_id}"
    append_physical_neighbor "${leaf_id}" "${host_id}"
  done

  for rack_index in $(seq 1 "${LEAF_COUNT}"); do
    leaf_id=$((100 + rack_index))
    for spine_id in $(seq 201 $((200 + SPINE_COUNT))); do
      append_physical_neighbor "${leaf_id}" "${spine_id}"
      append_physical_neighbor "${spine_id}" "${leaf_id}"
    done
  done
}

build_federation_physical_topology() {
  local host_count="${#SORTED_NODE_IDS[@]}"
  local host_id="" rack_index=0 leaf_id="" spine_id=""
  ((host_count == 64)) || fail "federation experiments require exactly 64 protocol hosts"
  LEAF_COUNT=16
  SPINE_COUNT=8
  for host_id in "${SORTED_NODE_IDS[@]}"; do add_device "${host_id}" "host"; done
  for rack_index in $(seq 1 8); do add_device "$((100 + rack_index))" "switch"; done
  for spine_id in $(seq 201 204); do add_device "${spine_id}" "switch"; done
  for rack_index in $(seq 1 8); do add_device "$((300 + rack_index))" "switch"; done
  for spine_id in $(seq 401 404); do add_device "${spine_id}" "switch"; done
  for host_id in "${SORTED_NODE_IDS[@]}"; do
    if ((host_id <= 32)); then
      rack_index=$(((host_id - 1) / 4 + 1)); leaf_id=$((100 + rack_index))
    else
      rack_index=$(((host_id - 33) / 4 + 1)); leaf_id=$((300 + rack_index))
    fi
    HOST_SERVICE_NEIGHBOR["${host_id}"]="${leaf_id}"
    append_physical_neighbor "${host_id}" "${leaf_id}"
    append_physical_neighbor "${leaf_id}" "${host_id}"
  done
  for rack_index in $(seq 1 8); do
    leaf_id=$((100 + rack_index))
    for spine_id in $(seq 201 204); do
      append_physical_neighbor "${leaf_id}" "${spine_id}"; append_physical_neighbor "${spine_id}" "${leaf_id}"
    done
    leaf_id=$((300 + rack_index))
    for spine_id in $(seq 401 404); do
      append_physical_neighbor "${leaf_id}" "${spine_id}"; append_physical_neighbor "${spine_id}" "${leaf_id}"
    done
  done
  # The only physical federation edge.  Its interfaces are configured but
  # left down until the experiment controller activates them at t0.
  append_physical_neighbor 1 33
  append_physical_neighbor 33 1
}

build_physical_edges() {
  local device_id=""
  local neighbor=""
  local neighbor_line=""

  mapfile -t SORTED_DEVICE_IDS < <(printf '%s\n' "${DEVICE_IDS[@]}" | sort -n)
  for device_id in "${SORTED_DEVICE_IDS[@]}"; do
    [[ -n "${PHYSICAL_NEIGHBORS_CSV[${device_id}]+set}" ]] || fail "missing physical neighbors for device ${device_id}"
    while IFS= read -r neighbor_line; do
      [[ -n "${neighbor_line}" ]] || continue
      neighbor="${neighbor_line}"
      [[ -n "${DEVICE_SEEN[${neighbor}]:-}" ]] || fail "device ${device_id} references missing physical neighbor ${neighbor}"
      if ! sorted_physical_neighbors "${neighbor}" | grep -Fxq "${device_id}"; then
        fail "physical neighbor relationship is not reciprocal between ${device_id} and ${neighbor}"
      fi
      if ((device_id < neighbor)); then
        PHYSICAL_EDGES+=("${device_id}:${neighbor}")
      fi
    done < <(sorted_physical_neighbors "${device_id}")
  done

  ((${#PHYSICAL_EDGES[@]} > 0)) || fail "no physical edges found"
}

assign_link_addresses() {
  local edge_index=0
  local edge=""
  local left=""
  local right=""
  local octet3=0
  local subnet_base=0
  local left_ip=""
  local right_ip=""
  local link_name=""

  for edge in "${PHYSICAL_EDGES[@]}"; do
    IFS=':' read -r left right <<<"${edge}"
    octet3=$((LINK_OCTET3_BASE + edge_index / LINKS_PER_OCTET3))
    subnet_base=$((((edge_index % LINKS_PER_OCTET3) * 4)))
    left_ip="172.${LINK_OCTET2}.${octet3}.$((subnet_base + 1))"
    right_ip="172.${LINK_OCTET2}.${octet3}.$((subnet_base + 2))"
    link_name="link${left}_${right}"

    LINK_NAME["${left}:${right}"]="${link_name}"
    LINK_NAME["${right}:${left}"]="${link_name}"
    LINK_LOCAL_IP["${left}:${right}"]="${left_ip}"
    LINK_LOCAL_IP["${right}:${left}"]="${right_ip}"
    LINK_PEER_IP["${left}:${right}"]="${right_ip}"
    LINK_PEER_IP["${right}:${left}"]="${left_ip}"

    edge_index=$((edge_index + 1))
  done
}

validate_link_delay() {
  [[ "${LINK_DELAY_MS}" =~ ^[0-9]+$ ]] || fail "KATHARA_LINK_DELAY_MS must be a non-negative integer"
  [[ "${LINK_RATE_MBIT}" =~ ^[0-9]+$ ]] || fail "KATHARA_LINK_RATE_MBIT must be a non-negative integer"
  [[ "${LINK_QUEUE_PACKETS}" =~ ^[1-9][0-9]*$ ]] || fail "KATHARA_LINK_QUEUE_PACKETS must be positive"
  [[ "${LINK_MTU}" =~ ^[1-9][0-9]*$ ]] || fail "KATHARA_LINK_MTU must be positive"
}

bfs_next_hop() {
  local source="$1"
  local target="$2"
  local current=""
  local neighbor=""
  local q_index=0
  local -a queue=("${source}")
  local -A seen=()
  local -A parent=()

  seen["${source}"]=1

  while ((q_index < ${#queue[@]})); do
    current="${queue[${q_index}]}"
    q_index=$((q_index + 1))
    while IFS= read -r neighbor; do
      [[ -n "${neighbor}" ]] || continue
      if [[ -z "${seen[${neighbor}]:-}" ]]; then
        seen["${neighbor}"]=1
        parent["${neighbor}"]="${current}"
        queue+=("${neighbor}")
      fi
    done < <(sorted_neighbors "${current}")
  done

  [[ -n "${seen[${target}]:-}" ]] || fail "graph is disconnected between node ${source} and node ${target}"

  current="${target}"
  while [[ "${parent[${current}]:-}" != "${source}" ]]; do
    current="${parent[${current}]:-}"
    [[ -n "${current}" ]] || fail "unable to resolve next hop from node ${source} to node ${target}"
  done

  printf '%s\n' "${current}"
}

compute_next_hops() {
  local source=""
  local target=""
  local current=""
  local neighbor=""
  local q_index=0

  for source in "${SORTED_DEVICE_IDS[@]}"; do
    local -a queue=("${source}")
    local -A seen=()
    local -A parent=()
    q_index=0
    seen["${source}"]=1

    while ((q_index < ${#queue[@]})); do
      current="${queue[${q_index}]}"
      q_index=$((q_index + 1))
      while IFS= read -r neighbor; do
        [[ -n "${neighbor}" ]] || continue
        if [[ -z "${seen[${neighbor}]:-}" ]]; then
          seen["${neighbor}"]=1
          parent["${neighbor}"]="${current}"
          queue+=("${neighbor}")
        fi
      done < <(sorted_physical_neighbors "${current}")
    done

    for target in "${SORTED_NODE_IDS[@]}"; do
      [[ "${target}" != "${source}" ]] || continue
      [[ -n "${seen[${target}]:-}" ]] || fail "graph is disconnected between node ${source} and node ${target}"

      current="${target}"
      while [[ "${parent[${current}]:-}" != "${source}" ]]; do
        current="${parent[${current}]:-}"
        [[ -n "${current}" ]] || fail "unable to resolve next hop from node ${source} to node ${target}"
      done
      NEXT_HOP["${source}:${target}"]="${current}"
    done
  done
}

render_topology_yaml() {
  local scenario_file="$1"
  local line=""
  local node_id=""
  local base_indent=""

  : >"${OUTPUT_TOPOLOGY}"
  while IFS= read -r line; do
    if [[ "${line}" =~ ^([[:space:]]*)-[[:space:]]*id:[[:space:]]*([0-9]+)[[:space:]]*$ ]]; then
      base_indent="${BASH_REMATCH[1]}"
      node_id="${BASH_REMATCH[2]}"
      printf '%s\n' "${line}" >>"${OUTPUT_TOPOLOGY}"
      printf '%s  host: %s\n' "${base_indent}" "$(loopback_ip "${node_id}")" >>"${OUTPUT_TOPOLOGY}"
      printf '%s  port: %s\n' "${base_indent}" "$(node_port "${node_id}")" >>"${OUTPUT_TOPOLOGY}"
      continue
    fi

    if [[ "${line}" =~ ^([[:space:]]*)id:[[:space:]]*([0-9]+)[[:space:]]*$ ]]; then
      base_indent="${BASH_REMATCH[1]}"
      node_id="${BASH_REMATCH[2]}"
      printf '%s\n' "${line}" >>"${OUTPUT_TOPOLOGY}"
      printf '%s  host: %s\n' "${base_indent}" "$(loopback_ip "${node_id}")" >>"${OUTPUT_TOPOLOGY}"
      printf '%s  port: %s\n' "${base_indent}" "$(node_port "${node_id}")" >>"${OUTPUT_TOPOLOGY}"
      continue
    fi

    if [[ "${line}" =~ ^[[:space:]]*host:[[:space:]]*.*$ ]]; then
      continue
    fi

    if [[ "${line}" =~ ^[[:space:]]*port:[[:space:]]*.*$ ]]; then
      continue
    fi

    printf '%s\n' "${line}" >>"${OUTPUT_TOPOLOGY}"
  done <"${scenario_file}"
}

render_federation_cluster_topologies() {
  local cluster="" output="" low=0 high=0 node_id="" neighbors=""
  for cluster in a b; do
    if [[ "${cluster}" == "a" ]]; then low=1; high=32; else low=33; high=64; fi
    output="${SHARED_DIR}/ruikekkk-topology-cluster-${cluster}.yaml"
    {
      printf 'protocol:\n  max_hop_count: %s\n\nnodes:\n' "${PROTOCOL_MAX_HOP_COUNT}"
      for node_id in "${SORTED_NODE_IDS[@]}"; do
        ((node_id >= low && node_id <= high)) || continue
        neighbors="$(sorted_neighbors "${node_id}" | awk 'BEGIN{first=1}{if(!first)printf ", "; printf "%s",$0; first=0}')"
        printf -- '- id: %s\n  host: %s\n  port: %s\n  neighbors: [%s]\n' \
          "${node_id}" "$(loopback_ip "${node_id}")" "$(node_port "${node_id}")" "${neighbors}"
      done
    } >"${output}"
  done
}

write_lab_conf() {
  local scenario_name="$1"
  local node_id=""
  local index=0
  local neighbor=""
  local lab_file="${LAB_DIR}/lab.conf"

  {
    printf 'LAB_NAME="ruikekkk-%s"\n' "${scenario_name}"
    printf 'LAB_DESCRIPTION="Scenario-selectable Kathara lab for %s"\n' "${scenario_name}"
    printf 'LAB_VERSION="%s"\n' "${LAB_VERSION}"
    printf '\n'

    for node_id in "${SORTED_DEVICE_IDS[@]}"; do
      index=0
      while IFS= read -r neighbor; do
        [[ -n "${neighbor}" ]] || continue
        printf 'node%s[%s]="%s"\n' "${node_id}" "${index}" "${LINK_NAME[${node_id}:${neighbor}]}"
        index=$((index + 1))
      done < <(sorted_physical_neighbors "${node_id}")

      printf 'node%s[image]="%s"\n' "${node_id}" "${IMAGE_TAG}"
      printf 'node%s[entrypoint]="/bin/sleep"\n' "${node_id}"
      printf 'node%s[args]="infinity"\n' "${node_id}"
      printf 'node%s[shell]="/bin/sh"\n' "${node_id}"
      printf 'node%s[ipv6]="false"\n' "${node_id}"
      printf '\n'
    done
  } >"${lab_file}"
}

write_lab_dep() {
  local root="${SORTED_DEVICE_IDS[0]}"
  local q_index=0
  local current=""
  local neighbor=""
  local node_id=""
  local -a queue=("${root}")
  local -A seen=()
  local -A parent=()
  local children=""
  local dep_file="${LAB_DIR}/lab.dep"

  seen["${root}"]=1

  while ((q_index < ${#queue[@]})); do
    current="${queue[${q_index}]}"
    q_index=$((q_index + 1))
    while IFS= read -r neighbor; do
      [[ -n "${neighbor}" ]] || continue
      if [[ -z "${seen[${neighbor}]:-}" ]]; then
        seen["${neighbor}"]=1
        parent["${neighbor}"]="${current}"
        queue+=("${neighbor}")
      fi
    done < <(sorted_physical_neighbors "${current}")
  done

  ((${#queue[@]} == ${#SORTED_DEVICE_IDS[@]})) || fail "physical graph is disconnected; unable to write lab.dep"

  : >"${dep_file}"
  for node_id in "${SORTED_DEVICE_IDS[@]}"; do
    children=""
    for neighbor in "${SORTED_DEVICE_IDS[@]}"; do
      if [[ "${parent[${neighbor}]:-}" == "${node_id}" ]]; then
        children="${children} node${neighbor}"
      fi
    done
    if [[ -n "${children}" ]]; then
      printf 'node%s:%s\n' "${node_id}" "${children}" >>"${dep_file}"
    fi
  done
}

write_node_assets() {
  local node_id=""
  local neighbor=""
  local next_hop=""
  local route_dev=""
  local route_via=""
  local index=0
  local node_name=""
  local network_file=""
  local startup_file=""
  local service_neighbor=""
  local federation_interface=""

  rm -f "${LAB_DIR}"/node*.startup
  rm -rf "${GENERATED_DIR}"
  mkdir -p "${GENERATED_DIR}" "${SHARED_DIR}/logs" "${SHARED_DIR}/state"

  for node_id in "${SORTED_DEVICE_IDS[@]}"; do
    node_name="node${node_id}"
    network_file="${GENERATED_DIR}/${node_name}.network.sh"
    startup_file="${LAB_DIR}/${node_name}.startup"

    {
      federation_interface=""
      service_neighbor="${HOST_SERVICE_NEIGHBOR[${node_id}]:-}"

      printf '#!/bin/sh\n'
      printf 'set -eu\n\n'
      printf 'NODE_LOOPBACK_IP="%s"\n\n' "$(loopback_ip "${node_id}")"
      printf 'NODE_LINK_DELAY_MS="%s"\n\n' "${LINK_DELAY_MS}"
      printf 'configure_network_node() {\n'
      printf '  ip link set dev lo up\n'
      if is_federation_scenario "${scenario_name}" && { [[ "${node_id}" == "1" ]] || [[ "${node_id}" == "33" ]]; }; then
        printf '  sysctl -w net.ipv4.ip_forward=1 >/dev/null\n'
      fi
      if [[ -z "${service_neighbor}" ]]; then
        printf '  ip address replace "%s/32" dev lo\n' "$(loopback_ip "${node_id}")"
      fi

      index=0
      while IFS= read -r neighbor; do
        [[ -n "${neighbor}" ]] || continue
        printf '  ip link set dev eth%s up\n' "${index}"
        printf '  ip link set dev eth%s mtu "%s"\n' "${index}" "${LINK_MTU}"
        printf '  ip address replace "%s/30" dev eth%s\n' "${LINK_LOCAL_IP[${node_id}:${neighbor}]}" "${index}"
        if [[ -n "${service_neighbor}" && "${neighbor}" == "${service_neighbor}" ]]; then
          printf '  ip address replace "%s/32" dev eth%s\n' "$(loopback_ip "${node_id}")" "${index}"
        fi
        if [[ "${LINK_DELAY_MS}" -gt 0 || "${LINK_RATE_MBIT}" -gt 0 ]]; then
          printf '  tc qdisc replace dev eth%s root netem' "${index}"
          if [[ "${LINK_DELAY_MS}" -gt 0 ]]; then
            printf ' delay "%sms"' "${LINK_DELAY_MS}"
          fi
          if [[ "${LINK_RATE_MBIT}" -gt 0 ]]; then
            printf ' rate "%smbit"' "${LINK_RATE_MBIT}"
          fi
          printf ' limit "%s"\n' "${LINK_QUEUE_PACKETS}"
        fi
        if is_federation_scenario "${scenario_name}" && \
           { [[ "${node_id}:${neighbor}" == "1:33" ]] || [[ "${node_id}:${neighbor}" == "33:1" ]]; }; then
          federation_interface="eth${index}"
          printf '  ip link set dev eth%s down\n' "${index}"
        fi
        index=$((index + 1))
      done < <(sorted_physical_neighbors "${node_id}")

      for neighbor in "${SORTED_NODE_IDS[@]}"; do
        if [[ "${neighbor}" == "${node_id}" ]]; then
          continue
        fi
        if is_federation_scenario "${scenario_name}"; then
          if ((node_id == 1 && neighbor > 32)); then continue; fi
          if ((node_id == 33 && neighbor <= 32)); then continue; fi
        fi
        if is_federation_scenario "${scenario_name}" && [[ "${RSYNC_FEDERATION_METHOD_VALUE}" == "nc" ]]; then
          if ((node_id <= 64)); then
            if ((node_id <= 32 && neighbor > 32 && !(node_id == 1 && neighbor == 33))); then continue; fi
            if ((node_id >= 33 && neighbor <= 32 && !(node_id == 33 && neighbor == 1))); then continue; fi
          else
            if ((node_id < 300 && neighbor > 32)); then continue; fi
            if ((node_id >= 300 && neighbor <= 32)); then continue; fi
          fi
        fi
        next_hop="${NEXT_HOP[${node_id}:${neighbor}]:-}"
        [[ -n "${next_hop}" ]] || fail "missing cached next hop from node ${node_id} to node ${neighbor}"
        route_via="${LINK_PEER_IP[${node_id}:${next_hop}]}"
        route_dev=""
        index=0
        while IFS= read -r current_neighbor; do
          [[ -n "${current_neighbor}" ]] || continue
          if [[ "${current_neighbor}" == "${next_hop}" ]]; then
            route_dev="eth${index}"
            break
          fi
          index=$((index + 1))
        done < <(sorted_physical_neighbors "${node_id}")
        [[ -n "${route_dev}" ]] || fail "unable to map route device for node ${node_id} -> ${next_hop}"
        if [[ -n "${service_neighbor}" ]]; then
          printf '  ip route replace "%s/32" via "%s" dev %s src "%s"\n' "$(loopback_ip "${neighbor}")" "${route_via}" "${route_dev}" "$(loopback_ip "${node_id}")"
        else
          printf '  ip route replace "%s/32" via "%s" dev %s\n' "$(loopback_ip "${neighbor}")" "${route_via}" "${route_dev}"
        fi
      done

      printf '}\n'
    } >"${network_file}"

    {
      printf 'set -eu\n\n'
      if [[ "${DEVICE_ROLE[${node_id}]:-host}" == "switch" ]]; then
        printf '/shared/start-switch.sh %s %s /shared/generated/%s.network.sh\n' "${node_name}" "${node_id}" "${node_name}"
      else
        printf 'export RSYNC_LOOP_SLEEP_MS="%s"\n' "${NODE_LOOP_SLEEP_MS}"
        if [[ -n "${NODE_LOOP_ITERATIONS_VALUE}" ]]; then
          printf 'export RSYNC_LOOP_ITERATIONS="%s"\n' "${NODE_LOOP_ITERATIONS_VALUE}"
        fi
        printf 'export RSYNC_OBSERVABILITY="%s"\n' "${RSYNC_OBSERVABILITY_VALUE}"
        printf 'export RSYNC_EVENT_TRACE="%s"\n' "${RSYNC_EVENT_TRACE_VALUE}"
        printf 'export RSYNC_EVENT_TRACE_FLUSH_EVERY="%s"\n' "${RSYNC_EVENT_TRACE_FLUSH_EVERY_VALUE}"
        printf 'export RSYNC_WIRE_VERSION="%s"\n' "${RSYNC_WIRE_VERSION_VALUE}"
        printf 'export RSYNC_BLOOM_BITS="%s"\n' "${RSYNC_BLOOM_BITS_VALUE}"
        printf 'export RSYNC_BLOOM_HASH_COUNT="%s"\n' "${RSYNC_BLOOM_HASH_COUNT_VALUE}"
        printf 'export RSYNC_STAGED_FALLBACK="%s"\n' "${RSYNC_STAGED_FALLBACK_VALUE}"
        printf 'export RSYNC_FALLBACK_DELAY_MS="%s"\n' "${RSYNC_FALLBACK_DELAY_MS_VALUE}"
        printf 'export RSYNC_REDIS_TIMEOUT_MS="%s"\n' "${RSYNC_REDIS_TIMEOUT_MS_VALUE}"
        if [[ -n "${RSYNC_REDIS_MAXMEMORY_VALUE}" ]]; then
          printf 'export RSYNC_REDIS_MAXMEMORY="%s"\n' "${RSYNC_REDIS_MAXMEMORY_VALUE}"
        fi
        if [[ -n "${RSYNC_REDIS_MAXMEMORY_POLICY_VALUE}" ]]; then
          printf 'export RSYNC_REDIS_MAXMEMORY_POLICY="%s"\n' "${RSYNC_REDIS_MAXMEMORY_POLICY_VALUE}"
        fi
        printf 'export RSYNC_REDIS_REQUIRE_EMPTY="%s"\n' "${RSYNC_REDIS_REQUIRE_EMPTY_VALUE}"
        if [[ -n "${RSYNC_CACHE_POLICY_VALUE}" ]]; then
          printf 'export RSYNC_CACHE_POLICY="%s"\n' "${RSYNC_CACHE_POLICY_VALUE}"
        fi
        if [[ -n "${RSYNC_REPLICA_BUDGET_BYTES_VALUE}" ]]; then
          printf 'export RSYNC_REPLICA_BUDGET_BYTES="%s"\n' "${RSYNC_REPLICA_BUDGET_BYTES_VALUE}"
        fi
        printf 'export RSYNC_QUERY_TIMEOUT_MS="%s"\n' "${RSYNC_QUERY_TIMEOUT_MS_VALUE}"
        if is_federation_scenario "${scenario_name}"; then
          if [[ "${RSYNC_FEDERATION_METHOD_VALUE}" != "nc" ]]; then
            printf 'export RSYNC_TOPOLOGY_CONFIG="/shared/ruikekkk-topology.yaml"\n'
          elif ((node_id <= 32)); then
            printf 'export RSYNC_TOPOLOGY_CONFIG="/shared/ruikekkk-topology-cluster-a.yaml"\n'
          else
            printf 'export RSYNC_TOPOLOGY_CONFIG="/shared/ruikekkk-topology-cluster-b.yaml"\n'
          fi
          if [[ -n "${federation_interface}" ]]; then
            printf 'export RSYNC_FEDERATION_INTERFACE="%s"\n' "${federation_interface}"
          fi
        fi
        if [[ -n "${RSYNC_EXPERIMENT_CONTROL_DIR_VALUE}" ]]; then
          printf 'export RSYNC_EXPERIMENT_CONTROL_DIR="%s"\n' "${RSYNC_EXPERIMENT_CONTROL_DIR_VALUE}"
        fi
        if [[ -n "${RSYNC_EXPERIMENT_DATA_DIR_VALUE}" ]]; then
          printf 'export RSYNC_EXPERIMENT_DATA_FILE="%s/%s.tsv"\n' \
            "${RSYNC_EXPERIMENT_DATA_DIR_VALUE}" "${node_name}"
        fi
        if [[ -n "${RSYNC_EXPERIMENT_SCHEDULE_HASH_VALUE}" ]]; then
          printf 'export RSYNC_EXPERIMENT_SCHEDULE_HASH="%s"\n' "${RSYNC_EXPERIMENT_SCHEDULE_HASH_VALUE}"
        fi
        if [[ -n "${RSYNC_EVENT_TRACE_RUN_ID_VALUE}" ]]; then
          printf 'export RSYNC_EVENT_TRACE_RUN_ID="%s"\n' "${RSYNC_EVENT_TRACE_RUN_ID_VALUE}"
        fi
        printf 'export RSYNC_SKIP_APP="%s"\n' "${RSYNC_SKIP_APP_VALUE}"
        printf 'export RSYNC_ORACLE_UNICAST="%s"\n' "${RSYNC_ORACLE_UNICAST_VALUE}"
        if [[ -n "${RSYNC_ORACLE_UNICAST_SCHEDULE_DIR_VALUE}" ]]; then
          printf 'export RSYNC_ORACLE_UNICAST_SCHEDULE="%s/%s.oracle_unicast.tsv"\n' \
            "${RSYNC_ORACLE_UNICAST_SCHEDULE_DIR_VALUE}" "${node_name}"
        fi
        printf '/shared/start-node.sh %s %s /shared/generated/%s.network.sh\n' "${node_name}" "${node_id}" "${node_name}"
      fi
    } >"${startup_file}"

    chmod +x "${network_file}" "${startup_file}"
  done
}

main() {
  local scenario_arg="${1:-}"
  local scenario_name=""
  local scenario_file=""

  if [[ "${scenario_arg}" == "--list" ]]; then
    list_scenarios
    exit 0
  fi

  require_scenario_arg "$@"

  scenario_name="$(normalize_scenario_name "${scenario_arg}")"
  scenario_file="${SCENARIOS_DIR}/${scenario_name}.yaml"
  [[ -f "${scenario_file}" ]] || fail "unknown scenario: ${scenario_name}"
  PROTOCOL_MAX_HOP_COUNT=$(awk '/^[[:space:]]*max_hop_count:[[:space:]]*[0-9]+[[:space:]]*$/{print $2; exit}' "${scenario_file}")
  [[ "${PROTOCOL_MAX_HOP_COUNT}" =~ ^[0-9]+$ ]] || fail "missing or invalid protocol.max_hop_count in ${scenario_file}"

  parse_topology "${scenario_file}"
  build_edges
  if is_federation_scenario "${scenario_name}"; then
    build_federation_physical_topology
  elif is_v3_scenario "${scenario_name}"; then
    build_v3_physical_topology
  else
    build_legacy_physical_topology
  fi
  build_physical_edges
  validate_link_delay
  assign_link_addresses
  compute_next_hops

  mkdir -p "${SHARED_DIR}"
  printf '%s\n' "${scenario_name}" >"${ACTIVE_SCENARIO_FILE}"
  render_topology_yaml "${scenario_file}"
  if is_federation_scenario "${scenario_name}"; then
    render_federation_cluster_topologies
  fi
  write_lab_conf "${scenario_name}"
  write_lab_dep
  write_node_assets

  printf 'scenario=%s\n' "${scenario_name}"
  printf 'source=%s\n' "${scenario_file}"
  printf 'lab_dir=%s\n' "${LAB_DIR}"
  printf 'rendered_topology=%s\n' "${OUTPUT_TOPOLOGY}"
  printf 'node_count=%s\n' "${#SORTED_NODE_IDS[@]}"
  printf 'edge_count=%s\n' "${#EDGES[@]}"
  printf 'host_count=%s\n' "${#SORTED_NODE_IDS[@]}"
  printf 'leaf_count=%s\n' "${LEAF_COUNT}"
  printf 'spine_count=%s\n' "${SPINE_COUNT}"
  printf 'device_count=%s\n' "${#SORTED_DEVICE_IDS[@]}"
  printf 'physical_edge_count=%s\n' "${#PHYSICAL_EDGES[@]}"
  printf 'link_delay_ms=%s\n' "${LINK_DELAY_MS}"
  printf 'link_rate_mbit=%s\n' "${LINK_RATE_MBIT}"
  printf 'link_queue_packets=%s\n' "${LINK_QUEUE_PACKETS}"
  printf 'link_mtu=%s\n' "${LINK_MTU}"
  printf 'rsync_observability=%s\n' "${RSYNC_OBSERVABILITY_VALUE}"
  printf 'rsync_event_trace=%s\n' "${RSYNC_EVENT_TRACE_VALUE}"
  printf 'rsync_event_trace_run_id=%s\n' "${RSYNC_EVENT_TRACE_RUN_ID_VALUE}"
  printf 'rsync_bloom_bits=%s\n' "${RSYNC_BLOOM_BITS_VALUE}"
  printf 'rsync_bloom_hash_count=%s\n' "${RSYNC_BLOOM_HASH_COUNT_VALUE}"
  printf 'rsync_staged_fallback=%s\n' "${RSYNC_STAGED_FALLBACK_VALUE}"
  printf 'rsync_fallback_delay_ms=%s\n' "${RSYNC_FALLBACK_DELAY_MS_VALUE}"
  printf 'protocol_max_hop_count=%s\n' "${PROTOCOL_MAX_HOP_COUNT}"
  printf 'rsync_skip_app=%s\n' "${RSYNC_SKIP_APP_VALUE}"
  printf 'rsync_oracle_unicast=%s\n' "${RSYNC_ORACLE_UNICAST_VALUE}"
  printf 'rsync_oracle_unicast_schedule_dir=%s\n' "${RSYNC_ORACLE_UNICAST_SCHEDULE_DIR_VALUE}"
}

main "$@"
