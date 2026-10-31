#!/usr/bin/env python3
"""Section 6 acceptance summary over the finished supplement campaign.

Reports per family/method: qualified count, the isolation_expiry distribution (the
spec expects 2 for federation nc and 0 for tcp-direct, and 0 elsewhere - recorded,
never a pass condition), host_cpu_peak envelope and the host_limited marker
(>= 90%), plus warm-up gate outcomes for 4.4.
"""
import collections
import glob
import json
from pathlib import Path

N = Path("/data/networkcache-concurrency-supplement-v1")
recs = [json.loads(open(p).read()) for p in
        sorted(glob.glob(str(N / "derived/supplement-progress/*.json")))]
print("records:", len(recs), "qualified:", sum(1 for d in recs if d.get("qualified")))

by = collections.defaultdict(lambda: collections.Counter())
iso = collections.defaultdict(lambda: collections.Counter())
for d in recs:
    slot = d["slot_id"]
    family = slot.split("-")[0]
    method = "tcp-direct" if "tcp-direct" in slot else "nc"
    gate = d.get("gate") or d
    by[(family, method)]["qualified" if d.get("qualified") else "not"] += 1
    iso[(family, method)][gate.get("isolation_expiry")] += 1
print()
print("qualified / isolation_expiry distribution")
for key in sorted(by):
    print(f"  {key[0]:11s} {key[1]:11s} qualified={by[key]['qualified']:3d} "
          f"not={by[key]['not']:2d} isolation_expiry={dict(iso[key])}")

peaks = [d.get("host_cpu_peak_percent") for d in recs
         if isinstance(d.get("host_cpu_peak_percent"), (int, float))]
limited = [d["slot_id"] for d in recs if d.get("host_limited")]
print()
print(f"host_cpu_peak_percent: n={len(peaks)} "
      f"min={min(peaks) if peaks else None} max={max(peaks) if peaks else None}")
print(f"host_limited slots (>=90%): {len(limited)}")
for slot in limited[:5]:
    print("   ", slot[:60])

fed = [d for d in recs if d["slot_id"].startswith("federation")]
print()
print(f"4.4 federation slots: {len(fed)}")
for d in fed:
    att = d.get("attempt")
    timing = {}
    if att and (Path(att) / "result.json").is_file():
        timing = (json.loads((Path(att) / "result.json").read_text()) or {}).get("timing") or {}
    print(f"   {d['slot_id'][-22:]} qualified={d.get('qualified')} "
          f"activation_ok={timing.get('activation_ok')} "
          f"warmup_gate_passed={timing.get('warmup_gate_passed')} "
          f"boundary_s={round(timing.get('boundary_seconds', -1), 1)}")