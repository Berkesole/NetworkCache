#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 29 \
  --listen-port 47029 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node29.data.tsv \
  --schedule-file /shared/oracle/node29.schedule.tsv \
  --log-file /shared/logs/node29.oracle.log
