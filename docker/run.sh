#!/usr/bin/env bash
# Run the functional checks once, without persistent containers or host mounts.
set -euo pipefail
IMAGE_TAG="${1:-latest}"
exec docker run --rm --entrypoint /bin/bash "rsync-router:${IMAGE_TAG}" \
  -lc 'cd /app && python3 scripts/tests/verify_local.py --build-dir build'
