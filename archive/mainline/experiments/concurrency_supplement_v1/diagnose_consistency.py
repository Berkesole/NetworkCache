#!/usr/bin/env python3
"""Diagnose the 4.2 64 KiB tcp-direct inconsistency against the frozen baseline.

Checks the three things the specification names (image, lifetime, bin) plus the
measurement-segment structure, which this campaign deliberately changed: the frozen
slides-v5 formal slots were split into four measurement groups with pauses between
them, while the supplement runs one continuous measurement segment (spec section 2
and 5c).
"""
import csv
import hashlib
import json
from pathlib import Path

NEW = Path("/data/networkcache-concurrency-supplement-v1")
OLD = Path("/data/networkcache-concurrency-slides-v5")
new_campaign = json.loads((NEW / "campaign.json").read_text())
old_campaign = json.loads((OLD / "campaign.json").read_text())

print("image: new", new_campaign["image_id"])
print("image: old", old_campaign["image_id"])
print("same image:", new_campaign["image_id"] == old_campaign["image_id"])

# sources that must be identical to the parent (everything this branch did not touch)
changed = []
for name, digest in old_campaign["sources"].items():
    new_path = NEW / "source" / name
    if new_path.is_file():
        if hashlib.sha256(new_path.read_bytes()).hexdigest() != digest:
            changed.append(name)
print("source files differing from the frozen parent:", len(changed))
for name in changed:
    print("   ", name)

for label, root in (("baseline", OLD / "derived/formal"), ("new", NEW / "derived/supplement")):
    slot = ("zipf-n32-b65536-c512-iid-zipf-poisson-r128-tcp-direct--s001-r1" if label == "baseline"
            else "size-n32-b65536-c512-iid-zipf-poisson-r128-tcp-direct--s001-r1")
    attempt = root.parent / ("formal" if label == "baseline" else "supplement") / slot / "attempt001"
    if label == "baseline":
        attempt = OLD / "derived" / "formal" / slot / "attempt001"
    else:
        attempt = NEW / "derived" / "supplement" / slot / "attempt001"
    if not (attempt / "summary.json").is_file():
        print(label, "missing", attempt)
        continue
    s = json.loads((attempt / "summary.json").read_text())
    cfg = s["config"]
    rows = list(csv.DictReader((attempt / "batch_timeline.csv").open()))
    bins = sorted({int(r["batch"]) for r in rows})
    sizes = sorted({int(r["planned"]) for r in rows})
    print(f"{label}: lifetime={cfg.get('query_lifetime_seconds')} "
          f"measurement_seconds={round(s.get('measurement_seconds', -1), 3)} "
          f"planned={s.get('planned')} bins={len(bins)} native_bin_planned={sizes} "
          f"mean_ms={round(s['total_latency_ms']['mean'], 3)} p95={round(s['total_latency_ms']['p95'], 3)}")
    phases = sorted({r["phase"].split(":")[0] for r in rows})
    print(f"{label}: timeline phases={phases}")

# how many measurement groups does each raw attempt carry?
for label, root in (("baseline", OLD / "raw/formal"), ("new", NEW / "raw/supplement")):
    slot = ("zipf-n32-b65536-c512-iid-zipf-poisson-r128-tcp-direct--s001-r1" if label == "baseline"
            else "size-n32-b65536-c512-iid-zipf-poisson-r128-tcp-direct--s001-r1")
    attempt = root / slot / "attempt001"
    groups = sorted(p.name for p in attempt.glob("measurement-*"))
    print(f"{label}: measurement groups = {groups}")