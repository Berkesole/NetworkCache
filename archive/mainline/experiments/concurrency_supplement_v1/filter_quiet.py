#!/usr/bin/env python3
"""Reduce an extracted delivery to exactly the slots that were re-measured quietly.

The extractor always emits the whole stage; the `-quiet` deliverable must contain only
the re-run slots, so its host CPU peak distribution is the quiet one and the local
acceptance check (median <60%, none >=90%) can be applied to the file as a whole.

Usage: filter_quiet.py <dir> --match <substring> [--match <substring> ...]
"""
import argparse
import csv
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("dir")
ap.add_argument("--match", action="append", required=True)
args = ap.parse_args()
d = Path(args.dir)
patterns = args.match


def keep(row):
    return any(p in row.get("slot_id", "") for p in patterns)


slots_path = d / "slides_v5_slot_metrics.csv"
rows = list(csv.DictReader(open(slots_path)))
kept = [r for r in rows if keep(r)]
ids = {r["slot_id"] for r in kept}
print(f"{d.name}: kept {len(kept)} of {len(rows)} slot rows for {len(patterns)} pattern(s)")
with slots_path.open("w", newline="") as h:
    w = csv.DictWriter(h, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(kept)

batch_path = d / "slides_v5_batch_latency.csv"
brows = list(csv.DictReader(open(batch_path)))
bkept = [r for r in brows if any(r.get("source", "").startswith(i) for i in ids)]
print(f"{d.name}: kept {len(bkept)} of {len(brows)} batch rows")
with batch_path.open("w", newline="") as h:
    w = csv.DictWriter(h, fieldnames=list(brows[0].keys()))
    w.writeheader()
    w.writerows(bkept)

prov_path = d / "PROVENANCE.json"
p = json.loads(prov_path.read_text())
p["slots"] = [s for s in p.get("slots", []) if s.get("slot") in ids]
p["run_status"] = {k: v for k, v in (p.get("run_status") or {}).items() if k in ids}
peaks = sorted(float(r["host_cpu_peak_percent"]) for r in kept)
sips = sorted({r["slots_in_parallel"] for r in kept})
p["scope"] = (
    "quiet targeted rework of the blocks named in the request: "
    + ", ".join(patterns)
    + f" ({len(kept)} slots), re-measured with slots_in_parallel=1 so the host CPU peaks "
      f"sit in the quiet band (min {peaks[0]:.1f}%, median {peaks[len(peaks)//2]:.1f}%, "
      f"max {peaks[-1]:.1f}%, {sum(1 for v in peaks if v >= 90)} slots >=90%). All other "
      "blocks keep their campaign rows in the main delivery file; nothing in that file was "
      "changed.")
prov_path.write_text(json.dumps(p, indent=2, sort_keys=True) + "\n")
print(f"  peaks min={peaks[0]:.1f} median={peaks[len(peaks)//2]:.1f} max={peaks[-1]:.1f} "
      f">=90%={sum(1 for v in peaks if v >= 90)} | sip={sips}")
fed = [r for r in kept if r["family"] == "federation"]
if fed:
    print(f"  federation slots={len(fed)} activation_ok={ {r['activation_ok'] for r in fed} } "
          f"warmup_gate_passed={ {r['warmup_gate_passed'] for r in fed} }")