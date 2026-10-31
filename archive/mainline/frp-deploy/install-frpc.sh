#!/usr/bin/env bash
# 在本机（运行 dsh web 的机器）安装并启动 frpc
# 需要 root 权限，且会写入 /opt/frp、/etc/frp、/etc/systemd/system
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FRP_DIR=/opt/frp
CONF_DIR=/etc/frp
BIN_SRC="$HERE/pkg/frp_0.71.0_linux_amd64/frpc"

[[ $(id -u) -eq 0 ]] || { echo "请用 root 运行"; exit 1; }
[[ -x "$BIN_SRC" ]] || { echo "找不到 frpc 二进制: $BIN_SRC"; exit 1; }
[[ -f "$HERE/build/frpc.toml" ]] || { echo "请先运行 ./render.sh"; exit 1; }

echo "== 1. 目录 =="
mkdir -p "$FRP_DIR" "$CONF_DIR"
chmod 755 "$FRP_DIR"; chmod 750 "$CONF_DIR"

echo "== 2. 安装二进制与配置 =="
install -m 0755 "$BIN_SRC" "$FRP_DIR/frpc"
install -m 0600 "$HERE/build/frpc.toml" "$CONF_DIR/frpc.toml"
"$FRP_DIR/frpc" --version

echo "== 3. 校验配置 =="
"$FRP_DIR/frpc" verify -c "$CONF_DIR/frpc.toml"

echo "== 4. systemd =="
install -m 0644 "$HERE/frpc.service" /etc/systemd/system/frpc.service
# 本机 systemd 若异常，daemon-reload 可能很慢，故加超时保护
timeout 180 systemctl daemon-reload || echo "WARN: daemon-reload 超时"
timeout 60 systemctl enable frpc || echo "WARN: enable 失败"
timeout 60 systemctl restart frpc || echo "WARN: restart 失败"
sleep 3
timeout 20 systemctl is-active frpc || true
echo "--- 最近日志 ---"
journalctl -u frpc -n 15 --no-pager 2>/dev/null || tail -n 15 /var/log/frpc.log

echo "== 5. 本地探活 =="
pgrep -a frpc || echo "frpc 进程不存在"
echo "== 完成 =="