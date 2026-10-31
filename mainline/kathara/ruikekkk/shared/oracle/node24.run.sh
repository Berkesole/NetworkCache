#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 24 \
  --listen-port 47024 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node24.data.tsv \
  --schedule-file /shared/oracle/node24.schedule.tsv \
  --log-file /shared/logs/node24.oracle.log
