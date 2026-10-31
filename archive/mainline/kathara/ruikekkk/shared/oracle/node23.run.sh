#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 23 \
  --listen-port 47023 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node23.data.tsv \
  --schedule-file /shared/oracle/node23.schedule.tsv \
  --log-file /shared/logs/node23.oracle.log
