#!/usr/bin/env python3
"""Aggregate content-discovery Docker batch summaries.

This script consumes the per-run summary.csv files produced by analyze_logs.py.
It uses only Python stdlib so the experiment pipeline stays portable.
"""

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
    "phase",
    "request_index",
    "is_first_key_request",
    "request_pair_id",
    "distance_class",
    "expected_cold_provider_hop",
    "expected_warm_cache_hop",
    "scenario",
    "env",
    "topology",
    "warm_type",
    "requester",
    "provider",
    "key",
    "discovery_delay_ns",
    "return_delay_ns",
    "miss_latency_ns",
    "request_packets",
    "data_packets",
    "aggregate_flushes",
    "sync_table_hits",
    "sync_table_misses",
    "provider_hit_hop",
]

CORE_LATENCY_COLUMNS = [
    "discovery_delay_ns",
    "return_delay_ns",
    "miss_latency_ns",
]
COMPLETION_REQUIRED_COLUMNS = CORE_LATENCY_COLUMNS + ["provider_hit_hop"]

METRIC_COLUMNS = [
    "discovery_delay_ns",
    "return_delay_ns",
    "miss_latency_ns",
    "request_packets",
    "data_packets",
    "aggregate_flushes",
    "sync_table_hits",
    "sync_table_misses",
    "provider_hit_hop",
]

