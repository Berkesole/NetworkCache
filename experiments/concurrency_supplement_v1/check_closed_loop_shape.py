#!/usr/bin/env python3
"""Check 3 (closed loop vs open arrival) as a shape / layering comparison.

The specification's paired equality test does not apply here: the closed-loop lineage
runs one request in flight with a 100 ms think time and a 7 s query lifetime, while the
supplement is open Poisson with 30 s (the repository README states outright that
absolute values must not be compared across arrival modes, only shape and layering).
The two lineages also bin differently (closed loop: 8 pre + 64 post positions; the
supplement: 32 pre + 48 post bins), so each side is aggregated into quantile groups
within its own phase before the comparison.

Reported per direction: the pre-phase mean and its last-bin value, the first post bin,
the post-phase mean and last bin, plus a log-linear decay fit over the post groups. The
numbers are printed side by side for the closed-loop baseline and each new track; no
equality verdict is claimed.

Usage: check_closed_loop_shape.py --closed-loop <run_batch_means.csv> \
           [--new <slides_v5_batch_latency.csv> ...] [--groups 8] [--out <json>]
"""
import argparse
import collections
import csv
import json
import math
import statistics
from pathlib import Path


def quantile_groups(values, groups):
    """values: list of (position, latency); returns mean latency per quantile group."""
    ordered = sorted(values)
    if not ordered:
        return []
    out = []
    n = len(ordered)
    for g in range(groups):
        lo, hi = int(g * n / groups), int((g + 1) * n / groups)
        chunk = ordered[lo:hi]
        if chunk:
            out.append(statistics.mean(v for _p, v in chunk))
    return out


def log_decay(groups):
    """Least-squares slope of log(mean) over group index (negative = decay)."""
    xs = [i for i in range(len(groups))]
    ys = [math.log(v) for v in groups if v and v > 0]
    if len(ys) < 3:
        return None
    n = len(ys)
    mx, my = statistics.mean(xs[:n]), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs[:n])
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs[:n], ys))
    return sxy / sxx if sxx else None


def closed_loop_series(path):
    rows = list(csv.DictReader(open(path)))
    series = collections.defaultdict(list)
    for r in rows:
        series[(r["method"], r["direction"])].append((int(r["phase_position"]),
                                                      float(r["latency_ms"])))
    return series


import re


def new_series(path):
    """Normalise the two pre-phase conventions.

    The closed loop labels its pre phase -8..-1 with -1 closest to t0; the supplement
    numbers pre bins 1..N forward in time. Both are mapped to -N..-1 so that sorting is
    chronological on either side (post stays 1..M for both).
    """
    rows = [r for r in csv.DictReader(open(path)) if r["family"] == "federation"]
    pre_count = max((int(r["batch_index"]) for r in rows if r["phase"] == "pre"), default=0)
    series = collections.defaultdict(list)
    for r in rows:
        # federation rows carry the phase-local bin inside `source` (bin=N); the
        # batch_index column is the row's position in the whole phase-ordered table
        m = re.search(r"bin=(\d+)", r.get("source", ""))
        bin_index = int(m.group(1)) if m else int(r["batch_index"])
        clock = (bin_index - (pre_count + 1) if r["phase"] == "pre" else bin_index)
        series[(r["method"], r["direction"])].append((clock, float(r["mean_ms"])))
    return series


def summarize(series, groups):
    out = {}
    for key, values in series.items():
        pre = [v for v in values if v[0] < 0]
        post = [v for v in values if v[0] > 0]
        pre_groups = quantile_groups(pre, groups)
        post_groups = quantile_groups(post, groups)
        out[f"{key[0]}:{key[1]}"] = dict(
            pre_n=len(pre), post_n=len(post),
            pre_mean=round(statistics.mean(v for _p, v in pre), 3) if pre else None,
            # robust first/last: the mean of the extreme quantile group, not one row
            pre_last=round(pre_groups[-1], 3) if pre_groups else None,
            post_first=round(statistics.mean(v for _p, v in sorted(post)[:1]), 3) if post else None,
            post_mean=round(statistics.mean(v for _p, v in post), 3) if post else None,
            post_last=round(post_groups[-1], 3) if post_groups else None,
            pre_groups=[round(v, 3) for v in pre_groups],
            post_groups=[round(v, 3) for v in post_groups],
            post_log_decay_per_group=(round(log_decay(post_groups), 4)
                                      if log_decay(post_groups) is not None else None))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--closed-loop", required=True)
    ap.add_argument("--new", action="append", default=[],
                    help="label=path pairs, repeatable")
    ap.add_argument("--groups", type=int, default=8)
    ap.add_argument("--out", default="/tmp/closed_loop_shape.json")
    args = ap.parse_args()
    report = dict(units="ms; shape only, no equality test across arrival modes",
                  groups=args.groups,
                  closed_loop=summarize(closed_loop_series(args.closed_loop), args.groups))
    for item in args.new:
        label, path = item.split("=", 1)
        report[label] = summarize(new_series(path), args.groups)

    print(f"closed loop: {Path(args.closed_loop).name}")
    for label, data in report.items():
        if label in ("units", "groups", "closed_loop"):
            continue
        print(f"\n== {label} ==")
    keys = sorted(report["closed_loop"])
    header = f"{'series':26s} {'pre_mean':>9s} {'pre_last':>9s} {'post_first':>10s} {'post_mean':>9s} {'post_last':>9s} {'decay/g':>8s}"
    print("\n" + header)
    for name in ["closed_loop"] + [k for k in report
                                    if k not in ("units", "groups", "closed_loop")]:
        for key in keys:
            d = report.get(name, {}).get(key)
            if not d:
                continue
            print(f"{name[:12]+' '+key:26s} "
                  f"{str(d['pre_mean']):>9s} {str(d['pre_last']):>9s} "
                  f"{str(d['post_first']):>10s} {str(d['post_mean']):>9s} "
                  f"{str(d['post_last']):>9s} {str(d['post_log_decay_per_group']):>8s}")
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("\nwrote", args.out)


if __name__ == "__main__":
    main()