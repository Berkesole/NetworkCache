#!/usr/bin/env python3
"""One-shot progress report for the supplement campaign (safe to run repeatedly)."""
import collections
import glob
import json
import os
import subprocess
import sys

N = "/data/networkcache-concurrency-supplement-v1"


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout.split()


recs = []
for path in sorted(glob.glob(N + "/derived/supplement-progress/*.json"), key=os.path.getmtime):
    try:
        recs.append(json.load(open(path)))
    except json.JSONDecodeError:
        continue
qualified = [d for d in recs if d.get("qualified")]
bad = [d for d in recs if not d.get("qualified")]
print(f"records={len(recs)} qualified={len(qualified)} bad={len(bad)} "
      f"workers={len(sh('pgrep', '-f', 'supplement_queue2.py --worker'))} "
      f"parents={len(sh('pgrep', '-f', 'supplement_queue2.py --execute'))} "
      f"containers={len(sh('docker', 'ps', '-q'))} "
      f"slots={len(glob.glob(N + '/raw/supplement/*'))}")

sys.path.insert(0, N + "/source/experiments/concurrency_slides_v5")
import matrix  # noqa: E402

done = {d["slot_id"] for d in qualified}
by = collections.defaultdict(lambda: [0, 0])
for c in matrix.slots("supplement"):
    key = c["family"] + ("-lt7s" if c["query_lifetime_seconds"] == 7 else "")
    by[key][1] += 1
    if c["id"] in done:
        by[key][0] += 1
print("per set:", " ".join(f"{k}={v[0]}/{v[1]}" for k, v in sorted(by.items())))
if bad:
    print("unqualified so far:")
    for d in bad[:5]:
        print(f"   {d['slot_id'][:56]} status={d.get('status')} reason={(d.get('reason') or '')[:40]}")