GROUP_COLUMNS = [
    "workload_id",
    "workload_type",
    "seed",
    "scenario",
    "env",
    "topology",
    "phase",
    "warm_type",
]
DISTANCE_GROUP_COLUMNS = [
    "workload_id",
    "workload_type",
    "seed",
    "scenario",
    "env",
    "topology",
    "phase",
    "distance_class",
    "warm_type",
]
COMPARISON_KEY_COLUMNS = ["workload_id", "workload_type", "seed", "scenario", "env", "topology", "phase"]
DISTANCE_COMPARISON_KEY_COLUMNS = [
    "workload_id",
    "workload_type",
    "seed",
    "scenario",
    "env",
    "topology",
    "phase",
    "distance_class",
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
STATS = ["mean", "median", "p95", "stddev", "min", "max"]
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
COMPARISON_COLUMNS = [
    "workload_id",
    "workload_type",
    "seed",
    "scenario",
    "env",
    "topology",
    "phase",
    "metric",
    "cold_median",
    "path_materialization_median",
    "path_over_cold_ratio",
    "path_minus_cold_delta",
]
DISTANCE_COMPARISON_COLUMNS = [
    "workload_id",
    "workload_type",
    "seed",
    "scenario",
    "env",
    "topology",
    "phase",
    "distance_class",
    "metric",
    "cold_median",
    "path_materialization_median",
    "path_over_cold_ratio",
    "path_minus_cold_delta",
]
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
    if value is None:
        return ""
    if math.isnan(value) or math.isinf(value):
        return ""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def row_is_complete(row: Dict[str, str]) -> bool:
    for column in COMPLETION_REQUIRED_COLUMNS:
        number = parse_number(row.get(column, ""))
        if number is None or number < 0:
            return False
    return True


def classify_summary(path: Path) -> str:
    if not path.exists():
        return "empty"
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return "empty"
    measurement_rows = [row for row in rows if row.get("phase") == "measurement"]
    status_rows = measurement_rows if measurement_rows else rows
    return "ok" if all(row_is_complete(row) for row in status_rows) else "incomplete"


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
    for summary in sorted((batch_dir / "runs").glob("*/summary.csv")):
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


def load_merged_rows(batch_dir: Path, manifest_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    batch_id = batch_dir.name
    merged: List[Dict[str, str]] = []
    for manifest_row in manifest_rows:
        summary_value = manifest_row.get("summary_path", "")
        if not summary_value:
            continue
        summary_path = resolve_batch_path(batch_dir, summary_value)
        for summary_row in read_csv(summary_path):
            merged_row = {
                "batch_id": batch_id,
                "run_id": manifest_row.get("run_id", ""),
                "repeat_index": manifest_row.get("repeat_index", ""),
                "run_status": manifest_row.get("status", ""),
                "summary_path": summary_value,
            }
            for column in SUMMARY_COLUMNS:
                merged_row[column] = summary_row.get(column, "")
            merged.append(merged_row)
    return merged


def nearest_rank_p95(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def metric_stats(values: Sequence[float]) -> Dict[str, str]:
    if not values:
        return {stat: "" for stat in STATS}
    ordered = sorted(values)
    return {
        "mean": format_number(statistics.mean(ordered)),
        "median": format_number(statistics.median(ordered)),
        "p95": format_number(nearest_rank_p95(ordered)),
        "stddev": format_number(statistics.stdev(ordered) if len(ordered) > 1 else 0.0),
        "min": format_number(ordered[0]),
        "max": format_number(ordered[-1]),
    }


def build_aggregate_rows(
    merged_rows: List[Dict[str, str]],
    group_columns: Sequence[str] = GROUP_COLUMNS,
) -> List[Dict[str, str]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in merged_rows:
        key = tuple(row.get(column, "") for column in group_columns)
        groups[key].append(row)

    aggregate_rows: List[Dict[str, str]] = []
    for key in sorted(groups):
        rows = groups[key]
        output = {column: value for column, value in zip(group_columns, key)}
        complete_runs = sum(1 for row in rows if row_is_complete(row))
        output["runs"] = str(len(rows))
        output["complete_runs"] = str(complete_runs)
        output["incomplete_runs"] = str(len(rows) - complete_runs)

        for metric in METRIC_COLUMNS:
            values = [
                number
                for number in (parse_number(row.get(metric, "")) for row in rows)
                if number is not None and number >= 0
            ]
            for stat, value in metric_stats(values).items():
                output[f"{metric}_{stat}"] = value
        aggregate_rows.append(output)
    return aggregate_rows


def median_for(rows: Sequence[Dict[str, str]], metric: str) -> Optional[float]:
    values = [
        number
        for number in (parse_number(row.get(metric, "")) for row in rows)
        if number is not None and number >= 0
    ]
    if not values:
        return None
    return float(statistics.median(sorted(values)))


def build_comparison_rows(
    merged_rows: List[Dict[str, str]],
    comparison_key_columns: Sequence[str] = COMPARISON_KEY_COLUMNS,
) -> List[Dict[str, str]]:
    groups: Dict[Tuple[str, ...], Dict[str, List[Dict[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in merged_rows:
        key = tuple(row.get(column, "") for column in comparison_key_columns)
        groups[key][row.get("warm_type", "")].append(row)

    comparison_rows: List[Dict[str, str]] = []
    for key in sorted(groups):
        warm_groups = groups[key]
        cold_rows = warm_groups.get("cold", [])
        path_rows = warm_groups.get("path_materialization", [])
        if not cold_rows or not path_rows:
            continue
        base = {column: value for column, value in zip(comparison_key_columns, key)}
        for metric in METRIC_COLUMNS:
            cold_median = median_for(cold_rows, metric)
            path_median = median_for(path_rows, metric)
            if cold_median is None or path_median is None:
                continue
            ratio = None if cold_median == 0 else path_median / cold_median
            output = dict(base)
            output.update(
                {
                    "metric": metric,
                    "cold_median": format_number(cold_median),
                    "path_materialization_median": format_number(path_median),
                    "path_over_cold_ratio": format_number(ratio),
                    "path_minus_cold_delta": format_number(path_median - cold_median),
                }
            )
            comparison_rows.append(output)
    return comparison_rows


def build_completion_rows(
    merged_rows: List[Dict[str, str]],
    group_columns: Sequence[str] = GROUP_COLUMNS,
) -> List[Dict[str, str]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in merged_rows:
        key = tuple(row.get(column, "") for column in group_columns)
        groups[key].append(row)

    completion_rows: List[Dict[str, str]] = []
    for key in sorted(groups):
        rows = groups[key]
        completed = sum(1 for row in rows if row_is_complete(row))
        total = len(rows)
        output = {column: value for column, value in zip(group_columns, key)}
        output.update(
            {
                "requests": str(total),
                "completed_requests": str(completed),
                "incomplete_requests": str(total - completed),
                "completion_ratio": format_number(completed / total if total else None),
            }
        )
        completion_rows.append(output)
    return completion_rows


def aggregate_batch(batch_dir: Path) -> None:
    manifest_rows = load_manifest(batch_dir)
    merged_rows = load_merged_rows(batch_dir, manifest_rows)
    write_csv(batch_dir / "merged.csv", MERGED_COLUMNS, merged_rows)
    measurement_rows = [row for row in merged_rows if row.get("phase") == "measurement"]
    stats_rows = measurement_rows if measurement_rows else merged_rows
    if measurement_rows:
        write_csv(batch_dir / "merged_measurement.csv", MERGED_COLUMNS, measurement_rows)
    write_csv(batch_dir / "aggregate.csv", AGGREGATE_COLUMNS, build_aggregate_rows(stats_rows))
    write_csv(batch_dir / "comparison.csv", COMPARISON_COLUMNS, build_comparison_rows(stats_rows))
    write_csv(batch_dir / "completion.csv", COMPLETION_COLUMNS, build_completion_rows(stats_rows))
    if any(row.get("distance_class", "") for row in stats_rows):
        write_csv(
            batch_dir / "aggregate_by_distance.csv",
            DISTANCE_AGGREGATE_COLUMNS,
            build_aggregate_rows(stats_rows, DISTANCE_GROUP_COLUMNS),
        )
        write_csv(
            batch_dir / "comparison_by_distance.csv",
            DISTANCE_COMPARISON_COLUMNS,
            build_comparison_rows(stats_rows, DISTANCE_COMPARISON_KEY_COLUMNS),
        )
        write_csv(
            batch_dir / "completion_by_distance.csv",
            DISTANCE_COMPLETION_COLUMNS,
            build_completion_rows(stats_rows, DISTANCE_GROUP_COLUMNS),
        )
    print(f"wrote {len(merged_rows)} merged rows under {batch_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", nargs="?", type=Path)
    parser.add_argument("--classify-summary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.classify_summary:
        print(classify_summary(args.classify_summary))
        return 0
    if args.batch_dir is None:
        raise SystemExit("batch_dir is required unless --classify-summary is used")
    aggregate_batch(args.batch_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
