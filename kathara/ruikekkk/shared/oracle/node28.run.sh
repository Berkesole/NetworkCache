#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 28 \
  --listen-port 47028 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node28.data.tsv \
  --schedule-file /shared/oracle/node28.schedule.tsv \
  --log-file /shared/logs/node28.oracle.log
