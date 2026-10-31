#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 12 \
  --listen-port 47012 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node12.data.tsv \
  --schedule-file /shared/oracle/node12.schedule.tsv \
  --log-file /shared/logs/node12.oracle.log
