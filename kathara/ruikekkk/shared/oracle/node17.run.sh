#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 17 \
  --listen-port 47017 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node17.data.tsv \
  --schedule-file /shared/oracle/node17.schedule.tsv \
  --log-file /shared/logs/node17.oracle.log
