#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 3 \
  --listen-port 47003 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node3.data.tsv \
  --schedule-file /shared/oracle/node3.schedule.tsv \
  --log-file /shared/logs/node3.oracle.log
