#!/usr/bin/env bash
set -uo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
status=0
for stage in 10_controlled.sh 20_iid_zipf.sh 30_iid_uniform.sh 40_data_size.sh; do
  echo "== latency-v4: ${stage} =="
  "${HERE}/${stage}" || status=1
done
"${HERE}/90_validate.sh" || status=1
exit "${status}"
