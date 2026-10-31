#!/usr/bin/env bash
echo "== process =="
pgrep -a frps || echo "no frps process"
echo "== listen 7000/7500 =="
timeout 5 ss -H -lntp 2>/dev/null | grep -E ':(7000|7500)' || echo "not listening"
echo "== systemctl (each with timeout) =="
timeout 10 systemctl is-enabled frps 2>&1; echo "is-enabled rc=$?"
timeout 10 systemctl is-active frps 2>&1; echo "is-active rc=$?"
echo "== pid1 =="
ps -p 1 -o comm= 2>/dev/null
echo "== journal tail =="
timeout 10 journalctl -u frps -n 15 --no-pager 2>&1 | tail -20
echo "== log file =="
tail -n 15 /var/log/frps.log 2>/dev/null || echo "no log file"
echo "== done =="