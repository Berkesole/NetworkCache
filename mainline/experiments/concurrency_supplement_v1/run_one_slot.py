#!/usr/bin/env python3
"""Run one specific supplement_v2 slot (spot check), reusing the frozen driver.

Useful for exercising a block ahead of the shuffled order - e.g. confirming the E
family (query lifetime 7 s) validates after the reports.py fix, before the campaign
reaches those slots naturally.

Usage: run_one_slot.py --src <source experiments/concurrency_slides_v5> --match <substring> [--run]
"""
import argparse
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--match", required=True)
    ap.add_argument("--method", default=None)
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, args.src)
    import pilot  # noqa: E402
    import supplement_queue_v2 as drv  # noqa: E402
    root = pilot.ROOT
    hits = [c for c in drv.slots()
            if (args.match in c["condition"] or args.match in c["id"])
            and (args.method is None or c["method"] == args.method)]
    print("root:", root, "| candidates:", len(hits))
    for c in hits:
        print("  ", c["id"], "| lifetime", c["query_lifetime_seconds"])
    if not args.run:
        return 0
    if len(hits) != 1:
        print("refusing to run: need exactly one candidate", file=sys.stderr)
        return 2
    c = hits[0]
    if not drv.claim(root, c["id"], "spotcheck"):
        print("already claimed")
        return 0
    marker = drv.mark_running(root, c["id"])
    os.environ["RSYNC_SLOTS_IN_PARALLEL"] = str(len(drv.running_markers(root)))
    try:
        out = drv.run_slot(root, c, "spotcheck")
    finally:
        drv.unmark_running(marker)
    print(json.dumps(dict(slot=c["id"], status=out.get("status"), qualified=out.get("qualified"),
                          reason=out.get("reason"), wall_seconds=out.get("wall_seconds")),
                     default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())