#!/usr/bin/env bash
set -euo pipefail

POSTFIX_SUITE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
POSTFIX_SCRIPTS_DIR=$(cd -- "${POSTFIX_SUITE_DIR}/.." && pwd)
POSTFIX_EXP_DIR=$(cd -- "${POSTFIX_SCRIPTS_DIR}/.." && pwd)

export FEDERATION_FALLBACK_RESULTS_ROOT="${POSTFIX_EXP_DIR}/results/federation-v3-bf1024-h3-fallback-pilot-postfix"
export FEDERATION_FALLBACK_PAPER_ASSETS_ROOT="${POSTFIX_EXP_DIR}/paper_assets/federation-v3-bf1024-h3-fallback-pilot-postfix"
export FEDERATION_FALLBACK_REQUIRED_GIT_ANCESTOR="aa2c6f24f62d8529b30385dc90a3ebbe961ede51"
export FEDERATION_FALLBACK_DATASET_LABEL="post-fix exploratory pilot; DATA-route and local-provider obligation fixes included"
export FEDERATION_FALLBACK_PLOT_LABEL="Post-fix exploratory pilot; single seed/run; no CI"
