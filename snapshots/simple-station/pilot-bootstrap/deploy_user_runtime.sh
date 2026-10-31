#!/usr/bin/env bash
set -euo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TARGET=${1:-/home/simpleai/zhengzihao/rsync}
BOOTSTRAP=${2:-/home/simpleai/zhengzihao/networkcache-pilot-bootstrap}
VENV=/home/simpleai/zhengzihao/kathara-venv

[[ "$(id -un)" == simpleai ]] || {
  echo "run the user deployment as simpleai" >&2
  exit 2
}
id -nG | tr ' ' '\n' | grep -qx docker || {
  echo "simpleai is not in the docker group; run bootstrap as root and reconnect" >&2
  exit 3
}

for name in repo.bundle pilot-overlay.tar.gz runtime-image.tar.gz \
  kathara-network-plugin.tar.gz kathara-venv.tar.gz; do
  [[ -f "${BOOTSTRAP}/${name}" ]] || {
    echo "missing deployment artifact ${BOOTSTRAP}/${name}" >&2
    exit 4
  }
done
(cd "${BOOTSTRAP}" && sha256sum -c SHA256SUMS)

if [[ ! -d "${TARGET}/.git" ]]; then
  [[ -d "${TARGET}" && -z "$(find "${TARGET}" -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
    echo "refusing to initialize a non-empty unmanaged target: ${TARGET}" >&2
    exit 5
  }
  git clone "${BOOTSTRAP}/repo.bundle" "${TARGET}"
fi
tar -C "${TARGET}" -xzf "${BOOTSTRAP}/pilot-overlay.tar.gz"

if [[ ! -x "${VENV}/bin/python" ]]; then
  mkdir -p "$(dirname "${VENV}")"
  tar -C "$(dirname "${VENV}")" -xzf "${BOOTSTRAP}/kathara-venv.tar.gz"
fi
sed -i "s#/opt/kathara-venv/bin/python#${VENV}/bin/python#" "${VENV}/bin/kathara"
"${VENV}/bin/python" -c \
  'import importlib.metadata as m,sys; sys.exit(m.version("kathara") != "3.8.1.post5")'

gzip -dc "${BOOTSTRAP}/runtime-image.tar.gz" | docker load
image=$(docker image inspect --format '{{.Id}}' rsync-router:ruikekkk-kathara)
[[ "${image}" == sha256:bce2e56d82225efd51335a3af959ec0c0fc199782ab68281fd16f1ff16c13a74 ]]

if ! docker plugin inspect kathara/katharanp_vde:amd64 >/dev/null 2>&1; then
  plugin_build="${BOOTSTRAP}/plugin-build"
  [[ ! -e "${plugin_build}" ]] || {
    echo "refusing to reuse existing plugin build directory" >&2
    exit 6
  }
  mkdir "${plugin_build}"
  tar -C "${plugin_build}" -xzf "${BOOTSTRAP}/kathara-network-plugin.tar.gz"
  docker plugin create kathara/katharanp_vde:amd64 "${plugin_build}"
  docker plugin enable kathara/katharanp_vde:amd64
fi
[[ "$(docker plugin inspect --format '{{.Enabled}}' kathara/katharanp_vde:amd64)" == true ]]

PATH="${VENV}/bin:${PATH}" \
  python3 "${TARGET}/experiments/content_discovery/delay_find_content/scripts/generate_federation_convergence_pilot.py"

{
  echo "deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "target=${TARGET}"
  echo "image=${image}"
  echo "kathara=$(PATH="${VENV}/bin:${PATH}" kathara --version | tr '\n' ' ')"
  echo "overlay_sha256=$(sha256sum "${BOOTSTRAP}/pilot-overlay.tar.gz" | awk '{print $1}')"
} >"${BOOTSTRAP}/deployment-complete.txt"

echo "user runtime deployed; run the pilot with PATH=${VENV}/bin:\$PATH"
