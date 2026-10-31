#!/usr/bin/env python3
"""Aggregate Oracle Direct Fetch baseline summaries."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


SUMMARY_COLUMNS = [
    "workload_id",
    "workload_type",
    "seed",
    "scenario",
    "env",
    "topology",
    "phase",
    "request_index",
    "request_pair_id",
    "distance_class",
    "expected_cold_provider_hop",
    "expected_warm_cache_hop",
    "requester",
    "provider",
    "key",
    "oracle_fetch_latency_ns",
    "oracle_provider_receive_delay_ns",
    "value_bytes",
    "status",
]
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
MERGED_COLUMNS = [
    "batch_id",
    "run_id",
    "repeat_index",
    "run_status",
    "summary_path",
] + SUMMARY_COLUMNS
METRIC_COLUMNS = [
    "oracle_fetch_latency_ns",
    "oracle_provider_receive_delay_ns",
    "value_bytes",
]
GROUP_COLUMNS = [
    "workload_id",
    "workload_type",
    "seed",
    "scenario",
    "env",
    "topology",
    "phase",
]
DISTANCE_GROUP_COLUMNS = GROUP_COLUMNS + ["distance_class"]
STATS = ["mean", "p95", "stddev", "min", "max"]
AGGREGATE_COLUMNS = (
    GROUP_COLUMNS
    + ["runs", "complete_runs", "incomplete_runs"]
    + [f"{metric}_{stat}" for metric in METRIC_COLUMNS for stat in STATS]
)
DISTANCE_AGGREGATE_COLUMNS = (
    DISTANCE_GROUP_COLUMNS
    + ["runs", "complete_runs", "incomplete_runs"]
    + [f"{metric}_{stat}" for metric in METRIC_COLUMNS for stat in STATS]
)
COMPLETION_COLUMNS = GROUP_COLUMNS + [
    "requests",
    "completed_requests",
    "incomplete_requests",
    "completion_ratio",
]
DISTANCE_COMPLETION_COLUMNS = DISTANCE_GROUP_COLUMNS + [
    "requests",
    "completed_requests",
    "incomplete_requests",
    "completion_ratio",
]


def parse_number(value: str) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def format_number(value: Optional[float]) -> str:
    if value is None or math.isnan(value) or math.isinf(value):
        return ""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def row_is_complete(row: Dict[str, str]) -> bool:
    latency_value = parse_number(row.get("oracle_fetch_latency_ns", ""))
    return row.get("status") == "ok" and latency_value is not None and latency_value >= 0


def classify_summary(path: Path) -> str:
    if not path.exists():
        return "empty"
    rows = read_csv(path)
    if not rows:
        return "empty"
    measurement = [row for row in rows if row.get("phase") == "measurement"]
    checked = measurement if measurement else rows
    return "ok" if all(row_is_complete(row) for row in checked) else "incomplete"


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def resolve_batch_path(batch_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return batch_dir / path


def load_manifest(batch_dir: Path) -> List[Dict[str, str]]:
    manifest = batch_dir / "manifest.csv"
    if manifest.exists():
        return read_csv(manifest)
    rows: List[Dict[str, str]] = []
    for summary in sorted((batch_dir / "runs").glob("*/oracle_summary.csv")):
        run_dir = summary.parent
        rows.append(
            {
                "run_id": run_dir.name,
                "scenario": "",
                "repeat_index": "",
                "status": classify_summary(summary),
                "summary_path": str(summary.relative_to(batch_dir)),
                "metadata_path": str((run_dir / "metadata.json").relative_to(batch_dir)),
                "started_at": "",
                "finished_at": "",
            }
        )
    return rows


def merged_rows(batch_dir: Path, manifest_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    output: List[Dict[str, str]] = []
    for manifest in manifest_rows:
        summary_path = resolve_batch_path(batch_dir, manifest.get("summary_path", ""))
        for row in read_csv(summary_path):
            output.append(
                {
                    "batch_id": batch_dir.name,
                    "run_id": manifest.get("run_id", ""),
                    "repeat_index": manifest.get("repeat_index", ""),
                    "run_status": manifest.get("status", ""),
                    "summary_path": manifest.get("summary_path", ""),
                    **row,
                }
            )
    return output


def group_key(row: Dict[str, str], columns: Sequence[str]) -> Tuple[str, ...]:
    return tuple(row.get(column, "") for column in columns)


def aggregate(rows: List[Dict[str, str]], group_columns: Sequence[str]) -> List[Dict[str, str]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[group_key(row, group_columns)].append(row)

    output: List[Dict[str, str]] = []
    for key, group in sorted(groups.items()):
        complete = [row for row in group if row_is_complete(row)]
        record = {column: value for column, value in zip(group_columns, key)}
        record["runs"] = str(len(group))
        record["complete_runs"] = str(len(complete))
        record["incomplete_runs"] = str(len(group) - len(complete))
        for metric in METRIC_COLUMNS:
            values = [
                number
                for row in complete
                if (number := parse_number(row.get(metric, ""))) is not None
            ]
            record[f"{metric}_mean"] = format_number(statistics.fmean(values) if values else None)
            record[f"{metric}_p95"] = format_number(percentile(values, 0.95))
            record[f"{metric}_stddev"] = format_number(statistics.stdev(values) if len(values) > 1 else 0 if values else None)
            record[f"{metric}_min"] = format_number(min(values) if values else None)
            record[f"{metric}_max"] = format_number(max(values) if values else None)
        output.append(record)
    return output


def completion(rows: List[Dict[str, str]], group_columns: Sequence[str]) -> List[Dict[str, str]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[group_key(row, group_columns)].append(row)
    output: List[Dict[str, str]] = []
    for key, group in sorted(groups.items()):
        completed = sum(1 for row in group if row_is_complete(row))
        record = {column: value for column, value in zip(group_columns, key)}
        record["requests"] = str(len(group))
        record["completed_requests"] = str(completed)
        record["incomplete_requests"] = str(len(group) - completed)
        record["completion_ratio"] = format_number(completed / len(group) if group else None)
        output.append(record)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_dir = args.batch_dir
    manifest = load_manifest(batch_dir)
    if not manifest:
        raise SystemExit(f"no oracle manifest rows found in {batch_dir}")

    merged = merged_rows(batch_dir, manifest)
    measurement = [row for row in merged if row.get("phase") == "measurement"]

    write_csv(batch_dir / "manifest.csv", MANIFEST_COLUMNS, manifest)
    write_csv(batch_dir / "oracle_merged.csv", MERGED_COLUMNS, merged)
    write_csv(batch_dir / "oracle_merged_measurement.csv", MERGED_COLUMNS, measurement)
    write_csv(batch_dir / "oracle_aggregate.csv", AGGREGATE_COLUMNS, aggregate(measurement, GROUP_COLUMNS))
    write_csv(
        batch_dir / "oracle_aggregate_by_distance.csv",
        DISTANCE_AGGREGATE_COLUMNS,
        aggregate(measurement, DISTANCE_GROUP_COLUMNS),
    )
    write_csv(batch_dir / "oracle_completion.csv", COMPLETION_COLUMNS, completion(measurement, GROUP_COLUMNS))
    write_csv(
        batch_dir / "oracle_completion_by_distance.csv",
        DISTANCE_COMPLETION_COLUMNS,
        completion(measurement, DISTANCE_GROUP_COLUMNS),
    )
    print(f"merged_rows={len(merged)}")
    print(f"measurement_rows={len(measurement)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
