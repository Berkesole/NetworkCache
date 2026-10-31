#!/usr/bin/env python3
"""Anchor smoke for Track B: run the 4.2b seed001 pair with the frozen driver.

The handoff requires the 4.2b anchor pair (one nc + one tcp-direct, seed001) to run
first so the new freeze, the extractor, slots_in_parallel / offered_mib_per_s and the
per-class federation rows can be checked before the full 180-slot launch.

It imports the frozen supplement_queue_v2 driver and reuses its per-slot contract
(claim, running marker, run_slot, diagnose, validate, progress record), so no tracked
file changes and no re-freeze. The two anchors share one scenario, and the driver's
scenario lock exists precisely to keep two slots of one scenario apart, so they are
run one after the other - slots_in_parallel is then correctly recorded as 1 for them.

Usage: smoke_anchor_v2.py [--run]
"""
import argparse
import json
import os
import sys

SRC = "/data/networkcache-concurrency-supplement-v2/source/experiments/concurrency_slides_v5"
sys.path.insert(0, SRC)
import pilot          # noqa: E402
import supplement_queue_v2 as drv  # noqa: E402

ANCHOR = "size-n32-b65536-c512-iid-zipf-poisson-r128-"


def anchors():
    """The 4.2b anchor pair plus one 4.4 slot, so the smoke also exercises the R3
    per-class federation rows, the warm-up gate and the activation evidence."""
    out = [c for c in drv.slots()
           if c["condition"].startswith(ANCHOR) and not c["condition"].endswith("-lt7s")
           and c["seed"] == 1]
    assert len(out) == 2, [c["condition"] for c in out]
    fed = [c for c in drv.slots()
           if c["family"] == "federation" and c["method"] == "nc" and c["seed"] == 1]
    assert len(fed) == 1, [c["condition"] for c in fed]
    return out + fed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    root = pilot.ROOT
    print("root:", root, flush=True)
    for c in anchors():
        if not args.run:
            print(json.dumps(dict(slot=c["id"], condition=c["condition"], dry_run=True)),
                  flush=True)
            continue
        if not drv.claim(root, c["id"], "smoke"):
            print(json.dumps(dict(slot=c["id"], claimed=False)), flush=True)
            continue
        marker = drv.mark_running(root, c["id"])
        os.environ["RSYNC_SLOTS_IN_PARALLEL"] = str(len(drv.running_markers(root)))
        try:
            out = drv.run_slot(root, c, "smoke")
        finally:
            drv.unmark_running(marker)
        print(json.dumps(dict(slot=c["id"], status=out.get("status"),
                              qualified=out.get("qualified"),
                              slots_in_parallel=os.environ["RSYNC_SLOTS_IN_PARALLEL"],
                              wall_seconds=out.get("wall_seconds"),
                              reason=out.get("reason")), default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())