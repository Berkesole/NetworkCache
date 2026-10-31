#!/usr/bin/env python3
"""Report an arm's progress from raw result.json files (R4 arms do not write records).

Usage: arm_status.py <root> <stage>
"""
import glob
import json
import os
import statistics
import sys

root, stage = sys.argv[1], sys.argv[2]
rows = []
for rp in sorted(glob.glob(os.path.join(root, "raw", stage, "*", "attempt*", "result.json"))):
    r = json.load(open(rp))
    slot = rp.split(os.sep)[-3]
    peak = None
    for h in glob.glob(os.path.join(os.path.dirname(rp), "health_and_mechanisms.json")):
        peak = (json.load(open(h)).get("resources") or {}).get("host_cpu_peak_percent")
    rows.append((slot, r.get("status"), r.get("slots_in_parallel"), peak,
                 round(r.get("total_wall_seconds") or 0, 1)))
ok = [x for x in rows if x[1] == "ok"]
print(f"{stage}: results={len(rows)} ok={len(ok)}")
for x in sorted(rows):
    print(f"   {x[0][-26:]:28s} {str(x[1]):6s} sip={x[2]} peak={x[3]} wall={x[4]}s")
peaks = [x[3] for x in ok if isinstance(x[3], (int, float))]
if peaks:
    print(f"   peaks: min {min(peaks):.1f} median {statistics.median(peaks):.1f} "
          f"max {max(peaks):.1f} >=90%: {sum(1 for v in peaks if v >= 90)}")