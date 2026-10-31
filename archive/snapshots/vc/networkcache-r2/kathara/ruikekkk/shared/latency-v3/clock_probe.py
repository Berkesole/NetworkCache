#!/usr/bin/env python3
"""Record a barrier-released monotonic timestamp inside one Kathara host."""

import argparse
import hashlib
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("node", type=int)
parser.add_argument("root", type=Path)
args = parser.parse_args()
while not (args.root / "go").exists():
    time.sleep(0.001)
offsets = Path("/proc/self/timens_offsets").read_bytes()
signature = hashlib.sha256(offsets).hexdigest()
(args.root / f"node{args.node}.txt").write_text(
    f"{time.monotonic_ns()}\t{signature}\n", encoding="utf-8")
