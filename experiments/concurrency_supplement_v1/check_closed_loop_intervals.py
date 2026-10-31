#!/usr/bin/env python3
"""Check 3, interval half: closed-loop groups vs the supplement blocks, side by side.

The closed-loop lineage is one request in flight with a 100 ms think time; the
supplement is open Poisson. The repository README states that absolute values must not
be compared across arrival modes, so this reports the two sides next to each other
(group means with their 95% intervals) and never claims equality. Only same-condition
pairs are listed; the object-size arms are flagged because the closed-loop size sweep
used the controlled distribution while the supplement's 4.2a uses iid-zipf.

Usage: check_closed_loop_intervals.py --closed-loop <overall_group_ci95.csv>[...] \
           --new <label=slot_metrics.csv> ... [--out <json>]
"""
import argparse
import collections
import csv
import json
import math
import statistics
from pathlib import Path

T95 = {1: 12.7062, 2: 4.3027, 3: 3.1824, 4: 2.7764, 5: 2.5706, 6: 2.4469, 7: 2.3646,
       8: 2.3060, 9: 2.2622, 10: 2.2281, 11: 2.2010, 12: 2.1788}

# closed-loop config prefix -> (block, note)
CL_MAP = {
    "controlled-32-nc-b65536": ("4.1", "same condition"),
    "controlled-32-tcp-direct-b65536": ("4.1", "same condition"),
    "iid-zipf-32-nc-b65536": ("4.2b", "same condition"),
    "iid-zipf-32-tcp-direct-b65536": ("4.2b", "same condition"),
    "iid-uniform-32-nc-b65536": ("4.1-opt", "same condition"),
    "iid-uniform-32-tcp-direct-b65536": ("4.1-opt", "same condition"),
    "controlled-32-nc-b1024": ("4.2a", "distribution differs (controlled vs iid-zipf)"),
    "controlled-32-tcp-direct-b1024": ("4.2a", "distribution differs"),
    "controlled-32-nc-b1048576": ("4.2a", "distribution differs"),
    "controlled-32-tcp-direct-b1048576": ("4.2a", "distribution differs"),
}


def block_of(row):
    if row["family"] == "federation":
        return "4.4"
    if row["family"] in ("zipf", "uniform"):
        return "4.1-opt"
    if row["family"] == "controlled":
        return "4.1"
    if row["family"] == "size":
        if row["rate"] == "32":
            return "4.2a"
        return "E" if row.get("query_lifetime_seconds") == "7" else "4.2b"
    return row["family"]


def closed_loop(paths):
    out = collections.defaultdict(list)
    for path in paths:
        for r in csv.DictReader(open(path)):
            key = CL_MAP.get(r["config"])
            if key is None:
                continue
            method = "tcp-direct" if "tcp-direct" in r["config"] else "nc"
            out[(key[0], method, key[1])].append((float(r["end_to_end_ms"]),
                                                  float(r["end_to_end_ms_ci95_low"]),
                                                  float(r["end_to_end_ms_ci95_high"])))
    summary = {}
    for (block, method, note), vals in out.items():
        means = [v[0] for v in vals]
        summary[(block, method)] = dict(
            note=note, groups=len(vals),
            mean=round(statistics.mean(means), 3),
            ci_low=round(statistics.mean(v[1] for v in vals), 3),
            ci_high=round(statistics.mean(v[2] for v in vals), 3),
            first_group=round(vals[0][0], 3), last_group=round(vals[-1][0], 3))
    return summary


def new_side(label, path):
    rows = [r for r in csv.DictReader(open(path)) if r["verdict"] == "true"]
    by = collections.defaultdict(list)
    for r in rows:
        by[(block_of(r), r["method"])].append(float(r["total_mean_ms"]))
    out = {}
    for key, vals in by.items():
        n = len(vals)
        m, sd = statistics.mean(vals), (statistics.stdev(vals) if n > 1 else 0.0)
        h = T95.get(n - 1, 2.1788) * sd / math.sqrt(n) if n > 1 else 0.0
        out[key] = dict(n=n, mean=round(m, 3), ci_low=round(m - h, 3), ci_high=round(m + h, 3))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--closed-loop", action="append", required=True)
    ap.add_argument("--new", action="append", default=[])
    ap.add_argument("--out", default="/tmp/closed_loop_intervals.json")
    args = ap.parse_args()
    cl = closed_loop(args.closed_loop)
    sides = {}
    for item in args.new:
        label, path = item.split("=", 1)
        sides[label] = new_side(label, path)
    keys = sorted(set(cl) | {k for s in sides.values() for k in s})
    print("NOTE: closed loop = 1 request in flight + 100 ms think; supplement = open "
          "Poisson. Levels are not comparable across arrival modes; shape/layering only.\n")
    head = f"{'block':8s} {'method':11s} | {'closed loop mean[CI]':>28s}"
    for label in sides:
        head += f" | {label+' mean[CI]':>26s}"
    print(head)
    report = dict(closed_loop={f"{k[0]}:{k[1]}": v for k, v in cl.items()},
                  new={label: {f"{k[0]}:{k[1]}": v for k, v in side.items()}
                       for label, side in sides.items()},
                  note="interval-level comparison only; arrival modes differ")
    for key in keys:
        c = cl.get(key)
        line = f"{key[0]:8s} {key[1]:11s} | "
        line += (f"{c['mean']:8.2f}[{c['ci_low']:.1f},{c['ci_high']:.1f}]" if c else " " * 28)
        line += " " * max(0, 28 - (len(f"{c['mean']:8.2f}[{c['ci_low']:.1f},{c['ci_high']:.1f}]") if c else 0))
        for label, side in sides.items():
            s = side.get(key)
            line += (f" | {s['mean']:7.2f}[{s['ci_low']:.1f},{s['ci_high']:.1f}] n={s['n']:2d}"
                     if s else " | " + " " * 24)
        if c and c["note"] != "same condition":
            line += f"   [{c['note']}]"
        print(line)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("\nwrote", args.out)


if __name__ == "__main__":
    main()