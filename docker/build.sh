#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${1:-latest}"
shift "$(( $# > 0 ? 1 : 0 ))"
exec docker build -f "$ROOT_DIR/Dockerfile" -t "rsync-router:${IMAGE_TAG}" "$@" "$ROOT_DIR"
