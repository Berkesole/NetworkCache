#!/usr/bin/env bash
echo "== df =="
timeout 10 df -h / /var/log /data 2>&1 | head -10
echo "== mount hang check (/data) =="
timeout 8 ls /data >/dev/null 2>&1; echo "ls /data rc=$?"
echo "== manual frps run (8s) =="
timeout 8 /opt/frp/frps -c /etc/frp/frps.toml 2>&1 | head -30
echo "manual_rc=${PIPESTATUS[0]}"
echo "== log file =="
ls -la /var/log/frps.log 2>&1
tail -n 20 /var/log/frps.log 2>&1
echo "== unit file =="
cat /etc/systemd/system/frps.service
echo "== done =="