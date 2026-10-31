#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SCRIPTS=$(cd -- "${HERE}/.." && pwd)
REPO=$(cd -- "${SCRIPTS}/../../../.." && pwd)
cd "${REPO}"
python3 "${SCRIPTS}/generate_federation_v4_bf1024_h3_formal.py"
python3 "${SCRIPTS}/test_federation_v4_bf1024_h3_formal.py"
python3 -m py_compile \
  "${SCRIPTS}/generate_federation_v4_bf1024_h3_formal.py" \
  "${SCRIPTS}/run_federation_v4_bf1024_h3_formal_suite.py" \
  "${SCRIPTS}/analyze_federation_v4_bf1024_h3_formal.py"
echo "federation-v4 static preflight passed"
