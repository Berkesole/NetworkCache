#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 22 \
  --listen-port 47022 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node22.data.tsv \
  --schedule-file /shared/oracle/node22.schedule.tsv \
  --log-file /shared/logs/node22.oracle.log
