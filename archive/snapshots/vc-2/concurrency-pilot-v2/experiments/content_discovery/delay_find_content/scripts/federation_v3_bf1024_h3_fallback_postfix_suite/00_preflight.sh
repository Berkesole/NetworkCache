#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${HERE}/postfix_env.sh"
exec "${HERE}/../federation_v3_bf1024_h3_fallback_suite/00_preflight.sh"
