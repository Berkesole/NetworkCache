#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export FEDERATION_V3_FALLBACK_BUILD_NO_CACHE=0
exec "${HERE}/../federation_v4_bf1024_h3_suite/run_all.sh"
