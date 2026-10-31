#!/usr/bin/env python3
"""Archive and clear the slots that must be re-run, so the driver picks them up again.

Targets, per track:
  * slots whose progress record is not qualified (e.g. a warm-up gate that did not
    converge - the specification says to stop that slot, and a re-run is the honest way
    to obtain a qualified measurement);
  * slots whose result.json recorded slots_in_parallel > 2 (a spot check that ran while
    the campaign was already at two workers).

Everything else is untouched. The archived evidence is kept under
recovery/rerun-<slot>-<stamp>/, and only then are the live directories, the claim and
the progress record removed.

Usage: prep_reruns.py <root> <stage> [--apply]
"""
import glob
import json
import shutil
import sys
import time
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
root = Path(args[0])
stage = args[1]
apply = "--apply" in sys.argv

targets = {}
for p in glob.glob(str(root / "derived" / "supplement-progress" / "*.json")):
    d = json.loads(Path(p).read_text())
    if not d.get("qualified"):
        targets[d["slot_id"]] = f"not qualified: {d.get('reason')}"
for rp in glob.glob(str(root / "raw" / stage / "*" / "attempt*" / "result.json")):
    r = json.loads(Path(rp).read_text())
    sip = r.get("slots_in_parallel")
    if isinstance(sip, int) and sip > 2:
        slot = Path(rp).parts[-3]
        targets[slot] = f"slots_in_parallel={sip}"

print(f"root={root.name} stage={stage} targets={len(targets)}")
for slot, why in sorted(targets.items()):
    print(f"   {slot[:60]}  <- {why}")
if not apply:
    print("dry run (pass --apply to archive and clear)")
    raise SystemExit(0)

stamp = time.strftime("%Y%m%d-%H%M%S")
for slot, why in sorted(targets.items()):
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
    record = root / "derived" / "supplement-progress" / f"{slot}.json"
    record.unlink(missing_ok=True)
    (target / "reason.json").write_text(json.dumps(dict(slot=slot, why=why), indent=2) + "\n")
    print("prepared", slot[:60])
print("done; relaunch the driver to re-run these slots")