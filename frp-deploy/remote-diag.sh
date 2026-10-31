#!/usr/bin/env bash
echo "== systemd state =="
timeout 10 systemctl is-system-running 2>&1; echo "rc=$?"
echo "== jobs =="
timeout 10 systemctl list-jobs --no-pager 2>&1 | head -20; echo "rc=$?"
echo "== failed units =="
timeout 10 systemctl --failed --no-pager 2>&1 | head -20; echo "rc=$?"
echo "== stuck clients/procs =="
ps -eo pid,ppid,stat,etime,comm,args | grep -E 'systemctl|frps|dbus' | grep -v grep | head -20
echo "== frps unit state =="
timeout 10 systemctl show frps -p ActiveState -p SubState -p ExecMainStatus -p Result 2>&1; echo "rc=$?"
echo "== dbus =="
timeout 10 busctl --version 2>&1 | head -2
echo "== done =="