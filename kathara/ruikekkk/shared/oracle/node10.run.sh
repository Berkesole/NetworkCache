#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 10 \
  --listen-port 47010 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node10.data.tsv \
  --schedule-file /shared/oracle/node10.schedule.tsv \
  --log-file /shared/logs/node10.oracle.log
