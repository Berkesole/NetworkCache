#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 6 \
  --listen-port 47006 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node6.data.tsv \
  --schedule-file /shared/oracle/node6.schedule.tsv \
  --log-file /shared/logs/node6.oracle.log
