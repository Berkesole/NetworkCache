#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 19 \
  --listen-port 47019 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node19.data.tsv \
  --schedule-file /shared/oracle/node19.schedule.tsv \
  --log-file /shared/logs/node19.oracle.log
