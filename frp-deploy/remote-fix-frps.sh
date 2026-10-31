#!/usr/bin/env bash
systemctl daemon-reload
echo "daemon-reload rc=$?"
timeout 30 systemctl start frps; echo "start rc=$?"
sleep 2
timeout 10 systemctl is-active frps; echo "is-active rc=$?"
echo "== status =="
timeout 15 systemctl status frps --no-pager -l 2>&1 | head -30
echo "== journal =="
timeout 15 journalctl -u frps -n 40 --no-pager 2>&1 | tail -40
echo "== manual run test =="
timeout 5 /opt/frp/frps -c /etc/frp/frps.toml 2>&1 | head -20
echo "manual rc=$?"
echo "== done =="