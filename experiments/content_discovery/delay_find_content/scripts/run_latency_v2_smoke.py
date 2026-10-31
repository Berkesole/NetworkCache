#!/usr/bin/env python3
"""Run the finite latency-v2 smoke matrix without background polling."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import collect_success_runs
import classify_latency_v2_run
import generate_latency_v2


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_DIR = SCRIPT_DIR.parent
REPO_ROOT = EXP_DIR.parents[2]
SMOKE_ROOT = EXP_DIR / "results" / "latency-v2-smoke"


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def expected_rows(batch: Path) -> int:
    total = 0
    for row in read_csv(batch / "manifest.csv"):
        metadata = Path(row.get("metadata_path", ""))
        if not metadata.is_absolute():
            metadata = batch / metadata
        total += collect_success_runs.expected_measurement_rows(metadata)
    return total


def batch_complete(batch: Path, repeats: int) -> bool:
    manifest = read_csv(batch / "manifest.csv")
    if len(manifest) != repeats or any(row.get("status") == "driver_failed" for row in manifest):
        return False
    for row in manifest:
        summary = Path(row.get("summary_path", ""))
        if not summary.is_absolute():
            summary = batch / summary
        if not classify_latency_v2_run.has_complete_measurement_trace(summary.parent):
            return False
    return True


def batch_has_infrastructure_failure(batch: Path) -> bool:
    return any(row.get("status") == "driver_failed" for row in read_csv(batch / "manifest.csv"))


def next_batch(case: str) -> Path:
    base = SMOKE_ROOT / case
    if not base.exists():
        return base
    index = 2
    while (SMOKE_ROOT / f"{case}-retry{index}").exists():
        index += 1
    return SMOKE_ROOT / f"{case}-retry{index}"


def wait_seconds(scenario: str) -> int:
    metadata = EXP_DIR / "workloads" / "kua-dc-v3" / "generated" / f"{scenario}.metadata.json"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    return int((int(data["scenario_ticks"]) * 25 + 999) // 1000 + 30)


def validate_clocks(batch: Path) -> None:
    for row in read_csv(batch / "manifest.csv"):
        summary = Path(row.get("summary_path", ""))
        if not summary.is_absolute():
            summary = batch / summary
        run_dir = summary.parent
        subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "validate_trace_clocks.py"), str(run_dir)],
            cwd=REPO_ROOT,
            check=True,
        )


def run_case(case: str, scenario: str, method: str, repeats: int, flush_every: int = 64) -> Path:
    existing = [SMOKE_ROOT / case, *sorted(SMOKE_ROOT.glob(f"{case}-retry*"))]
    for batch in existing:
        if batch_complete(batch, repeats):
            print(f"[{case}] complete smoke already exists: {batch}")
            validate_clocks(batch)
            return batch
    batch = next_batch(case)
    runner = SCRIPT_DIR / ("run_kathara_batch.sh" if method == "nc" else "run_oracle_nc_unicast_batch.sh")
    env = os.environ.copy()
    env.update(
        {
            "RESULTS_ROOT": str(SMOKE_ROOT),
            "RSYNC_EVENT_TRACE": "1",
            "RSYNC_EVENT_TRACE_FLUSH_EVERY": str(flush_every),
            "KATHARA_NODE_LOOP_SLEEP_MS": "25",
            "KATHARA_LINK_DELAY_MS": "2",
            "KATHARA_WAIT_SECONDS": str(wait_seconds(scenario)),
        }
    )
    command = [
        str(runner),
        "--scenarios",
        scenario,
        "--repeats",
        str(repeats),
        "--allow-incomplete",
        "--run-group-id",
        batch.name,
    ]
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)
    subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "diagnose_completion.py"), str(batch)],
        cwd=REPO_ROOT,
        check=True,
    )
    if batch_has_infrastructure_failure(batch):
        raise SystemExit(f"infrastructure failure in smoke batch {batch}")
    validate_clocks(batch)
    if not batch_complete(batch, repeats):
        print(f"[{case}] completed with incomplete rows; formal success20 fallback remains enabled")
    return batch


def scenario(topology: int, workload: str, spacing: int, seed: int = 1) -> str:
    return generate_latency_v2.scenario_name(f"kua-dc-v3-{topology}", workload, spacing, seed)


def main() -> int:
    SMOKE_ROOT.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(SCRIPT_DIR / "generate_latency_v2.py")], cwd=REPO_ROOT, check=True)

    flush_one = run_case("trace-flush1-controlled-16", scenario(16, "controlled", 2000), "nc", 3, 1)
    buffered = run_case("trace-flush64-controlled-16", scenario(16, "controlled", 2000), "nc", 3, 64)
    if batch_complete(flush_one, 3) and batch_complete(buffered, 3):
        subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "compare_trace_overhead.py"), str(flush_one), str(buffered)],
            cwd=REPO_ROOT,
            check=True,
        )

    run_case("uniform-16-nc", scenario(16, "iid-uniform", 2000), "nc", 1)
    run_case("zipf-32-nc", scenario(32, "iid-zipf", 2000), "nc", 3)
    run_case("zipf-32-known-owner", scenario(32, "iid-zipf", 2000), "known-owner", 3)
    run_case("load-32-nc", scenario(32, "iid-zipf", 250), "nc", 1)
    run_case("load-32-known-owner", scenario(32, "iid-zipf", 250), "known-owner", 1)
    print("latency-v2 finite smoke matrix finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
