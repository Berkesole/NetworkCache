#!/usr/bin/env python3
"""Input-contract test for all 120 supplement slots (no containers, no lab).

Validates, per slot: the schedule's measurement count equals matrix.request_count,
the group split (single continuous segment for non-federation; pre 4096 / post 7680
for federation), the setup rows (32 for single-cluster families; the federation
schedule carries no setup phase and the pilot uses quiesce instead), the recorded
query lifetime, and a 64-hex schedule digest.
"""
import sys
from pathlib import Path

PILOT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "/data/networkcache-concurrency-supplement-v1/source/experiments/concurrency_slides_v5")
sys.path.insert(0, str(PILOT_DIR))
import pilot  # noqa: E402
import matrix  # noqa: E402

def main():
    slots = matrix.slots("supplement")
    bad, summary = 0, {}
    for c in slots:
        try:
            setup, groups, digest = pilot.input_rows(c)
            n = sum(len(g) for g in groups)
            assert n == matrix.request_count(c), (n, matrix.request_count(c))
            if c["family"] == "federation":
                assert [len(g) for g in groups] == [4096, 7680], [len(g) for g in groups]
                assert len(setup) == 0, len(setup)
                assert [g[0]["phase"] for g in groups] == ["pre", "post"], [g[0]["phase"] for g in groups]
                key = "federation"
            else:
                assert len(groups) == 1, len(groups)
                assert len(setup) == 32, len(setup)
                key = c["family"]
            assert len(digest) == 64
            summary.setdefault(key, set()).add((n, len(setup), len(groups), c["query_lifetime_seconds"]))
        except Exception as exc:
            bad += 1
            if bad <= 3:
                print("FAIL", c["id"], repr(exc))
    print("slots:", len(slots), "failures:", bad)
    for k in sorted(summary):
        print(f"  {k}: (measurement, setup, groups, lifetime) = {sorted(summary[k])}")
    print("OK" if bad == 0 else "NOT OK")
    return 1 if bad else 0

if __name__ == "__main__":
    raise SystemExit(main())
