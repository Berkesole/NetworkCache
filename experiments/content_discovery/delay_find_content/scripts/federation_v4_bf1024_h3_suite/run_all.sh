#!/usr/bin/env bash
set -uo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
status=0
"${HERE}/00_preflight.sh" || status=$?
if ((status == 0)); then
  "${HERE}/10_networkcache.sh" || status=$?
  "${HERE}/20_baselines.sh" || status=$?
fi
"${HERE}/90_validate.sh" || status=$?
exit "${status}"
