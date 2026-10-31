#!/usr/bin/env python3
"""Archive and clear one arm so it can be re-measured under quiet conditions.

Used for the section 6 check 1 anchor: the 4.2b arm must be compared against the frozen
volcengine-3 baseline, whose slots all ran at 19.6-38% host CPU peak, while the campaign
ran far hotter. Re-measuring the arm serially (--workers 1) brings it into the baseline's
load band; the campaign's own attempts are archived first so the main delivery, already
extracted from them, is unaffected.

Usage: prep_arm_rerun.py <root> <stage> --match <substring> [--exclude <substring>] [--apply]
"""
import glob
import json
import shutil
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
flags = [a for a in argv if a.startswith("--")]
plain = [a for a in argv if not a.startswith("--")]
root, stage = Path(plain[0]), plain[1]


def value(name, default=None):
    if name in argv:
        return argv[argv.index(name) + 1]
    return default


match = value("--match")
exclude = value("--exclude")
apply = "--apply" in flags
assert match, "--match is required"

targets = []
for p in glob.glob(str(root / "raw" / stage / "*")):
    slot = Path(p).name
    if match in slot and (exclude is None or exclude not in slot):
        targets.append(slot)
targets.sort()
print(f"root={root.name} match={match!r} exclude={exclude!r} -> {len(targets)} slots")
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
    target = root / "recovery" / f"anchor-{slot}-{stamp}"
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
        dict(slot=slot, reason="section 6.1 anchor: quiet serial re-measurement"), indent=2) + "\n")
print(f"prepared {len(targets)} slots; relaunch the driver with --workers 1")