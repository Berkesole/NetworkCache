#!/usr/bin/env python3
"""Section 6 consistency checks for the dual-track supplement campaign.

Check 1 - machine/image anchor: Track A's 4.2b arm (size n32 b65536 r128) against the
          frozen volcengine-3 baseline (zipf-n32-b65536-c512-iid-zipf-poisson-r128-*),
          per seed the two frozen repetitions are averaged first.
Check 2 - cross-machine reproduction: Track A vs Track B, same block, same seed.
Check 3 - closed loop vs open arrival: reported separately (see the campaign notes);
          the closed-loop source is not co-located with these runs.

All tests are the specification's: paired per-seed difference, 95% t CI with df = 9
(t = 2.262157); a CI containing 0 means "consistent".

Usage:
  check_consistency_dual.py --a <slot_metrics A.csv> [--b <slot_metrics B.csv>]
      [--baseline-root /data/networkcache-concurrency-slides-v5/derived]
      [--out <json>]
"""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path

# two-sided 95% t quantiles by degrees of freedom; a paired test over n seeds has df = n-1
T95 = {1: 12.7062, 2: 4.3027, 3: 3.1824, 4: 2.7764, 5: 2.5706, 6: 2.4469, 7: 2.3646,
       8: 2.3060, 9: 2.2622, 10: 2.2281, 11: 2.2010, 12: 2.1788, 13: 2.1604, 14: 2.1448,
       15: 2.1314, 16: 2.1199, 17: 2.1098, 18: 2.1009, 19: 2.0930, 20: 2.0860}
METRICS = (("total_mean_ms", "mean"), ("total_p95_ms", "p95"))


def block_of(row):
    fam, size, rate = row["family"], row["size"], row["rate"]
    life = row.get("query_lifetime_seconds", "30")
    if fam == "federation":
        return "4.4"
    if fam in ("zipf", "uniform"):
        return "4.1-opt"
    if fam == "controlled":
        return "4.1"
    if fam == "size":
        if rate == "32":
            return "4.2a"
        return "E" if life == "7" else "4.2b"
    return fam


def load(path):
    rows = list(csv.DictReader(open(path)))
    out = {}
    for r in rows:
        key = (block_of(r), r["method"], int(r["seed"]))
        out[key] = r
    return out, len(rows)


def paired(diffs):
    n = len(diffs)
    if n < 3:
        return None
    m, sd = statistics.mean(diffs), statistics.stdev(diffs)
    crit = T95.get(n - 1, 2.0452)
    h = crit * sd / math.sqrt(n)
    return dict(n=n, df=n - 1, t_crit=crit, mean_diff=m, ci_low=m - h, ci_high=m + h,
                contains_zero=m - h <= 0 <= m + h)


def baseline_seed(root, method, seed, metric):
    vals = []
    for rep in (1, 2):
        p = (Path(root) / "formal" /
             f"zipf-n32-b65536-c512-iid-zipf-poisson-r128-{method}--s{seed:03d}-r{rep}" /
             "attempt001/summary.json")
        if not p.is_file():
            return None
        vals.append(json.loads(p.read_text())["total_latency_ms"][metric])
    return statistics.mean(vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b")
    ap.add_argument("--baseline-root")
    ap.add_argument("--out", default="/tmp/consistency_dual.json")
    args = ap.parse_args()
    A, na = load(args.a)
    B, nb = (load(args.b) if args.b else ({}, 0))
    report = dict(a_rows=na, b_rows=nb, check1={}, check2={}, notes=[])
    print(f"track A rows={na} track B rows={nb}")

    if args.baseline_root:
        print("\ncheck 1 - Track A 4.2b vs the frozen volcengine-3 baseline")
        for method in ("nc", "tcp-direct"):
            for col, label in METRICS:
                diffs = []
                for seed in range(1, 11):
                    row = A.get(("4.2b", method, seed))
                    base = baseline_seed(args.baseline_root, method, seed, label)
                    if row is None or base is None:
                        continue
                    diffs.append(float(row[col]) - base)
                stats = paired(diffs)
                if stats is None:
                    report["notes"].append(f"check1 {method}:{label} pending (n={len(diffs)})")
                    print(f"  {method:11s} {label:5s} PENDING n={len(diffs)}/10")
                    continue
                report["check1"][f"{method}:{label}"] = stats
                verdict = "AGREES" if stats["contains_zero"] else "DIFFERS"
                print(f"  {method:11s} {label:5s} n={stats['n']:2d} mean_diff={stats['mean_diff']:+8.3f} "
                      f"95%CI=[{stats['ci_low']:7.3f},{stats['ci_high']:7.3f}] {verdict}")

    if B:
        # The specification requires a same-load comparison when the two machines are not
        # both in the quiet band: pair only the seeds whose host CPU peaks fall in the
        # same band, and report the unrestricted result next to it.
        def peak(row):
            try:
                return float(row.get("host_cpu_peak_percent"))
            except (TypeError, ValueError):
                return None

        bands = (("all", lambda a, b: True),
                 ("both<60", lambda a, b: a is not None and b is not None and a < 60 and b < 60),
                 ("both>=60", lambda a, b: a is not None and b is not None and a >= 60 and b >= 60))
        print("\ncheck 2 - cross-machine reproduction (A vs B, same block and seed), "
              "stratified by host CPU peak band")
        blocks = sorted({k[0] for k in A} | {k[0] for k in B})
        for block in blocks:
            for method in ("nc", "tcp-direct"):
                for col, label in METRICS:
                    for band_name, keep in bands:
                        diffs = []
                        for seed in range(1, 11):
                            ra, rb = A.get((block, method, seed)), B.get((block, method, seed))
                            if ra is None or rb is None:
                                continue
                            if not keep(peak(ra), peak(rb)):
                                continue
                            diffs.append(float(ra[col]) - float(rb[col]))
                        stats = paired(diffs)
                        key = f"{block}:{method}:{label}:{band_name}"
                        if stats is None:
                            report["notes"].append(f"check2 {key} pending (n={len(diffs)})")
                            if band_name == "all":
                                print(f"  {block:8s} {method:11s} {label:5s} PENDING n={len(diffs)}/10")
                            continue
                        report["check2"][key] = stats
                        verdict = "AGREES" if stats["contains_zero"] else "DIFFERS"
                        print(f"  {block:8s} {method:11s} {label:5s} {band_name:8s} n={stats['n']:2d} "
                              f"mean_diff={stats['mean_diff']:+8.3f} "
                              f"95%CI=[{stats['ci_low']:7.3f},{stats['ci_high']:7.3f}] {verdict}")

    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("\nwrote", args.out)


if __name__ == "__main__":
    main()