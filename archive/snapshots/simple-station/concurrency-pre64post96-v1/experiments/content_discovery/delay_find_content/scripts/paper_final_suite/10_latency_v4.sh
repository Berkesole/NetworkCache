#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export LATENCY_V3_BUILD_NO_CACHE=0
exec "${HERE}/../latency_v4_bf1024_h3_c512_suite/run_all.sh"
