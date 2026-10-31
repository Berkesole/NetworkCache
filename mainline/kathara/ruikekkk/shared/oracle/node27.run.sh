#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 27 \
  --listen-port 47027 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node27.data.tsv \
  --schedule-file /shared/oracle/node27.schedule.tsv \
  --log-file /shared/logs/node27.oracle.log
