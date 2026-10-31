#!/usr/bin/env python3
"""Verify that a held scenario lock excludes a second worker (lab names must not clash).

Every scenario in the supplement matrix is shared by 2-4 slots (the 4.2 size arms and
the E lifetime arm reuse the same scenario for a given seed, and nc/tcp-direct share
it), so the parallel driver must never start two slots of the same scenario at once.
"""
import sys
from pathlib import Path

PILOT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "/data/networkcache-concurrency-supplement-v1/source/experiments/concurrency_slides_v5")
sys.path.insert(0, str(PILOT_DIR))
import matrix  # noqa: E402
import pilot  # noqa: E402
import supplement_queue as sq  # noqa: E402


def main():
    root = pilot.ROOT
    scenarios = {}
    for c in matrix.slots("supplement"):
        pilot.context(c)
        scenarios.setdefault(pilot.SCENARIO, []).append(c["id"])
    shared = {k: v for k, v in scenarios.items() if len(v) > 1}
    print(f"distinct scenarios: {len(scenarios)} for "
          f"{sum(len(v) for v in scenarios.values())} slots")
    print(f"scenarios shared by more than one slot: {len(shared)}")
    name = sorted(shared)[0]
    a, b = sq.ScenarioLock(root, name), sq.ScenarioLock(root, name)
    first = a.acquire()
    second = b.acquire()
    a.release()
    third = b.acquire()
    b.release()
    assert first and not second and third, (first, second, third)
    print("SCENARIO EXCLUSION OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
