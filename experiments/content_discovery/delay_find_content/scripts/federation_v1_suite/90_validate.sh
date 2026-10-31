#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 "${HERE}/run_federation_v1_suite.py" validate
python3 "${HERE}/analyze_federation_v1.py"
