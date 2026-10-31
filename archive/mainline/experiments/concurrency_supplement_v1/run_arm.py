#!/usr/bin/env python3
"""Run a set of slots sequentially in one process (quiet, slots_in_parallel = 1).

Unlike the queue driver, which walks the whole stage, this runs only the slots matching
the given patterns - used for the RERUN_SPEC blocks (one arm at a time) so the campaign's
other slots are left untouched.

Usage: run_arm.py --src <source experiments/concurrency_slides_v5> --match <substr>
       [--match ...] [--run] [--limit N]
"""
import argparse
import json
import os
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--match", action="append", required=True)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="rerun")
    args = ap.parse_args()
    sys.path.insert(0, args.src)
    import pilot  # noqa: E402
    import supplement_queue_v2 as drv  # noqa: E402

    root = pilot.ROOT
    hits = [c for c in drv.slots()
            if any(p in c["condition"] or p in c["id"] for p in args.match)]
    hits.sort(key=lambda c: c["id"])
    if args.limit:
        hits = hits[:args.limit]
    print(f"root={root} stage={drv.STAGE} matched={len(hits)}", flush=True)
    for c in hits:
        print("   ", c["id"], "| lifetime", c["query_lifetime_seconds"], flush=True)
    if not args.run:
        return 0

    done = 0
    for c in hits:
        if not drv.claim(root, c["id"], args.tag):
            print(f"skip (already claimed): {c['id']}", flush=True)
            continue
        marker = drv.mark_running(root, c["id"])
        os.environ["RSYNC_SLOTS_IN_PARALLEL"] = str(len(drv.running_markers(root)))
        t0 = time.time()
        try:
            out = drv.run_slot(root, c, args.tag)
        finally:
            drv.unmark_running(marker)
        done += 1
        print(json.dumps(dict(slot=c["id"], status=out.get("status"),
                              qualified=out.get("qualified"), reason=out.get("reason"),
                              wall_seconds=round(time.time() - t0, 1)), default=str), flush=True)
    print(f"ARM DONE: {done}/{len(hits)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())