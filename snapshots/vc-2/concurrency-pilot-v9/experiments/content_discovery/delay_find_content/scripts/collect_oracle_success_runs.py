#!/usr/bin/env python3
"""Build a success-only Oracle Direct Fetch batch from attempt batches."""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


MANIFEST_COLUMNS = [
    "run_id",
    "scenario",
    "repeat_index",
    "status",
    "summary_path",
    "metadata_path",
    "started_at",
    "finished_at",
    "workload_id",
    "workload_type",
    "topology",
    "seed",
    "request_count",
    "key_count",
    "provider_count",
    "requester_count",
    "phase_policy",
]


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def resolve(batch_dir: Path, value: str) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((batch_dir / path).resolve())


def load_ok_rows(batch_dirs: Sequence[Path], scenarios: Sequence[str]) -> Dict[str, List[Dict[str, str]]]:
    wanted = set(scenarios)
    rows_by_scenario: Dict[str, List[Dict[str, str]]] = {scenario: [] for scenario in scenarios}
    for batch_dir in batch_dirs:
        manifest = batch_dir / "manifest.csv"
        if not manifest.exists():
            raise SystemExit(f"missing manifest: {manifest}")
        for row in read_csv(manifest):
            scenario = row.get("scenario", "")
            if scenario not in wanted or row.get("status") != "ok":
                continue
            output = dict(row)
            output["summary_path"] = resolve(batch_dir, row.get("summary_path", ""))
            output["metadata_path"] = resolve(batch_dir, row.get("metadata_path", ""))
            rows_by_scenario[scenario].append(output)
    return rows_by_scenario


def build_success_manifest(
    rows_by_scenario: Dict[str, List[Dict[str, str]]],
    scenarios: Sequence[str],
    target_per_scenario: int,
) -> List[Dict[str, str]]:
    output_rows: List[Dict[str, str]] = []
    for scenario in scenarios:
        candidates = rows_by_scenario.get(scenario, [])
        if len(candidates) < target_per_scenario:
            raise SystemExit(
                f"scenario {scenario} has {len(candidates)} ok oracle runs, need {target_per_scenario}"
            )
        for success_index, row in enumerate(candidates[:target_per_scenario], start=1):
            output = dict(row)
            output["run_id"] = f"{scenario}-oracle-success{success_index}"
            output["repeat_index"] = str(success_index)
            output["status"] = "ok"
            output_rows.append(output)
    return output_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-per-scenario", type=int, default=20)
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("batch_dirs", nargs="+", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenarios = [part.strip() for part in args.scenarios.split(",") if part.strip()]
    if not scenarios:
        raise SystemExit("--scenarios must not be empty")
    if args.target_per_scenario <= 0:
        raise SystemExit("--target-per-scenario must be positive")
    if args.output.exists():
        if not args.overwrite:
            raise SystemExit(f"refusing to overwrite existing output: {args.output}")
        shutil.rmtree(args.output)

    rows_by_scenario = load_ok_rows(args.batch_dirs, scenarios)
    manifest_rows = build_success_manifest(rows_by_scenario, scenarios, args.target_per_scenario)
    write_csv(args.output / "manifest.csv", MANIFEST_COLUMNS, manifest_rows)

    aggregate_script = Path(__file__).with_name("aggregate_oracle_summaries.py")
    subprocess.run([sys.executable, str(aggregate_script), str(args.output)], check=True)
    trace_aggregate_script = Path(__file__).with_name("aggregate_trace_breakdowns.py")
    subprocess.run([sys.executable, str(trace_aggregate_script), str(args.output)], check=True)

    for scenario in scenarios:
        print(f"{scenario}: selected {args.target_per_scenario} ok oracle runs")
    print(f"oracle success batch: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
