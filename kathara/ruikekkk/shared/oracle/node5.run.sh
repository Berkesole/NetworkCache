#!/bin/sh
set -eu
exec /app/build/oracle-direct-fetch \
  --node-id 5 \
  --listen-port 47005 \
  --ticks 7220 \
  --loop-sleep-ms 1 \
  --data-file /shared/oracle/node5.data.tsv \
  --schedule-file /shared/oracle/node5.schedule.tsv \
  --log-file /shared/logs/node5.oracle.log
