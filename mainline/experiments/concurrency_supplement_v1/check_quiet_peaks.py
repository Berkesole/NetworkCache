#!/usr/bin/env python3
"""Report, per target block, how many quiet re-run slots have finished and their peaks.

Usage: check_quiet_peaks.py <root> <stage> <pattern>[:<label>] [<pattern>[:<label>] ...]
"""
import glob
import json
import os
import statistics
import sys

root, stage = sys.argv[1], sys.argv[2]
specs = []
for arg in sys.argv[3:]:
    pat, _, label = arg.partition(":")
    specs.append((pat, label or pat[:14]))

for pat, label in specs:
    sel = []
    for d in sorted(glob.glob(os.path.join(root, "derived", stage, pat + "*"))):
        slot = os.path.basename(d)
        rec = os.path.join(root, "derived", "supplement-progress", slot + ".json")
        done = os.path.isfile(rec) and json.load(open(rec)).get("qualified")
        peak = None
        sip = None
        for h in glob.glob(os.path.join(d, "attempt*", "health_and_mechanisms.json")):
            r = json.load(open(h)).get("resources") or {}
            peak = r.get("host_cpu_peak_percent")
        for rp in glob.glob(os.path.join(d, "attempt*", "result.json")):
            sip = json.load(open(rp)).get("slots_in_parallel")
        sel.append((slot, done, peak, sip))
    fin = [t for t in sel if t[1] and isinstance(t[2], (int, float))]
    print(f"{label}: {len(fin)}/{len(sel)} finished")
    if fin:
        peaks = sorted(t[2] for t in fin)
        sips = sorted({t[3] for t in fin})
        print(f"   peaks min {peaks[0]:.1f} median {statistics.median(peaks):.1f} "
              f"max {peaks[-1]:.1f} | >=90%: {sum(1 for v in peaks if v >= 90)} | sip {sips}")
    pend = [t[0][-24:] for t in sel if not t[1]]
    if pend:
        print(f"   pending ({len(pend)}): {pend}")