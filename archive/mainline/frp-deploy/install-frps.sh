#!/usr/bin/env bash
# 在远端 (volcengine-3 / 124.174.41.239) 安装并启动 frps
# 用法: ssh volcengine-3 'bash -s' < install-frps.sh
set -euo pipefail

FRP_DIR=/opt/frp
CONF_DIR=/etc/frp

echo "== 1. 目录 =="
mkdir -p "$FRP_DIR" "$CONF_DIR"
chmod 755 "$FRP_DIR"; chmod 750 "$CONF_DIR"

echo "== 2. 安装二进制 =="
install -m 0755 /tmp/frp-upload/frps "$FRP_DIR/frps"
install -m 0644 /tmp/frp-upload/frps.toml "$CONF_DIR/frps.toml"
chmod 600 "$CONF_DIR/frps.toml"
"$FRP_DIR/frps" --version

echo "== 3. 校验配置 =="
"$FRP_DIR/frps" verify -c "$CONF_DIR/frps.toml"

echo "== 4. systemd =="
install -m 0644 /tmp/frp-upload/frps.service /etc/systemd/system/frps.service
systemctl daemon-reload
systemctl enable --now frps
sleep 2
systemctl is-active frps
systemctl --no-pager --lines=5 status frps || true

echo "== 5. 监听端口 =="
timeout 5 ss -H -lntp | grep -E ':(7000|7500)' || echo "未看到 7000/7500 监听！"

echo "== 6. 清理上传目录 =="
rm -rf /tmp/frp-upload
echo "== frps 安装完成 =="