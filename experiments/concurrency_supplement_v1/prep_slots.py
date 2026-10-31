#!/usr/bin/env python3
"""Archive and clear a set of slots so the driver re-runs exactly them.

Targets are chosen by substring match against the slot id; every pattern may be given
more than once. Used for the RERUN_SPEC blocks: the named spike/stall slots (R1/R2) and
the whole 1 MiB arm (R3), and the federation slots of the pre-48-bin campaign (R4).
The campaign's own attempts are archived to recovery/ first, so nothing already
delivered is lost.

Usage: prep_slots.py <root> <stage> --match <substring> [--match ...] [--apply]
"""
import glob
import json
import shutil
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
plain = [a for a in argv if not a.startswith("--")]
root, stage = Path(plain[0]), plain[1]
patterns = [argv[i + 1] for i, a in enumerate(argv) if a == "--match"]
apply = "--apply" in argv
assert patterns, "at least one --match is required"

targets = []
for p in glob.glob(str(root / "raw" / stage / "*")):
    slot = Path(p).name
    if any(pat in slot for pat in patterns):
        targets.append(slot)
targets.sort()
print(f"root={root.name} stage={stage} patterns={len(patterns)} -> {len(targets)} slots")
for slot in targets:
    print("   ", slot)
if not apply:
    print("dry run (pass --apply to archive and clear)")
    raise SystemExit(0)

stamp = time.strftime("%Y%m%d-%H%M%S")
for slot in targets:
    raw_dir = root / "raw" / stage / slot
    derived = root / "derived" / stage / slot
    lab = root / "lab" / stage / slot
    target = root / "recovery" / f"rerun-{slot}-{stamp}"
    (target / "raw").mkdir(parents=True, exist_ok=True)
    for attempt in sorted(raw_dir.glob("attempt*")):
        shutil.copytree(attempt, target / "raw" / attempt.name, dirs_exist_ok=True)
    if derived.exists():
        shutil.copytree(derived, target / "derived", dirs_exist_ok=True)
    for attempt in sorted(raw_dir.glob("attempt*")):
        shutil.rmtree(attempt, ignore_errors=True)
    if raw_dir.exists():
        shutil.rmtree(raw_dir, ignore_errors=True)
    if derived.exists():
        shutil.rmtree(derived, ignore_errors=True)
    if lab.exists():
        shutil.rmtree(lab, ignore_errors=True)
    claim = root / "derived" / "supplement-claims" / slot
    if claim.exists():
        shutil.rmtree(claim, ignore_errors=True)
    (root / "derived" / "supplement-progress" / f"{slot}.json").unlink(missing_ok=True)
    (target / "reason.json").write_text(json.dumps(
        dict(slot=slot, reason="RERUN_SPEC quiet re-measurement"), indent=2) + "\n")
print(f"prepared {len(targets)} slots; relaunch the driver with --workers 1")