#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 2 \
  --listen-port 47002 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node2.data.tsv \
  --schedule-file /shared/oracle/node2.schedule.tsv \
  --log-file /shared/logs/node2.oracle.log
