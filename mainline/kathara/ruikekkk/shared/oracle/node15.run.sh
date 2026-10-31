#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 15 \
  --listen-port 47015 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node15.data.tsv \
  --schedule-file /shared/oracle/node15.schedule.tsv \
  --log-file /shared/logs/node15.oracle.log
