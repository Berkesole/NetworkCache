#!/usr/bin/env bash
set -u
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
status=0
"${HERE}/00_preflight.sh" || exit 1
for stage in 10_federation_repeated.sh 20_federation_uniform.sh; do
  echo "== federation-v2: ${stage} =="
  "${HERE}/${stage}" || status=1
done
"${HERE}/90_validate.sh" || status=1
exit "${status}"
