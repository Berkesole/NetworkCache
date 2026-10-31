#!/usr/bin/env python3
"""Merge and aggregate per-run trace_breakdown.csv files for a batch directory."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


GROUP_COLUMNS = [
    "scenario",
    "env",
    "topology",
    "phase",
    "warm_type",
    "distance_class",
    "service_class",
]
METRIC_COLUMNS = [
    "request_to_send_ns",
    "tcp_send_ns",
    "send_to_handle_ns",
    "provider_lookup_ns",
    "request_to_hit_ns",
    "data_send_ns",
    "data_to_app_handle_ns",
    "app_delivery_ns",
    "return_delay_ns",
    "miss_latency_ns",
    "provider_hit_hop",
]
STATS = ["count", "mean", "median", "p95", "stddev", "min", "max"]


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def resolve(batch_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (batch_dir / path).resolve()


def trace_path_for_manifest_row(batch_dir: Path, row: Dict[str, str]) -> Optional[Path]:
    summary = row.get("summary_path", "")
    if not summary:
        return None
    summary_path = resolve(batch_dir, summary)
    candidate = summary_path.with_name("trace_breakdown.csv")
    return candidate if candidate.exists() else None


def parse_number(value: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def format_number(value: float) -> str:
    if math.isnan(value) or math.isinf(value):
        return ""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def aggregate(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(column, "") for column in GROUP_COLUMNS)].append(row)
    output: List[Dict[str, str]] = []
    for key, group_rows in sorted(groups.items()):
        row = {column: key[index] for index, column in enumerate(GROUP_COLUMNS)}
        for metric in METRIC_COLUMNS:
            values = [
                number
                for number in (parse_number(group_row.get(metric, "")) for group_row in group_rows)
                if number is not None
            ]
            row[f"{metric}_count"] = str(len(values))
            if values:
                row[f"{metric}_mean"] = format_number(statistics.fmean(values))
                row[f"{metric}_median"] = format_number(statistics.median(values))
                row[f"{metric}_p95"] = format_number(p95(values))
                row[f"{metric}_stddev"] = format_number(statistics.pstdev(values))
                row[f"{metric}_min"] = format_number(min(values))
                row[f"{metric}_max"] = format_number(max(values))
            else:
                for stat in STATS[1:]:
                    row[f"{metric}_{stat}"] = ""
        output.append(row)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_dir = args.batch_dir
    manifest = batch_dir / "manifest.csv"
    if not manifest.exists():
        raise SystemExit(f"missing manifest: {manifest}")

    rows: List[Dict[str, str]] = []
    output_columns: List[str] = []
    for manifest_row in read_csv(manifest):
        trace_path = trace_path_for_manifest_row(batch_dir, manifest_row)
        if trace_path is None:
            continue
        for trace_row in read_csv(trace_path):
            row = {
                "batch_id": batch_dir.name,
                "run_id": manifest_row.get("run_id", ""),
                "repeat_index": manifest_row.get("repeat_index", ""),
                "run_status": manifest_row.get("status", ""),
                "trace_path": str(trace_path),
                **trace_row,
            }
            rows.append(row)
            if not output_columns:
                output_columns = list(row.keys())

    merged_path = batch_dir / "trace_breakdown_merged.csv"
    aggregate_path = batch_dir / "trace_breakdown_by_class.csv"
    if rows:
        write_csv(merged_path, output_columns, rows)
    else:
        write_csv(merged_path, ["batch_id", "run_id", "trace_path"], [])

    aggregate_columns = GROUP_COLUMNS + [
        f"{metric}_{stat}" for metric in METRIC_COLUMNS for stat in STATS
    ]
    write_csv(aggregate_path, aggregate_columns, aggregate(rows))
    print(f"merged trace rows: {len(rows)} -> {merged_path}")
    print(f"aggregate: {aggregate_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
