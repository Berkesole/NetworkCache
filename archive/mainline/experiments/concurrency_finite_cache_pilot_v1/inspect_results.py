#!/usr/bin/env python3
"""Summarise the pilot screening results (status, verdicts, key metrics)."""
import json
import sys
from pathlib import Path

pr = Path(sys.argv[1])
phase = sys.argv[2] if len(sys.argv) > 2 else "screening"
root = pr / "raw" / phase
done = 0
for slot_dir in sorted(root.iterdir()):
    rj = slot_dir / "attempt001" / "result.json"
    name = slot_dir.name.split("r128-")[-1]
    if not rj.is_file():
        print(name + " -> no result yet")
        continue
    d = json.loads(rj.read_text())
    m = d["metrics"]
    bad = {k: v["state"] for k, v in d["acceptance"]["gates"].items()
           if v["state"] != "pass"}
    done += 1
    print("{} {} {} wall={:.1f} startup={} cmpl={} fail={} ev={} tput={} {}".format(
        name, d["status"], d["acceptance"]["verdict"], d["total_wall_seconds"],
        d.get("startup_seconds"), m["completed_measurement"], m["failed_measurement"],
        m["cache"]["totals"]["evicts"], m["window_throughput_per_s"],
        ("non_pass=" + json.dumps(bad)) if bad else ""))
print("done={} of 24".format(done))