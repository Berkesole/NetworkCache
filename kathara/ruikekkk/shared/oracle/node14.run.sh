#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 14 \
  --listen-port 47014 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node14.data.tsv \
  --schedule-file /shared/oracle/node14.schedule.tsv \
  --log-file /shared/logs/node14.oracle.log
