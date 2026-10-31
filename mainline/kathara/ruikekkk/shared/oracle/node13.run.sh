#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 13 \
  --listen-port 47013 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node13.data.tsv \
  --schedule-file /shared/oracle/node13.schedule.tsv \
  --log-file /shared/logs/node13.oracle.log
