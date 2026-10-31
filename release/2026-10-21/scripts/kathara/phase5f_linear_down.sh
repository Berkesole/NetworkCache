#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LAB_DIR="${LAB_DIR:-${ROOT_DIR}/kathara/phase5f-linear-smoke}"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

command -v kathara >/dev/null 2>&1 || fail "missing required command: kathara"
echo "+ kathara lclean -d $LAB_DIR"
kathara lclean -d "$LAB_DIR"
