#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export TOPOLOGY_FILE="${TOPOLOGY_FILE:-${ROOT_DIR}/config/acceptance/bridge-2node-bad-peer.yaml}"
export NETWORK_NAME="${NETWORK_NAME:-rsync-phase4-bad-peer}"
export NODE1_NAME="${NODE1_NAME:-rsync-phase4-bad-node1}"
export NODE2_NAME="${NODE2_NAME:-rsync-phase4-bad-node2}"
export VERIFY_WAIT_SECONDS="${VERIFY_WAIT_SECONDS:-22}"

exec bash "${ROOT_DIR}/scripts/acceptance/phase3_bridge_verify.sh"
