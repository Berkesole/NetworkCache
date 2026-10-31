#!/usr/bin/env python3
"""R4 preparation pipeline for one machine: build and freeze the pre-48-bin root.

Runs, in order: the root bootstrap (source copy, gate tightening, v4-formal input
regeneration, initial freeze), the fallback-lineage generator fix and regeneration, the
scenario YAML copy from the frozen campaign, the count-edit re-freeze, and the stage
restriction to the 20 federation slots. Each step's own asserts gate the next, so a
failure stops the pipeline with the step named.

Usage: prep_pre48_full.py <old_root> <new_root> <label>
"""
import shutil
import subprocess
import sys
from pathlib import Path

OLD, NEW, LABEL = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
REC = OLD / "recovery"


def step(label, argv):
    print(f"\n=== [{LABEL}] {label} ===", flush=True)
    r = subprocess.run(["python3"] + argv, capture_output=True, text=True)
    tail = (r.stdout or "").strip().splitlines()[-6:]
    for line in tail:
        print("   " + line, flush=True)
    if r.returncode != 0:
        print("STDERR:\n" + (r.stderr or "")[-1500:], flush=True)
        raise SystemExit(f"step failed: {label}")
    print(f"   [ok] {label}", flush=True)


step("bootstrap", [str(REC / "bootstrap_pre48.py"), str(OLD), str(NEW), LABEL])
step("fallback generator fix", [str(REC / "pre48_fix_generators.py"), str(NEW)])

print(f"\n=== [{LABEL}] scenario YAMLs ===", flush=True)
dst = NEW / "inputs/federation/scenarios"
dst.mkdir(parents=True, exist_ok=True)
shutil.copytree(OLD / "inputs/federation/scenarios", dst, dirs_exist_ok=True)
n = len(list(dst.glob("*.yaml")))
print(f"   scenarios present: {n}", flush=True)
assert n >= 40

step("count edits + freeze", [str(REC / "finish_pre48.py"), str(NEW), str(OLD), LABEL])
step("restrict to the federation arm", [str(REC / "restrict_pre48_slots.py"),
                                        str(NEW), str(OLD), LABEL])
print(f"\nPREP DONE {LABEL}", flush=True)