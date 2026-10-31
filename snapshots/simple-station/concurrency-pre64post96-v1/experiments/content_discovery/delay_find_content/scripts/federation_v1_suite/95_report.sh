#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
REPO=$(cd -- "${HERE}/../../../.." && pwd)
"${HERE}/federation_v1_suite/90_validate.sh"
tmp=$(mktemp -d "${REPO}/experiments/reports/.latency-v3-federation.XXXXXX")
trap 'rm -rf "${tmp}"' EXIT
python3 "${REPO}/experiments/reports/generate_latency_v3_report.py" --output-dir "${tmp}"
test -s "${tmp}/overall.pdf" -a -s "${tmp}/overall.en.pdf"
mv -f "${tmp}/overall.pdf" "${REPO}/experiments/reports/latency-v3/overall.pdf"
mv -f "${tmp}/overall.en.pdf" "${REPO}/experiments/reports/latency-v3/overall.en.pdf"
