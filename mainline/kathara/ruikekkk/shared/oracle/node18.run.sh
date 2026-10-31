#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 18 \
  --listen-port 47018 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node18.data.tsv \
  --schedule-file /shared/oracle/node18.schedule.tsv \
  --log-file /shared/logs/node18.oracle.log
