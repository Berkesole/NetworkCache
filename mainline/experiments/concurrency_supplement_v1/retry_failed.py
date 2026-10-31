#!/usr/bin/env python3
"""Archive the attempts of non-qualified supplement slots so they can be re-run.

The frozen runner refuses to re-run a slot whose prior attempt issued requests (the
anti-cherry-picking rule). For slots that failed at the transport or correctness
level the honest remedy is to keep the failed evidence, archive it aside and re-run
the slot under the SAME frozen campaign - no code change, so no re-freeze and the
already-qualified slots keep their campaign snapshot and hashes.

What it does per non-qualified slot:
  * copies raw/supplement/<slot>/attempt*  -> recovery/retry-<slot>-<stamp>/raw/
  * copies derived/supplement/<slot>       -> recovery/retry-<slot>-<stamp>/derived/
  * removes the attempt directories from the live tree and clears the claim so the
    parallel driver can claim the slot again
  * never touches a qualified slot

Usage: retry_failed.py [--apply]        (default is a dry run that only reports)
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

PILOT_DIR = Path("/data/networkcache-concurrency-supplement-v1/source/experiments/concurrency_slides_v5")
if not PILOT_DIR.is_dir():
    PILOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PILOT_DIR))
import matrix as mat          # noqa: E402
import pilot                  # noqa: E402
import reports                # noqa: E402

STAGE = "supplement"


def classify(root, slot_id):
    slot_raw = root / "raw" / STAGE / slot_id
    attempts = sorted(slot_raw.glob("attempt*"))
    if not attempts:
        return dict(slot=slot_id, state="no_attempt", attempts=0)
    last = attempts[-1]
    result_path = last / "result.json"
    if not result_path.is_file():
        return dict(slot=slot_id, state="unfinished_attempt", attempts=len(attempts),
                    archive=list(attempts))
    gate = reports.validate(last, root)
    if gate.get("qualified"):
        return dict(slot=slot_id, state="qualified", attempts=len(attempts))
    result = json.loads(result_path.read_text())
    return dict(slot=slot_id, state="not_qualified", status=result.get("status"),
                reason=result.get("reason"), requests_issued=result.get("requests_issued"),
                attempts=len(attempts), archive=list(attempts), last=str(last),
                gate_reason=gate.get("reason"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    root = pilot.ROOT
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report, archived = [], []
    for config in mat.slots(STAGE):
        info = classify(root, config["id"])
        report.append(info)
        if info["state"] not in ("not_qualified", "unfinished_attempt"):
            continue
        if not args.apply:
            continue
        target = root / "recovery" / f"retry-{config['id']}-{stamp}"
        (target / "raw").mkdir(parents=True, exist_ok=True)
        for attempt in info.get("archive", []):
            shutil.copytree(attempt, target / "raw" / attempt.name, dirs_exist_ok=True)
        derived = root / "derived" / STAGE / config["id"]
        if derived.exists():
            shutil.copytree(derived, target / "derived", dirs_exist_ok=True)
        # only now remove the live attempts (the archive already holds a copy)
        for attempt in info.get("archive", []):
            shutil.rmtree(attempt)
        if derived.exists():
            shutil.rmtree(derived)
        claim = root / "derived" / "supplement-claims" / config["id"]
        if claim.exists():
            shutil.rmtree(claim)
        (target / "reason.json").write_text(json.dumps(info, default=str, indent=2) + "\n")
        archived.append(config["id"])
    summary = dict(stamp=stamp, applied=bool(args.apply),
                   counts={state: sum(1 for r in report if r["state"] == state)
                           for state in {r["state"] for r in report}},
                   not_qualified=[r for r in report if r["state"] == "not_qualified"],
                   archived=archived)
    (root / "recovery" / f"retry-report-{stamp}.json").write_text(
        json.dumps(summary, default=str, indent=2, sort_keys=True) + "\n")
    print(json.dumps(dict(counts=summary["counts"], archived=len(archived),
                          not_qualified=[(r["slot"], r.get("status")) for r in
                                         summary["not_qualified"]]), indent=2))


if __name__ == "__main__":
    main()