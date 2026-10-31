#!/usr/bin/env python3
"""Reduce an extracted delivery to just the anchor (4.2b) slots.

The extractor always emits the whole stage; the anchor deliverable must contain only the
20 slots of the 4.2b arm so its peak distribution is the quiet one and the file is
self-describing.

Usage: filter_anchor.py <dir>            (rewrites the three files in place)
"""
import csv
import json
import sys
from pathlib import Path

d = Path(sys.argv[1])
is_anchor = lambda r: (r["family"] == "size" and r["size"] == "65536"
                       and r["rate"] == "128"
                       and r.get("query_lifetime_seconds") == "30")

slots_path = d / "slides_v5_slot_metrics.csv"
rows = list(csv.DictReader(open(slots_path)))
keep = [r for r in rows if is_anchor(r)]
ids = {r["slot_id"] for r in keep}
print(f"{d.name}: keeping {len(keep)} of {len(rows)} slot rows")
with slots_path.open("w", newline="") as h:
    w = csv.DictWriter(h, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(keep)

batch_path = d / "slides_v5_batch_latency.csv"
brows = list(csv.DictReader(open(batch_path)))
bkeep = [r for r in brows if any(r.get("source", "").startswith(i) for i in ids)]
print(f"{d.name}: keeping {len(bkeep)} of {len(brows)} batch rows")
with batch_path.open("w", newline="") as h:
    w = csv.DictWriter(h, fieldnames=list(brows[0].keys()))
    w.writeheader()
    w.writerows(bkeep)

prov_path = d / "PROVENANCE.json"
p = json.loads(prov_path.read_text())
p["slots"] = [s for s in p.get("slots", []) if s.get("slot") in ids]
p["run_status"] = {k: v for k, v in (p.get("run_status") or {}).items() if k in ids}
p["scope"] = ("section 6 check 1 anchor only: the 20 slots of the 4.2b arm "
              "(size-n32-b65536-c512-iid-zipf-poisson-r128-{nc,tcp-direct} x 10 seeds), "
              "re-measured with slots_in_parallel=1 so the host CPU peaks sit in the frozen "
              "baseline's band (19-55%, median ~34%, none >=90%). The campaign's own rows for "
              "these slots remain in the main delivery file.")
prov_path.write_text(json.dumps(p, indent=2, sort_keys=True) + "\n")
peaks = [float(r["host_cpu_peak_percent"]) for r in keep]
peaks.sort()
print(f"  {d.name}: anchor peaks min={peaks[0]:.1f} median={peaks[len(peaks)//2]:.1f} "
      f"max={peaks[-1]:.1f} >=90%={sum(1 for v in peaks if v >= 90)}")
print("  sip:", sorted({r["slots_in_parallel"] for r in keep}))