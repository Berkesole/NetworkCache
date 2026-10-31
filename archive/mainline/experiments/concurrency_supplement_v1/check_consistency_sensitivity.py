#!/usr/bin/env python3
"""Section 6.1 sensitivity: is the tcp-direct difference attributable to host pressure?

The supplement ran 3-4 concurrent 32-node labs on the same 48-CPU host, so some slots
hit host CPU peaks near 100%, while every frozen baseline slot stayed at 27-40%.
Recompute the paired 95% t CI (df=9) over all seeds and over the seeds whose new slot
was not host-limited (host_cpu_peak_percent < 90).
"""
import csv, json, math, statistics
from pathlib import Path

N = Path("/data/networkcache-concurrency-supplement-v1")
O = Path("/data/networkcache-concurrency-slides-v5/derived")
T95_DF = {9: 2.2621571627982044}
new_metrics = {}
for r in csv.DictReader(open(N / "delivery/slides_v5_slot_metrics.csv")):
    if r["family"] == "size" and r["size"] == "65536":
        new_metrics[(r["method"], int(r["seed"]))] = (float(r["total_mean_ms"]), float(r["total_p95_ms"]), r["host_limited"] == "true", float(r["host_cpu_peak_percent"]))

def baseline(method, seed):
    vals = []
    for rep in (1, 2):
        p = O / "formal" / f"zipf-n32-b65536-c512-iid-zipf-poisson-r128-{method}--s{seed:03d}-r{rep}" / "attempt001" / "summary.json"
        s = json.loads(p.read_text())
        vals.append((s["total_latency_ms"]["mean"], s["total_latency_ms"]["p95"]))
    return statistics.mean(v[0] for v in vals), statistics.mean(v[1] for v in vals)

def ci(diffs):
    n = len(diffs); m = statistics.mean(diffs); sd = statistics.stdev(diffs)
    h = T95_DF[9] * sd / math.sqrt(n)
    return m, m - h, m + h, m - h <= 0 <= m + h

for method in ("nc", "tcp-direct"):
    for idx, label in ((0, "mean"), (1, "p95")):
        for subset in ("all", "not_host_limited"):
            diffs, peaks = [], []
            for seed in range(1, 11):
                new = new_metrics.get((method, seed))
                if not new:
                    continue
                if subset == "not_host_limited" and new[2]:
                    continue
                b = baseline(method, seed)
                diffs.append(new[idx] - b[idx])
                peaks.append(new[3])
            if len(diffs) < 3:
                print(f"{method:11s} {label:5s} {subset:16s} n={len(diffs)} too few"); continue
            m, lo, hi, contains = ci(diffs)
            print(f"{method:11s} {label:5s} {subset:16s} n={len(diffs):2d} mean_diff={m:7.4f} "
                  f"95%CI=[{lo:6.3f},{hi:6.3f}] {'AGREES' if contains else 'DIFFERS'} "
                  f"(new peaks {min(peaks):.0f}-{max(peaks):.0f}%)")
