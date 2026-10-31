#!/usr/bin/env python3
"""Run and validate the isolated 88-container staged-fallback smoke."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import run_federation_v3_bf1024_h3_fallback_suite as suite


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[2]
INPUTS = EXP / "workloads/federation-v3-bf1024-h3-fallback-pilot-smoke"
RESULTS = Path(os.environ.get(
    "FEDERATION_FALLBACK_RESULTS_ROOT",
    str(EXP / "results/federation-v3-bf1024-h3-fallback-pilot"),
)).resolve()
OUTPUT = RESULTS / "smoke/run001"
SCENARIO = "kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot-smoke-catalog512-zipf-nc-seed001"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def validate() -> tuple[bool, str]:
    try:
        requests = rows(OUTPUT / "request_results.csv")
        trace = rows(OUTPUT / "trace_breakdown.csv")
        summary = json.loads((OUTPUT / "completion_summary.json").read_text(encoding="utf-8"))
        metadata = json.loads((OUTPUT / "metadata.json").read_text(encoding="utf-8"))
        probes = rows(OUTPUT / "isolation_results.csv")
        occupancy = rows(OUTPUT / "cache_occupancy.csv")
        bloom = rows(OUTPUT / "sync_bloom_state.csv")
    except Exception:
        return False, "missing_or_invalid"
    if len(requests) != 224 or any(row.get("status") != "ok" for row in requests):
        return False, "requests"
    if summary.get("scheduled") != 224 or summary.get("completed") != 224:
        return False, "completion"
    if (metadata.get("wire_version") != 3 or metadata.get("bloom_bits") != 1024 or
            metadata.get("bloom_hash_count") != 3 or metadata.get("staged_fallback") is not True):
        return False, "parameters"
    if not suite.commit_satisfies_required(metadata):
        return False, "git_commit_precedes_required_postfix"
    if len(probes) != 2 or any(row.get("status") != "ok" for row in probes):
        return False, "isolation"
    if len(trace) != 224 or any(int(row.get("query_expired_unsatisfied_events") or 0) for row in trace):
        return False, "trace"
    if any(int(row.get("application_delivery_events") or 0) != 1 for row in trace):
        return False, "application_delivery"
    if len(occupancy) != 5 * 64 or not bloom:
        return False, "snapshots"
    return True, "ok"


def main() -> int:
    subprocess.run([sys.executable, str(HERE / "generate_federation_v3_bf1024_h3_fallback_pilot.py")],
                   cwd=REPO, check=True)
    subprocess.run([sys.executable, str(HERE / "prepare_federation_v3_bf1024_h3_fallback_smoke.py")],
                   cwd=REPO, check=True)
    if OUTPUT.exists():
        ok, reason = validate()
        if ok:
            print(f"SMOKE READY {OUTPUT}")
            return 0
        raise RuntimeError(f"existing smoke is not valid ({reason}); preserved at {OUTPUT}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["FEDERATION_V3_FALLBACK_WORKLOAD_ROOT"] = str(INPUTS)
    env["FEDERATION_V3_FALLBACK_BUILD_NO_CACHE"] = "0"
    completed = subprocess.run(
        [str(HERE / "run_federation_v3_bf1024_h3_fallback_once.sh"), SCENARIO, str(OUTPUT)],
        cwd=REPO, env=env, check=False,
    )
    if completed.returncode:
        return completed.returncode
    ok, reason = validate()
    if not ok:
        raise RuntimeError(f"smoke quality gate failed: {reason}")
    print(f"SMOKE READY {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
