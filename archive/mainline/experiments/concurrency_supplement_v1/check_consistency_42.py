#!/usr/bin/env python3
"""Section 6.1: does the new 4.2 64 KiB arm agree with the existing zipf slots?

Baseline (volcengine-3, frozen): /data/networkcache-concurrency-slides-v5/derived/formal/
  zipf-n32-b65536-c512-iid-zipf-poisson-r128-{nc,tcp-direct}--s0NN-r{1,2}/attempt001/summary.json
  -> per seed the two repetitions share one arrival sequence, so they are averaged to a
     single per-seed value (10 independent values).

New arm: the supplement 4.2 64 KiB slots
  derived/supplement/size-n32-b65536-c512-iid-zipf-poisson-r128-{nc,tcp-direct}--s0NN-r1

Paired test: d_i = new_i - baseline_i for i = 1..10, 95% t CI with df = 9
(t = 2.262157); the arm agrees when the CI contains 0. Metrics: total_latency_ms.mean
and total_latency_ms.p95.

Usage: check_consistency_42.py [campaign_root] [baseline_root]
"""
import json
import math
import statistics
import sys
from pathlib import Path

T95_DF9 = 2.2621571627982044
METHODS = ("nc", "tcp-direct")
METRICS = (("mean", "total_latency_ms.mean"), ("p95", "total_latency_ms.p95"))


def load(path):
    return json.loads(Path(path).read_text())


def baseline_per_seed(root, method, metric):
    values = {}
    for seed in range(1, 11):
        reps = []
        for rep in (1, 2):
            p = (root / "formal" /
                 f"zipf-n32-b65536-c512-iid-zipf-poisson-r128-{method}--s{seed:03d}-r{rep}" /
                 "attempt001" / "summary.json")
            if not p.is_file():
                return None, f"missing baseline {p}"
            s = load(p)
            section, key = metric.split(".")
            value = (s.get(section) or {}).get(key)
            if not isinstance(value, (int, float)):
                return None, f"baseline {p} has no {metric}"
            reps.append(float(value))
        values[seed] = statistics.mean(reps)
    return values, None


def new_per_seed(root, method, metric):
    values = {}
    for seed in range(1, 11):
        p = (root / "derived" / "supplement" /
             f"size-n32-b65536-c512-iid-zipf-poisson-r128-{method}--s{seed:03d}-r1" /
             "attempt001" / "summary.json")
        if not p.is_file():
            return None, f"new arm not finished: seed {seed} {method}"
        s = load(p)
        section, key = metric.split(".")
        value = (s.get(section) or {}).get(key)
        if not isinstance(value, (int, float)):
            return None, f"new {p} has no {metric}"
        values[seed] = float(value)
    return values, None


def paired_ci(diffs):
    n = len(diffs)
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs)
    se = sd / math.sqrt(n)
    half = T95_DF9 * se
    return dict(n=n, mean=mean, stdev=sd, se=se, ci_low=mean - half, ci_high=mean + half,
                contains_zero=mean - half <= 0 <= mean + half)


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1
                else "/data/networkcache-concurrency-supplement-v1")
    baseline = Path(sys.argv[2] if len(sys.argv) > 2
                    else "/data/networkcache-concurrency-slides-v5/derived")
    report, ok = {}, True
    for method in METHODS:
        for label, metric in METRICS:
            base, err = baseline_per_seed(baseline, method, metric)
            if err:
                print("PENDING", method, label, err)
                ok = False
                continue
            new, err = new_per_seed(root, method, metric)
            if err:
                print("PENDING", method, label, err)
                ok = False
                continue
            diffs = [new[s] - base[s] for s in range(1, 11)]
            stats = paired_ci(diffs)
            report[f"{method}:{label}"] = dict(
                baseline_mean=statistics.mean(base.values()),
                new_mean=statistics.mean(new.values()), **stats)
            verdict = "AGREES" if stats["contains_zero"] else "DIFFERS"
            if not stats["contains_zero"]:
                ok = False
            print(f"{method:11s} {label:5s} baseline={statistics.mean(base.values()):9.4f} "
                  f"new={statistics.mean(new.values()):9.4f} "
                  f"mean_diff={stats['mean']:8.4f} 95%CI=[{stats['ci_low']:.4f},{stats['ci_high']:.4f}]"
                  f" -> {verdict}")
    out = Path("/tmp/consistency_42.json")
    out.write_text(json.dumps(dict(agrees=ok, tests=report), indent=2, sort_keys=True) + "\n")
    print("ALL_AGREE" if ok else "NOT_SETTLED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())