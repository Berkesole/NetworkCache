#!/usr/bin/env python3
"""Run the four reduced 88-container federation-v2 smoke configurations."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[2]


def next_dir(root: Path, slug: str) -> Path:
    index = 1
    while (candidate := root / f"{slug}-{index:03d}").exists(): index += 1
    return candidate


def validate(path: Path, method: str) -> None:
    with (path / "request_results.csv").open(newline="") as handle: rows = list(csv.DictReader(handle))
    if len(rows) != 80 or any(row["status"] != "ok" for row in rows): raise RuntimeError(f"request smoke failed {path}")
    with (path / "isolation_results.csv").open(newline="") as handle: isolation = list(csv.DictReader(handle))
    if len(isolation) != 2 or any(row["status"] != "ok" for row in isolation): raise RuntimeError(f"isolation smoke failed {path}")
    summary = json.loads((path / "completion_summary.json").read_text())
    if summary["completed"] != 80: raise RuntimeError(f"completion smoke failed {path}")
    if method != "tcp-direct":
        with (path / "cache_occupancy.csv").open(newline="") as handle: occupancy = list(csv.DictReader(handle))
        if len(occupancy) != 512: raise RuntimeError(f"occupancy smoke failed {path}: {len(occupancy)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=EXP / "results/federation-v2-smoke")
    parser.add_argument("--only", action="append", choices=("repeated-nc", "repeated-known-owner",
                        "repeated-tcp-direct", "uniform-nc"), help="run only the named smoke; repeatable")
    args = parser.parse_args(); args.output_root.mkdir(parents=True, exist_ok=True)
    inputs = args.output_root / "inputs"
    subprocess.run([sys.executable, str(HERE / "prepare_federation_v2_smoke.py"), "--output-root", str(inputs)], check=True)
    configs = (("repeated", "nc"), ("repeated", "known-owner"), ("repeated", "tcp-direct"), ("uniform", "nc"))
    for workload, method in configs:
        slug = f"{workload}-{method}"
        if args.only and slug not in args.only: continue
        scenario = f"kua-dc-v3-federation-v2-smoke-{workload}-seed001"
        output = next_dir(args.output_root, slug)
        env = os.environ.copy(); env["FEDERATION_V2_WORKLOAD_ROOT"] = str(inputs)
        env["FEDERATION_V2_BUILD_NO_CACHE"] = "0"
        completed = subprocess.run([str(HERE / "run_federation_v2_once.sh"), scenario, method, str(output)],
                                   cwd=REPO, env=env, check=False)
        if completed.returncode: raise RuntimeError(f"smoke failed: {scenario} {method} {output}")
        validate(output, method)
        print(f"SMOKE READY {slug} {output}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
