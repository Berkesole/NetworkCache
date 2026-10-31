#!/usr/bin/env bash
# 在远端（volcengine-3）执行的环境探测脚本
set -u
echo "== whoami/arch/os =="
whoami; uname -m; head -2 /etc/os-release
echo "== tools =="
for t in curl wget tar openssl systemctl iptables nft ufw; do
  printf '%-10s %s\n' "$t" "$(command -v "$t" || echo MISSING)"
done
echo "== listen =="
timeout 5 ss -H -lnt || true
echo "== existing frp =="
ls -d /opt/frp /usr/local/frp /etc/frp 2>/dev/null || echo "none"
systemctl list-unit-files 'frp*' 2>/dev/null || true
echo "== firewall state =="
timeout 5 ufw status 2>/dev/null || true
timeout 5 nft list ruleset 2>/dev/null | head -20 || true
echo "== github reachability =="
timeout 12 curl -sS -o /dev/null -w 'github=%{http_code}\n' --max-time 10 https://github.com/ || echo "github unreachable"
timeout 12 curl -sSL -o /dev/null -w 'release=%{http_code} size=%{size_download} speed=%{speed_download}\n' --max-time 10 \
  -r 0-200000 https://github.com/fatedier/frp/releases/download/v0.71.0/frp_0.71.0_linux_amd64.tar.gz || echo "release download failed"
echo "== done =="