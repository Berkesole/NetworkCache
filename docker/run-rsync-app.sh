#!/usr/bin/env bash
set -euo pipefail

target="${RSYNC_APP_TARGET:-rsync-router}"
binary="/app/build/${target}"
args=()

fail() {
    echo "错误: $1" >&2
    exit 1
}

if [[ ! -x "$binary" ]]; then
    fail "目标二进制不存在或不可执行: $binary"
fi

if [[ -n "${RSYNC_APP_ARGS:-}" && -n "${RSYNC_APP_ARGS_FILE:-}" ]]; then
    fail "RSYNC_APP_ARGS 与 RSYNC_APP_ARGS_FILE 不能同时设置"
fi

if [[ -n "${RSYNC_APP_ARGS_FILE:-}" ]]; then
    if [[ ! -f "${RSYNC_APP_ARGS_FILE}" ]]; then
        fail "参数文件不存在: ${RSYNC_APP_ARGS_FILE}"
    fi

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]] && continue
        args+=("$line")
    done < "${RSYNC_APP_ARGS_FILE}"
fi

if [[ -n "${RSYNC_APP_ARGS:-}" ]]; then
    read -r -a env_args <<<"${RSYNC_APP_ARGS}"
    args+=("${env_args[@]}")
fi

if [[ "${#args[@]}" -eq 0 ]]; then
    echo "应用参数 / App args: <none>"
else
    printf '应用参数 / App args:'
    for arg in "${args[@]}"; do
        printf ' %q' "$arg"
    done
    printf '\n'
fi

exec "$binary" "${args[@]}"
