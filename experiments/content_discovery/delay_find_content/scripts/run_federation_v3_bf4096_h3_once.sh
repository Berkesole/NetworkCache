#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export FEDERATION_V3_BLOOM_BITS=4096
exec "${HERE}/run_federation_v3_bf2048_h3_once.sh" "$@"
