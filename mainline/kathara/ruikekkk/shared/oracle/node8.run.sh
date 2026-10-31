#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 8 \
  --listen-port 47008 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node8.data.tsv \
  --schedule-file /shared/oracle/node8.schedule.tsv \
  --log-file /shared/logs/node8.oracle.log
