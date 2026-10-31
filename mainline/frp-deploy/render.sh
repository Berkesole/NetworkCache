#!/usr/bin/env bash
# 渲染 frps/frpc 配置到 build/
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f .frp_token ]]; then
  TOKEN="$(cat .frp_token)"
else
  TOKEN="$(openssl rand -hex 24)"
  printf '%s' "$TOKEN" > .frp_token
  chmod 600 .frp_token
fi
PW_FILE=.frp_panel_password
if [[ -f "$PW_FILE" ]]; then
  PANEL_PASSWORD="$(cat "$PW_FILE")"
else
  PANEL_PASSWORD="$(openssl rand -hex 12)"
  printf '%s' "$PANEL_PASSWORD" > "$PW_FILE"
  chmod 600 "$PW_FILE"
fi

mkdir -p build
sed -e "s/__TOKEN__/${TOKEN}/g" -e "s/__PANEL_PASSWORD__/${PANEL_PASSWORD}/g" \
  frps.toml.tmpl > build/frps.toml
sed -e "s/__TOKEN__/${TOKEN}/g" frpc.toml.tmpl > build/frpc.toml

chmod 600 build/frps.toml build/frpc.toml
echo "rendered: build/frps.toml build/frpc.toml"
grep -c '__' build/frps.toml build/frpc.toml | grep -v ':0' && echo "WARN: 仍有未替换占位符" || echo "占位符已全部替换"