#!/usr/bin/env python3
"""Platform evidence for R5: post-phase convergence per slot and domain.

R5 doubles the post phase (96 bins) to show the plateau is reached. For each slot the
local-control latency of each domain over the final 16 (bins 81-96) and final 8 (89-96)
post bins is fitted with an OLS slope and a 95% t CI, mirroring the activation gate.

Usage: post_slope_evidence.py <batch_latency.csv> <out.json>
"""
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict

T95 = {1: 12.7062, 2: 4.3027, 3: 3.1824, 4: 2.7764, 5: 2.5706, 6: 2.4469, 7: 2.3646,
       8: 2.3060, 9: 2.2622, 10: 2.2281, 11: 2.2010, 12: 2.1788, 13: 2.1604, 14: 2.1448,
       15: 2.1314, 16: 2.1199}


def fit(points):
    """OLS slope of latency over bin index with a 95% t CI (dof = n-2)."""
    n = len(points)
    if n < 3:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in points)
    if sxx == 0:
        return None
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in points]
    s2 = sum(r * r for r in resid) / (n - 2)
    se = math.sqrt(s2 / sxx)
    crit = T95.get(n - 2, 2.1199)
    lo, hi = slope - crit * se, slope + crit * se
    return dict(n=n, dof=n - 2, bins=[p[0] for p in points], slope_ms_per_bin=round(slope, 6),
                se_ms_per_bin=round(se, 6), ci_low=round(lo, 6), ci_high=round(hi, 6),
                contains_zero=lo <= 0 <= hi,
                mean_ms=round(statistics.mean(ys), 3))


def main():
    src_csv, out_json = sys.argv[1], sys.argv[2]
    per_slot = defaultdict(lambda: defaultdict(list))
    for r in csv.DictReader(open(src_csv)):
        if r.get("family") != "federation" or r.get("phase") != "post":
            continue
        if r.get("request_class") not in ("local_control", "local"):
            continue
        m = re.search(r"bin=(\d+)", r.get("source", ""))
        if not m:
            continue
        slot = r["source"].split(" ")[0].rstrip("/").split("/")[-1]
        slot = slot or r.get("slot_id", "")
        per_slot[slot][r["direction"]].append((int(m.group(1)), float(r["mean_ms"])))

    out = {}
    for slot, doms in per_slot.items():
        entry = {}
        for dom, pts in doms.items():
            pts.sort()
            entry[dom] = {"post_bins": len(pts),
                          "last_16": fit(pts[-16:]),
                          "last_8": fit(pts[-8:])}
        out[slot] = entry
    json.dump(dict(rule=("post-phase local latency should plateau: the OLS slope over the "
                         "final 16 and final 8 post bins should have a 95% t CI containing "
                         "zero"),
                   slots=out), open(out_json, "w"), indent=2, sort_keys=True)
    slots = len(out)
    for window in ("last_16", "last_8"):
        ok = sum(1 for s in out.values() for d in s.values()
                 if d.get(window) and d[window]["contains_zero"])
        total = sum(1 for s in out.values() for d in s.values() if d.get(window))
        print(f"{window}: {ok}/{total} domain-fits contain zero across {slots} slots")
    print("wrote", out_json)


if __name__ == "__main__":
    main()