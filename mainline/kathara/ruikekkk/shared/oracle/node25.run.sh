#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 25 \
  --listen-port 47025 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node25.data.tsv \
  --schedule-file /shared/oracle/node25.schedule.tsv \
  --log-file /shared/logs/node25.oracle.log
