#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 1 \
  --listen-port 47001 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node1.data.tsv \
  --schedule-file /shared/oracle/node1.schedule.tsv \
  --log-file /shared/logs/node1.oracle.log
