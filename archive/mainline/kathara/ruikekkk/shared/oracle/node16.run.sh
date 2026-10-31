#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 16 \
  --listen-port 47016 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node16.data.tsv \
  --schedule-file /shared/oracle/node16.schedule.tsv \
  --log-file /shared/logs/node16.oracle.log
