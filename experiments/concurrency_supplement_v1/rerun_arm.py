#!/usr/bin/env python3
"""Re-run one arm of the campaign under low host pressure (deliberate, documented).

The section 6.1 equivalence check flagged the 4.2 tcp-direct 64 KiB arm against the
frozen baseline. The gap tracks host CPU pressure (pearson r = +0.75 over the ten
seeds; the supplement ran at a mean peak of 67.6% against 19.6-32.0% for every
baseline slot), so the arm is re-measured serially - one lab at a time - to remove
the confound and re-test the equivalence directly. The existing attempts are archived
first, exactly like the retry path, so nothing measured is lost.

Usage: rerun_arm.py --family size --size 65536 [--apply]
"""
import argparse
import json
import shutil
import time
from pathlib import Path

STAGE = "supplement"
N = Path("/data/networkcache-concurrency-supplement-v1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="size")
    ap.add_argument("--size", type=str, default="65536")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = N
    import sys
    sys.path.insert(0, str(root / "source/experiments/concurrency_slides_v5"))
    import matrix  # noqa: E402

    stamp = time.strftime("%Y%m%d-%H%M%S")
    selected, archived = [], []
    for config in matrix.slots(STAGE):
        if config["family"] != args.family or str(config["size"]) != args.size:
            continue
        slot = config["id"]
        selected.append(slot)
        if not args.apply:
            continue
        raw_dir = root / "raw" / STAGE / slot
        attempts = sorted(raw_dir.glob("attempt*"))
        derived = root / "derived" / STAGE / slot
        target = root / "recovery" / f"rerun-{slot}-{stamp}"
        if attempts:
            (target / "raw").mkdir(parents=True, exist_ok=True)
            for attempt in attempts:
                shutil.copytree(attempt, target / "raw" / attempt.name, dirs_exist_ok=True)
            if derived.exists():
                shutil.copytree(derived, target / "derived", dirs_exist_ok=True)
            for attempt in attempts:
                shutil.rmtree(attempt, ignore_errors=True)
        if derived.exists():
            shutil.rmtree(derived, ignore_errors=True)
        for lab_attempt in (root / "lab" / STAGE / slot).glob("attempt*"):
            shutil.rmtree(lab_attempt, ignore_errors=True)
        claim = root / "derived" / "supplement-claims" / slot
        if claim.exists():
            shutil.rmtree(claim, ignore_errors=True)
        (root / "derived" / "supplement-progress" / f"{slot}.json").unlink(missing_ok=True)
        if target.exists():
            (target / "reason.json").write_text(json.dumps(
                dict(slot=slot, reason="section 6.1 host-pressure confound; re-run serially",
                     attempts=[str(a) for a in attempts]), indent=2) + "\n")
        archived.append(slot)
    report = dict(stamp=stamp, applied=bool(args.apply), family=args.family,
                  size=args.size, selected=selected, archived=archived)
    (root / "recovery" / f"rerun-arm-{stamp}.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(dict(selected=len(selected), archived=len(archived),
                          applied=bool(args.apply)), indent=2))
    for slot in selected[:6]:
        print("   ", slot[:64])


if __name__ == "__main__":
    main()