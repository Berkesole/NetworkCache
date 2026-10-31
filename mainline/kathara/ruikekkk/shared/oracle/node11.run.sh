#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 11 \
  --listen-port 47011 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node11.data.tsv \
  --schedule-file /shared/oracle/node11.schedule.tsv \
  --log-file /shared/logs/node11.oracle.log
