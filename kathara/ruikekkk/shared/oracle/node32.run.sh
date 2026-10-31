#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 32 \
  --listen-port 47032 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node32.data.tsv \
  --schedule-file /shared/oracle/node32.schedule.tsv \
  --log-file /shared/logs/node32.oracle.log
