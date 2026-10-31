#!/usr/bin/env python3
"""Entry-level tests for offline_accept.verify_slot.

Copies the sealed selection into TEMPORARY directories (never touching the
original) and tampers with the copies:
  * add an unknown completion file
  * add an extra completion with no matching audit row
  * delete a planned completion
  * change the in-row request index
  * use a mismatched plan
and asserts the entry returns REJECTED (or a non-PASS verdict) for each.

Usage: test_offline_entry.py --selection DIR --inputs DIR --scenario SC \
           --owner-hashes FILE --frozen-lib DIR
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline_accept  # noqa: E402


def run_case(selection, slot_name, inputs, scenario, hashes, frozen_lib, mutate, label):
    tmp = Path(tempfile.mkdtemp(prefix="offline-entry-"))
    slot_dir = tmp / "slots" / slot_name
    shutil.copytree(selection / "slots" / slot_name, slot_dir)
    mutate(slot_dir)
    result = offline_accept.verify_slot(slot_dir, Path(inputs), scenario, 16,
                                        hashes, Path(frozen_lib))
    verdict = result["verdict"]
    ok = verdict != "PASS"
    print(f"{label}: {verdict} {'PASS' if ok else 'FAIL'}")
    return ok, result


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selection", required=True)
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--owner-hashes", required=True)
    ap.add_argument("--frozen-lib", required=True)
    args = ap.parse_args(argv)

    selection = Path(args.selection)
    hashes = json.loads(Path(args.owner_hashes).read_text(encoding="utf-8"))["owner_hashes"]
    # use a TCP slot: one completion set, no cache/monitor dependencies
    slot_name = "finite-smoke-n16-r32-tcp-direct"
    checks = []

    # baseline (unmodified copy) must stay PASS
    tmp = Path(tempfile.mkdtemp(prefix="offline-entry-base-"))
    shutil.copytree(selection / "slots" / slot_name, tmp / "slots" / slot_name)
    base = offline_accept.verify_slot(tmp / "slots" / slot_name, Path(args.inputs),
                                      args.scenario, 16, hashes, Path(args.frozen_lib))
    checks.append((f"baseline_{base['verdict']}", base["verdict"] == "PASS"))
    print(f"baseline: {base['verdict']} {'PASS' if base['verdict'] == 'PASS' else 'FAIL'}")

    def add_unknown(slot_dir):
        (slot_dir / "evidence" / "control" / "completions" / "777777.tsv").write_text(
            "\t".join(["777777", "1", "k1", "ok", "65536", "0" * 16]) + "\n",
            encoding="utf-8")

    def add_extra_no_audit(slot_dir):
        # copy an existing completion under a new index that is not planned
        src = sorted((slot_dir / "evidence" / "control" / "completions").glob("*.tsv"))[0]
        (slot_dir / "evidence" / "control" / "completions" / "888888.tsv").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8")

    def delete_planned(slot_dir):
        sorted((slot_dir / "evidence" / "control" / "completions").glob("*.tsv"))[0].unlink()

    def change_row_index(slot_dir):
        path = sorted((slot_dir / "evidence" / "control" / "completions").glob("*.tsv"))[0]
        fields = path.read_text(encoding="utf-8").strip().split("\t")
        fields[0] = "424242"
        path.write_text("\t".join(fields) + "\n", encoding="utf-8")

    def mismatch_plan(slot_dir):
        plan = json.loads((slot_dir / "plan.json").read_text(encoding="utf-8"))
        plan["measurement_arrival_ns"] = [[0, 1_000_000, 2_000_000, 3_000_000]] * 4
        (slot_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    for label, mutate in (("add_unknown_completion", add_unknown),
                          ("add_extra_completion_without_audit", add_extra_no_audit),
                          ("delete_planned_completion", delete_planned),
                          ("change_in_row_index", change_row_index),
                          ("mismatched_plan", mismatch_plan)):
        ok, _ = run_case(selection, slot_name, args.inputs, args.scenario, hashes,
                         args.frozen_lib, mutate, label)
        checks.append((label, ok))

    failed = [n for n, ok in checks if not ok]
    print(f"checks={len(checks)} failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))