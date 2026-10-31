#!/usr/bin/env python3
"""Port the slides-v5 source to the supplement root: no hardcoded campaign paths.

ROOT is derived from the module location, so the same branch can be checked out
under any campaign root (the old root stays untouched and frozen).
"""
import sys
from pathlib import Path

SRC = Path(sys.argv[1])  # .../<ROOT>/source/experiments/concurrency_slides_v5
ROOT_NAME = sys.argv[2]  # e.g. networkcache-concurrency-supplement-v1
BRANCH = sys.argv[3]     # e.g. codex/concurrency-supplement-v1


def patch(name, pairs):
    p = SRC / name
    s = p.read_text()
    for old, new, count in pairs:
        got = s.count(old)
        assert got == count, f"{name}: anchor x{got} != {count}: {old[:70]!r}"
        s = s.replace(old, new, count)
    p.write_text(s)
    print("patched", name)


patch("pilot.py", [
    ("ROOT=Path('/data/networkcache-concurrency-slides-v5');SOURCE=ROOT/'source';HERE=Path(__file__).resolve().parent",
     "ROOT=Path(__file__).resolve().parents[3];SOURCE=ROOT/'source';HERE=Path(__file__).resolve().parent", 1),
    ("UNIT='networkcache-concurrency-slides-v5'",
     f"UNIT='{ROOT_NAME}'", 1),
    (" assert out(['git','-C',str(SOURCE),'branch','--show-current'])=='codex/concurrency-slides-v5'",
     f" assert out(['git','-C',str(SOURCE),'branch','--show-current'])=='{BRANCH}'", 1),
])

patch("kathara_bridge.py", [
    ("ROOT=pathlib.Path('/data/networkcache-concurrency-slides-v5')",
     "ROOT=pathlib.Path(__file__).resolve().parents[1]", 1),
])

patch("network_backend.py", [
    (" base=pathlib.Path('/data/networkcache-concurrency-slides-v5')",
     " base=pathlib.Path(__file__).resolve().parents[3]", 1),
])
print("port complete")