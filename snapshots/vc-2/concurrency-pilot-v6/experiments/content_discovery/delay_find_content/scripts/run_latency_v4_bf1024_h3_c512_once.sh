#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
EXP_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)

export LATENCY_SUITE_NAME=latency-v4-bf1024-h3-c512
export LATENCY_WORKLOAD_ROOT="${EXP_DIR}/workloads/latency-v4-bf1024-h3-c512"
export LATENCY_SHARED_NAMESPACE=latency-v4-bf1024-h3-c512
export LATENCY_WIRE_VERSION=3
export LATENCY_BLOOM_BITS=1024
export LATENCY_BLOOM_HASH_COUNT=3
export LATENCY_STAGED_FALLBACK=1
export LATENCY_FALLBACK_DELAY_MS=1500
export LATENCY_QUERY_TIMEOUT_MS=7000
export LATENCY_CONTROLLER_TIMEOUT_MS=8000
export LATENCY_MAX_HOP_COUNT=12
export LATENCY_ENABLE_SNAPSHOTS=1
export LATENCY_TRACE_ANALYZER="${SCRIPT_DIR}/analyze_latency_v4_trace.py"

exec "${SCRIPT_DIR}/run_latency_v3_once.sh" "$@"
