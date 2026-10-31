#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${HERE}/postfix_env.sh"
"${HERE}/../federation_v3_bf1024_h3_fallback_suite/00_preflight.sh"
"${HERE}/../federation_v3_bf1024_h3_fallback_suite/05_smoke.sh"
"${HERE}/../federation_v3_bf1024_h3_fallback_suite/10_pilots.sh"
