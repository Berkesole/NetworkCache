#!/usr/bin/env bash
# Capacity-screening queue (resumable: slots that already have a result are skipped).: run the 24 frozen slots in the matrix order, serially,
# stopping on the first failure. One lab at a time.
set -uo pipefail

PR="${1:?pilot root required}"
ORDER_FILE="${2:?order file required}"
RUN="$PR/source/experiments/concurrency_finite_cache_pilot_v1/pilot_runner.py"
IMAGE="rsync-router:concurrency-finite-cache-smoke-81c22fe9"
i=0
total=$(grep -c . "$ORDER_FILE")
while read -r SLOT; do
    [ -n "$SLOT" ] || continue
    i=$((i + 1))
    if [ -f "$PR/raw/screening/$SLOT/attempt001/result.json" ]; then
        echo "=== [$i/$total] $SLOT (already done, skipped) ==="
        continue
    fi
    echo "=== [$i/$total] $SLOT ==="
    sudo -E env "PATH=$PR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        python3 "$RUN" --root "$PR" --inputs "$PR/inputs/single" --image "$IMAGE" \
        --matrix "$PR/plan/pilot_matrix.json" --plan-md "$PR/plan/CAPACITY_SCREENING_PLAN.md" \
        --owner-hashes "$PR/tools/owner-hashes-n32.json" --slot-id "$SLOT" \
        --phase screening --attempt 001 --execute > "/tmp/scr-$SLOT.log" 2>&1
    rc=$?
    echo "$SLOT rc=$rc"
    python3 - "$PR" "$SLOT" <<'PY'
import json, sys
pr, slot = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(f"{pr}/raw/screening/{slot}/attempt001/result.json"))
    m = d["metrics"]
    print(f"  status={d['status']} verdict={d['acceptance']['verdict']} "
          f"wall={d['total_wall_seconds']} completed={m['completed_measurement']} "
          f"failed={m['failed_measurement']} evicts={m['cache']['totals']['evicts']}")
    bad = {k: v["state"] for k, v in d["acceptance"]["gates"].items() if v["state"] != "pass"}
    if bad:
        print(f"  non_pass={bad}")
except Exception as exc:
    print(f"  no result: {exc}")
PY
    if [ "$rc" -ne 0 ]; then
        echo "QUEUE_STOPPED_AT $SLOT"
        exit 1
    fi
done < "$ORDER_FILE"
echo SCREENING_QUEUE_DONE