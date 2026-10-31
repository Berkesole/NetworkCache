#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
"${HERE}/federation_v3_bf4096_h3_suite/00_preflight.sh"
python3 "${HERE}/run_federation_v3_bf4096_h3_suite.py" run
