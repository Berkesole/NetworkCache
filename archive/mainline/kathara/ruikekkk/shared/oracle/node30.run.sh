#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 30 \
  --listen-port 47030 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node30.data.tsv \
  --schedule-file /shared/oracle/node30.schedule.tsv \
  --log-file /shared/logs/node30.oracle.log
