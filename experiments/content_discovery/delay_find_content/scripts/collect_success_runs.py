#!/usr/bin/env python3
"""Build a success-only batch from existing content-discovery batch results."""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Sequence
import json


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


def expected_measurement_rows(metadata_path: Path) -> int:
    candidate_paths = [metadata_path]
    if metadata_path.name != "schedule_metadata.json":
        candidate_paths.append(metadata_path.with_name("schedule_metadata.json"))
    for path in candidate_paths:
        expected = expected_measurement_rows_from_file(path)
        if expected > 0:
            return expected
    return 0


def expected_measurement_rows_from_file(metadata_path: Path) -> int:
    if not metadata_path.exists():
        return 0
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    value = data.get("measurement_requests")
    if isinstance(value, int):
        return value
    requests = data.get("requests")
    if isinstance(requests, list):
        return sum(1 for request in requests if request.get("phase") == "measurement")
    return 0


def has_complete_measurement_trace(row: Dict[str, str]) -> bool:
    metadata_path = Path(row.get("metadata_path", ""))
    expected = expected_measurement_rows(metadata_path)
    if expected <= 0:
        return True
    summary_path = Path(row.get("summary_path", ""))
    trace_path = summary_path.with_name("trace_breakdown.csv")
    if not trace_path.exists():
        return False
    complete = 0
    for trace_row in read_csv(trace_path):
        if (
            trace_row.get("phase") == "measurement"
            and trace_row.get("trace_complete") == "1"
            and trace_row.get("miss_latency_ns")
        ):
            complete += 1
    return complete >= expected


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
            if not has_complete_measurement_trace(output):
                continue
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
                f"scenario {scenario} has {len(candidates)} ok runs, need {target_per_scenario}"
            )
        for success_index, row in enumerate(candidates[:target_per_scenario], start=1):
            output = dict(row)
            output["run_id"] = f"{scenario}-success{success_index}"
            output["repeat_index"] = str(success_index)
            output["status"] = "ok"
            output_rows.append(output)
    return output_rows


def build_balanced_total_manifest(
    rows_by_scenario: Dict[str, List[Dict[str, str]]],
    scenarios: Sequence[str],
    target_total: int,
    preferred_per_scenario: int,
) -> List[Dict[str, str]]:
    missing = [scenario for scenario in scenarios if not rows_by_scenario.get(scenario)]
    if missing:
        raise SystemExit(f"no trace-complete run available for required seed scenario(s): {missing}")
    selected: Dict[str, List[Dict[str, str]]] = {scenario: [] for scenario in scenarios}
    if all(len(rows_by_scenario.get(scenario, [])) >= preferred_per_scenario for scenario in scenarios):
        for scenario in scenarios:
            selected[scenario] = rows_by_scenario[scenario][:preferred_per_scenario]
    else:
        offsets = {scenario: 0 for scenario in scenarios}
        while sum(len(rows) for rows in selected.values()) < target_total:
            progressed = False
            for scenario in scenarios:
                candidates = rows_by_scenario.get(scenario, [])
                offset = offsets[scenario]
                if offset >= len(candidates):
                    continue
                selected[scenario].append(candidates[offset])
                offsets[scenario] += 1
                progressed = True
                if sum(len(rows) for rows in selected.values()) >= target_total:
                    break
            if not progressed:
                break
    if sum(len(rows) for rows in selected.values()) < target_total:
        counts = {scenario: len(rows_by_scenario.get(scenario, [])) for scenario in scenarios}
        raise SystemExit(f"only {sum(counts.values())} trace-complete runs available, need {target_total}: {counts}")

    output_rows: List[Dict[str, str]] = []
    for scenario in scenarios:
        for success_index, row in enumerate(selected[scenario], start=1):
            output = dict(row)
            output["run_id"] = f"{scenario}-success{success_index}"
            output["repeat_index"] = str(success_index)
            output["status"] = "ok"
            output_rows.append(output)
    return output_rows[:target_total]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Output synthetic batch directory.")
    parser.add_argument("--target-per-scenario", type=int, default=30)
    parser.add_argument("--target-total", type=int)
    parser.add_argument("--preferred-per-scenario", type=int, default=4)
    parser.add_argument("--scenarios", required=True, help="Comma-separated scenarios to collect.")
    parser.add_argument("batch_dirs", nargs="+", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenarios = [part.strip() for part in args.scenarios.split(",") if part.strip()]
    if not scenarios:
        raise SystemExit("--scenarios must not be empty")
    if args.target_per_scenario <= 0:
        raise SystemExit("--target-per-scenario must be positive")
    if args.target_total is not None and args.target_total <= 0:
        raise SystemExit("--target-total must be positive")
    if args.output.exists():
        if not args.overwrite:
            raise SystemExit(f"refusing to overwrite existing output: {args.output}")
        shutil.rmtree(args.output)

    rows_by_scenario = load_ok_rows(args.batch_dirs, scenarios)
    manifest_rows = (
        build_balanced_total_manifest(
            rows_by_scenario,
            scenarios,
            args.target_total,
            args.preferred_per_scenario,
        )
        if args.target_total is not None
        else build_success_manifest(rows_by_scenario, scenarios, args.target_per_scenario)
    )
    write_csv(args.output / "manifest.csv", MANIFEST_COLUMNS, manifest_rows)

    aggregate_script = Path(__file__).with_name("aggregate_summaries.py")
    subprocess.run([sys.executable, str(aggregate_script), str(args.output)], check=True)
    trace_aggregate_script = Path(__file__).with_name("aggregate_trace_breakdowns.py")
    subprocess.run([sys.executable, str(trace_aggregate_script), str(args.output)], check=True)
    convergence_script = Path(__file__).with_name("aggregate_convergence.py")
    subprocess.run([sys.executable, str(convergence_script), str(args.output)], check=True)

    selected_counts = Counter(row.get("scenario", "") for row in manifest_rows)
    for scenario in scenarios:
        print(f"{scenario}: selected {selected_counts[scenario]} trace-complete runs")
    print(f"success batch: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
