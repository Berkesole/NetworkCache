#!/usr/bin/env python3
"""Run the single 10k-catalog, 88-container short smoke."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[2]
INPUTS = EXP / "workloads/federation-v3-pilot-smoke"
OUTPUT = EXP / "results/federation-v3-pilot/smoke/10k-zipf-run001"
SCENARIO = "kua-dc-v3-federation-v3-pilot-smoke-catalog10000-zipf-nc-seed001"


def validate() -> tuple[bool, str]:
    try:
        with (OUTPUT / "request_results.csv").open(newline="") as handle: rows = list(csv.DictReader(handle))
        summary = json.loads((OUTPUT / "completion_summary.json").read_text())
        with (OUTPUT / "isolation_results.csv").open(newline="") as handle: probes = list(csv.DictReader(handle))
        with (OUTPUT / "cache_occupancy.csv").open(newline="") as handle: occupancy = list(csv.DictReader(handle))
        with (OUTPUT / "sync_bloom_state.csv").open(newline="") as handle: bloom = list(csv.DictReader(handle))
    except Exception:
        return False, "missing_or_invalid"
    if len(rows) != 224 or any(row.get("status") != "ok" for row in rows): return False, "requests"
    if summary.get("completed") != 224: return False, "completion"
    if len(probes) != 2 or any(row.get("status") != "ok" for row in probes): return False, "isolation"
    if len(occupancy) != 5 * 64 or not bloom: return False, "snapshots"
    return True, "ok"


def main() -> int:
    subprocess.run([sys.executable, str(HERE / "generate_federation_v3_pilot.py")], cwd=REPO, check=True)
    subprocess.run([sys.executable, str(HERE / "prepare_federation_v3_pilot_smoke.py")], cwd=REPO, check=True)
    if OUTPUT.exists():
        ok, reason = validate()
        if ok:
            print(f"SMOKE READY {OUTPUT}")
            return 0
        raise RuntimeError(f"existing smoke is not valid ({reason}); preserved at {OUTPUT}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy(); env["FEDERATION_V3_WORKLOAD_ROOT"] = str(INPUTS)
    env["FEDERATION_V3_BUILD_NO_CACHE"] = "0"
    completed = subprocess.run([str(HERE / "run_federation_v3_pilot_once.sh"), SCENARIO, str(OUTPUT)],
                               cwd=REPO, env=env, check=False)
    if completed.returncode: return completed.returncode
    ok, reason = validate()
    if not ok: raise RuntimeError(f"smoke quality gate failed: {reason}")
    print(f"SMOKE READY {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
