#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 21 \
  --listen-port 47021 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node21.data.tsv \
  --schedule-file /shared/oracle/node21.schedule.tsv \
  --log-file /shared/logs/node21.oracle.log
