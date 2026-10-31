#!/usr/bin/env python3
"""Fast variant of retry_failed.py: classify from the per-slot progress records.

reports.validate re-parses every trace of every slot, which takes tens of minutes for
120 slots. The driver already wrote one progress record per run slot, so the
classification (qualified / failed / interrupted) can be read directly. Any slot that
has a qualified record is left untouched.

Usage: retry_fast.py [--apply]
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time
from pathlib import Path

STAGE = "supplement"
sys.path.insert(0, "/data/networkcache-concurrency-supplement-v1/source/experiments/concurrency_slides_v5")
import matrix  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = Path("/data/networkcache-concurrency-supplement-v1")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    counts = {}
    archived, todo, to_run = [], [], []
    for config in matrix.slots(STAGE):
        slot = config["id"]
        raw_dir = root / "raw" / STAGE / slot
        attempts = sorted(raw_dir.glob("attempt*"))
        record_path = root / "derived" / "supplement-progress" / f"{slot}.json"
        record = None
        if record_path.is_file():
            try:
                record = json.loads(record_path.read_text())
            except json.JSONDecodeError:
                record = None
        if record and record.get("qualified"):
            state = "qualified"
        elif not attempts:
            state = "no_attempt"
        else:
            live = [a for a in attempts if (a / "result.json").is_file()]
            state = "not_qualified" if live else "unfinished_attempt"
        counts[state] = counts.get(state, 0) + 1
        if state == "qualified":
            continue
        if state == "no_attempt":
            # never started, but an earlier driver may have left a claim behind that
            # would stop any worker from ever claiming it: release claim and lab only
            if args.apply:
                claim = root / "derived" / "supplement-claims" / slot
                if claim.exists():
                    shutil.rmtree(claim, ignore_errors=True)
                for lab_attempt in (root / "lab" / STAGE / slot).glob("attempt*"):
                    shutil.rmtree(lab_attempt, ignore_errors=True)
            to_run.append(slot)
            continue
        todo.append(slot)
        if not args.apply:
            continue
        target = root / "recovery" / f"retry-{slot}-{stamp}"
        (target / "raw").mkdir(parents=True, exist_ok=True)
        for attempt in attempts:
            shutil.copytree(attempt, target / "raw" / attempt.name, dirs_exist_ok=True)
        derived = root / "derived" / STAGE / slot
        if derived.exists():
            shutil.copytree(derived, target / "derived", dirs_exist_ok=True)
        for attempt in attempts:               # the archive copy exists by now
            shutil.rmtree(attempt, ignore_errors=True)
        if derived.exists():
            shutil.rmtree(derived, ignore_errors=True)
        for lab_attempt in (root / "lab" / STAGE / slot).glob("attempt*"):
            shutil.rmtree(lab_attempt, ignore_errors=True)
        claim = root / "derived" / "supplement-claims" / slot
        if claim.exists():
            shutil.rmtree(claim, ignore_errors=True)
        record_path.unlink(missing_ok=True)    # a fresh run will write a new record
        (target / "reason.json").write_text(json.dumps(
            dict(slot=slot, state=state, record=record, attempts=[str(a) for a in attempts]),
            default=str, indent=2) + "\n")
        archived.append(slot)
    report = dict(stamp=stamp, applied=bool(args.apply), counts=counts,
                  todo=todo, to_run=to_run, archived=archived)
    (root / "recovery" / f"retry-fast-{stamp}.json").write_text(
        json.dumps(report, default=str, indent=2, sort_keys=True) + "\n")
    print(json.dumps(dict(counts=counts, to_run=len(to_run), todo=len(todo),
                          archived=len(archived)), indent=2))
    for slot in archived:
        print("   archived", slot[:60])


if __name__ == "__main__":
    main()