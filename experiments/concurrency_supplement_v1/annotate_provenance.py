#!/usr/bin/env python3
"""Add the section 6.1 consistency verdicts and the host-pressure evidence to PROVENANCE.

The tcp-direct 64 KiB arm disagrees with the frozen baseline while nc agrees, so the
verdict, the paired CIs and the host CPU peaks of new versus baseline slots are
recorded in the delivered PROVENANCE.json - a reader must not be able to miss it.
"""
import csv, json, math, statistics
from pathlib import Path

N = Path("/data/networkcache-concurrency-supplement-v1")
O = Path("/data/networkcache-concurrency-slides-v5/derived")
prov = N / "delivery/PROVENANCE.json"
data = json.loads(prov.read_text())
T = 2.2621571627982044
new = {}
for r in csv.DictReader(open(N / "delivery/slides_v5_slot_metrics.csv")):
    if r["family"] == "size" and r["size"] == "65536":
        new[(r["method"], int(r["seed"]))] = dict(mean=float(r["total_mean_ms"]),
                                                  p95=float(r["total_p95_ms"]),
                                                  peak=float(r["host_cpu_peak_percent"]),
                                                  limited=r["host_limited"] == "true")
base_slots = {}
for method in ("nc", "tcp-direct"):
    for seed in range(1, 11):
        vals = []
        for rep in (1, 2):
            p = O / "formal" / f"zipf-n32-b65536-c512-iid-zipf-poisson-r128-{method}--s{seed:03d}-r{rep}" / "attempt001" / "summary.json"
            s = json.loads(p.read_text())
            vals.append((s["total_latency_ms"]["mean"], s["total_latency_ms"]["p95"]))
        base_slots[(method, seed)] = (statistics.mean(v[0] for v in vals),
                                      statistics.mean(v[1] for v in vals))
tests, peaks = {}, {}
for method in ("nc", "tcp-direct"):
    for idx, label in ((0, "mean"), (1, "p95")):
        diffs = [new[(method, s)][label] - base_slots[(method, s)][idx] for s in range(1, 11)]
        m, sd = statistics.mean(diffs), statistics.stdev(diffs)
        h = T * sd / math.sqrt(len(diffs))
        tests[f"{method}:{label}"] = dict(baseline_mean=statistics.mean(base_slots[(method, s)][idx] for s in range(1, 11)),
                                          new_mean=statistics.mean(new[(method, s)][label] for s in range(1, 11)),
                                          mean_diff=m, ci_low=m - h, ci_high=m + h, df=9,
                                          contains_zero=m - h <= 0 <= m + h,
                                          verdict="AGREES" if m - h <= 0 <= m + h else "DIFFERS")
        peaks[method] = dict(new_peak_min=min(new[(method, s)]["peak"] for s in range(1, 11)),
                             new_peak_max=max(new[(method, s)]["peak"] for s in range(1, 11)),
                             new_host_limited=sum(1 for s in range(1, 11) if new[(method, s)]["limited"]),
                             baseline_peak_min=27.2, baseline_peak_max=40.1,
                             baseline_host_limited=0)
data["consistency_check_42"] = dict(
    method="paired per-seed difference, 95% t CI (df=9, t=2.262157); baseline = mean of the two "
           "frozen repetitions per seed; metrics total_latency_ms.mean and .p95",
    baseline_path="/data/networkcache-concurrency-slides-v5/derived/formal/"
                  "zipf-n32-b65536-c512-iid-zipf-poisson-r128-{nc,tcp-direct}--s0NN-r{1,2}/attempt001/summary.json",
    tests=tests,
    host_pressure=peaks,
    verdict="INCONSISTENT_TCP_DIRECT",
    interpretation="image id, per-slot query lifetime (30 s), delivery bins (32 x 128) and the "
                   "single continuous measurement segment (measurement-1, ~32.49 s) all match the "
                   "baseline, and only the nine intentionally edited source files differ. The "
                   "tcp-direct arm is however +2.1% mean / +3.0% p95 while nc agrees, and the new "
                   "slots ran at host CPU peaks of 37-99% against 27-40% for every baseline slot "
                   "(the supplement ran 3-4 concurrent 32-node labs, the baseline campaign did not "
                   "saturate the host). The difference survives excluding the host-limited seed, so "
                   "it is reported rather than explained away; per the specification an inconsistent "
                   "equivalence check is a stop-and-report condition.")
prov.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
print("annotated", prov)
for key, t in sorted(tests.items()):
    print(f"  {key:16s} {t['verdict']:8s} diff={t['mean_diff']:7.4f} CI=[{t['ci_low']:.3f},{t['ci_high']:.3f}]")
