#!/usr/bin/env python3
"""Is the 4.2 tcp-direct equivalence gap explained by host CPU pressure?

For the 10 seeds of the 64 KiB arm: correlate the per-seed latency difference against
the frozen baseline with that seed's host CPU peak, and report the full baseline peak
distribution (the earlier comparison sampled only 10 of the 80 baseline slots).
"""
import csv
import glob
import json
import math
import statistics
from pathlib import Path

N = Path("/data/networkcache-concurrency-supplement-v1")
O = Path("/data/networkcache-concurrency-slides-v5")

rows = [r for r in csv.DictReader(open(N / "delivery/slides_v5_slot_metrics.csv"))
        if r["family"] == "size" and r["size"] == "65536"]


def baseline(method, seed):
    vals = []
    for rep in (1, 2):
        p = (O / "derived/formal" /
             f"zipf-n32-b65536-c512-iid-zipf-poisson-r128-{method}--s{seed:03d}-r{rep}" /
             "attempt001/summary.json")
        s = json.loads(p.read_text())
        vals.append(s["total_latency_ms"]["mean"])
    return statistics.mean(vals)


def pearson(xs, ys):
    n = len(xs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    syy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sxx == 0 or syy == 0:
        return None
    r = sxy / (sxx * syy)
    # two-sided p via t with df = n-2
    if abs(r) >= 1:
        return r, 0.0, n - 2
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, t, n - 2


print("per-seed 64 KiB arm (new tcp-direct vs baseline):")
for label, method in (("nc", "nc"), ("tcp-direct", "tcp-direct")):
    sel = sorted((r for r in rows if r["method"] == method), key=lambda r: int(r["seed"]))
    peaks = [float(r["host_cpu_peak_percent"]) for r in sel]
    diffs = [float(r["total_mean_ms"]) - baseline(method, int(r["seed"])) for r in sel]
    r, t, dof = pearson(peaks, diffs)
    print(f"  {label:11s} peaks={[round(p) for p in peaks]}")
    print(f"  {label:11s} diffs={[round(d, 3) for d in diffs]}")
    print(f"  {label:11s} mean_diff={statistics.mean(diffs):+.3f} sd={statistics.stdev(diffs):.3f} "
          f"pearson_r={r:.3f} t={t:.3f} df={dof}")

print()
print("baseline host CPU peaks over ALL 80 slots of the zipf-n32 64 KiB arm:")
for method in ("nc", "tcp-direct"):
    peaks = []
    for p in sorted(glob.glob(str(O / "derived/formal" /
                                  f"zipf-n32-b65536-c512-iid-zipf-poisson-r128-{method}--s*-r*" /
                                  "attempt001/health_and_mechanisms.json"))):
        res = (json.loads(Path(p).read_text()).get("resources") or {})
        v = res.get("host_cpu_peak_percent")
        if isinstance(v, (int, float)):
            peaks.append(v)
    if peaks:
        print(f"  {method:11s} n={len(peaks)} min={min(peaks):.1f} mean={statistics.mean(peaks):.1f} "
              f"max={max(peaks):.1f} >=90%: {sum(1 for p in peaks if p >= 90)}")

print()
print("new supplement slots overall (from the delivery):")
peaks = [float(r["host_cpu_peak_percent"]) for r in
         csv.DictReader(open(N / "delivery/slides_v5_slot_metrics.csv"))]
print(f"  n={len(peaks)} min={min(peaks):.1f} mean={statistics.mean(peaks):.1f} max={max(peaks):.1f} "
      f">=90%: {sum(1 for p in peaks if p >= 90)}")