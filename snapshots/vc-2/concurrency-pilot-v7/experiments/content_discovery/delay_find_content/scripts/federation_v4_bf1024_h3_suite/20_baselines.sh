#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "${HERE}/../run_federation_v4_bf1024_h3_formal_suite.py" run --phase baselines
