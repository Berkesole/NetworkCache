#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 31 \
  --listen-port 47031 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node31.data.tsv \
  --schedule-file /shared/oracle/node31.schedule.tsv \
  --log-file /shared/logs/node31.oracle.log
