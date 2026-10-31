#!/usr/bin/env bash
set -euo pipefail

[[ "${EUID}" -eq 0 ]] || {
  echo "run this bootstrap as root (for example with sudo)" >&2
  exit 2
}

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates git cmake make g++ python3 python3-venv python3-pip \
  iproute2 iptables vde2 rsync zstd

getent group docker >/dev/null
usermod -aG docker simpleai

echo "bootstrap complete; open a fresh simpleai SSH session before deployment"
