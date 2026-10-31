#!/usr/bin/env python3
"""Collect the full activation-gate evidence for a set of slots.

The delivered slot table carries the gate slope and one CI bound per domain; this
sidecar adds both bounds, the bins used and contains_zero, read straight from each
slot's raw warmup_gate.json, so the "both domains' final-8-bin slope CI contains zero"
requirement can be checked slot by slot.

Usage: gate_evidence.py <root> <stage> <pattern> <out.json>
"""
import glob
import json
import os
import sys

root, stage, pattern, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
evidence = {}
for gp in sorted(glob.glob(os.path.join(root, "raw", stage, pattern + "*", "attempt*",
                                        "warmup_gate.json"))):
    slot = gp.split(os.sep)[-3]
    gate = json.load(open(gp))
    doms = {}
    for dom, v in (gate.get("domains") or {}).items():
        doms[dom] = dict(bins=v.get("bins"), slope_ns_per_bin=v.get("slope_ns_per_bin"),
                         se_ns_per_bin=v.get("se_ns_per_bin"),
                         ci_low_ns_per_bin=v.get("ci_low_ns_per_bin"),
                         ci_high_ns_per_bin=v.get("ci_high_ns_per_bin"),
                         contains_zero=v.get("contains_zero"), dof=v.get("dof"))
    evidence[slot] = dict(passed=gate.get("passed"), bins_used=gate.get("bins_used"),
                          reasons=gate.get("reasons"), domains=doms)
json.dump(dict(rule=("each domain's OLS slope of local latency over the final 8 pre bins "
                     "must have a 95% t CI containing zero, otherwise warmup_gate_failed"),
               slots=evidence), open(out, "w"), indent=2, sort_keys=True)
ok = [s for s, v in evidence.items()
      if v["passed"] and all(d["contains_zero"] for d in v["domains"].values())]
print(f"{out}: {len(evidence)} slots, {len(ok)} with both domains' CI containing zero")
print("  bins_used:", sorted({tuple(v['bins_used'] or []) for v in evidence.values()}))