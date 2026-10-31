#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 26 \
  --listen-port 47026 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node26.data.tsv \
  --schedule-file /shared/oracle/node26.schedule.tsv \
  --log-file /shared/logs/node26.oracle.log
