#!/usr/bin/env bash
set -uo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
status=0
for phase in 10_controlled.sh 20_iid_zipf.sh 30_iid_uniform.sh 40_data_size.sh; do
  echo "== latency-v3: ${phase} =="
  "${HERE}/${phase}" || status=2
done
"${HERE}/90_validate.sh" || status=2
exit "${status}"
