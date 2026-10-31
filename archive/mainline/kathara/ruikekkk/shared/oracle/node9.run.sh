#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 9 \
  --listen-port 47009 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node9.data.tsv \
  --schedule-file /shared/oracle/node9.schedule.tsv \
  --log-file /shared/logs/node9.oracle.log
