#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 4 \
  --listen-port 47004 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node4.data.tsv \
  --schedule-file /shared/oracle/node4.schedule.tsv \
  --log-file /shared/logs/node4.oracle.log
