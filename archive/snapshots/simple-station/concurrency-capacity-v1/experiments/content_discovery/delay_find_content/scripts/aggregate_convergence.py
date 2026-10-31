#!/usr/bin/env python3
"""Aggregate latency-convergence trace rows for a batch directory."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


METRIC_COLUMNS = [
    "discovery_delay_ns",
    "return_delay_ns",
    "miss_latency_ns",
    "request_to_hit_ns",
    "request_packets",
    "data_packets",
    "aggregate_flushes",
    "provider_hit_hop",
]
STATS = ["count", "mean", "median", "p95", "stddev", "min", "max"]
SUMMARY_COLUMNS = [
    "batch_id",
    "scenario",
    "env",
    "topology",
    "workload_id",
    "workload_type",
    "rows",
    "complete_rows",
    "incomplete_rows",
    "cold_start_mean",
    "cold_start_mean_ns",
    "steady_state_mean",
    "steady_state_mean_ns",
    "steady_state_rows",
    "converged",
    "requests_to_converge",
    "time_to_converge_ms",
    "convergence_threshold_ns",
    "rolling_window_requests",
    "convergence_required_windows",
]


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def parse_number(value: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def format_number(value: Optional[float]) -> str:
    if value is None or math.isnan(value) or math.isinf(value):
        return ""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def is_complete(row: Dict[str, str]) -> bool:
    return row.get("trace_complete") == "1" and bool(row.get("miss_latency_ns"))


def metric_stats(rows: Sequence[Dict[str, str]]) -> Dict[str, str]:
    output: Dict[str, str] = {
        "rows": str(len(rows)),
        "complete_rows": str(sum(1 for row in rows if is_complete(row))),
        "incomplete_rows": str(sum(1 for row in rows if not is_complete(row))),
    }
    complete_rows = [row for row in rows if is_complete(row)]
    for metric in METRIC_COLUMNS:
        values = [
            value
            for value in (parse_number(row.get(metric, "")) for row in complete_rows)
            if value is not None
        ]
        output[f"{metric}_count"] = str(len(values))
        if values:
            output[f"{metric}_mean"] = format_number(statistics.fmean(values))
            output[f"{metric}_median"] = format_number(statistics.median(values))
            output[f"{metric}_p95"] = format_number(p95(values))
            output[f"{metric}_stddev"] = format_number(statistics.pstdev(values))
            output[f"{metric}_min"] = format_number(min(values))
            output[f"{metric}_max"] = format_number(max(values))
        else:
            for stat in STATS[1:]:
                output[f"{metric}_{stat}"] = ""
    return output


def aggregate_by(rows: Sequence[Dict[str, str]], group_columns: Sequence[str]) -> List[Dict[str, str]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(column, "") for column in group_columns)].append(row)
    output: List[Dict[str, str]] = []
    for key, group_rows in sorted(groups.items(), key=lambda item: sortable_key(item[0])):
        row = {column: key[index] for index, column in enumerate(group_columns)}
        row.update(metric_stats(group_rows))
        output.append(row)
    return output


def sortable_key(values: Sequence[str]) -> Tuple[object, ...]:
    result: List[object] = []
    for value in values:
        try:
            result.append((0, int(value)))
        except ValueError:
            result.append((1, value))
    return tuple(result)


def mean_metric(rows: Sequence[Dict[str, str]], metric: str) -> Optional[float]:
    values = [
        value
        for value in (parse_number(row.get(metric, "")) for row in rows if is_complete(row))
        if value is not None
    ]
    return statistics.fmean(values) if values else None


def convergence_summary(rows: Sequence[Dict[str, str]], batch_id: str) -> List[Dict[str, str]]:
    group_columns = ["scenario", "env", "topology", "workload_id", "workload_type"]
    groups: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(column, "") for column in group_columns)].append(row)

    summaries: List[Dict[str, str]] = []
    for key, group_rows in sorted(groups.items()):
        base = {column: key[index] for index, column in enumerate(group_columns)}
        complete_rows = [row for row in group_rows if is_complete(row)]
        cold_rows = [row for row in complete_rows if row.get("round_index") == "1"]
        max_elapsed = max(
            (parse_number(row.get("measurement_elapsed_ms", "")) or 0.0)
            for row in group_rows
        )
        steady_cutoff = max_elapsed * 0.8
        steady_rows = [
            row
            for row in complete_rows
            if (parse_number(row.get("measurement_elapsed_ms", "")) or 0.0) >= steady_cutoff
        ]
        cold_start_mean = mean_metric(cold_rows, "miss_latency_ns")
        steady_state_mean = mean_metric(steady_rows, "miss_latency_ns")
        threshold = steady_state_mean * 1.10 if steady_state_mean is not None else None
        bin_rows = aggregate_by(group_rows, ["request_bin"])
        consecutive = 0
        requests_to_converge = ""
        time_to_converge_ms = ""
        if threshold is not None:
            for bin_row in bin_rows:
                bin_mean = parse_number(bin_row.get("miss_latency_ns_mean", ""))
                if bin_mean is not None and bin_mean <= threshold:
                    consecutive += 1
                else:
                    consecutive = 0
                if consecutive >= 3:
                    target_bin = int(bin_row.get("request_bin", "0") or "0")
                    matching = [
                        row
                        for row in group_rows
                        if row.get("request_bin") == str(target_bin)
                    ]
                    min_measurement_position = min(
                        int(row.get("measurement_position", "0") or "0")
                        for row in matching
                    )
                    min_elapsed = min(
                        int(float(row.get("measurement_elapsed_ms", "0") or "0"))
                        for row in matching
                    )
                    requests_to_converge = str(min_measurement_position)
                    time_to_converge_ms = str(min_elapsed)
                    break
        summaries.append(
            {
                "batch_id": batch_id,
                **base,
                "rows": str(len(group_rows)),
                "complete_rows": str(len(complete_rows)),
                "incomplete_rows": str(len(group_rows) - len(complete_rows)),
                "cold_start_mean": format_number(cold_start_mean),
                "cold_start_mean_ns": format_number(cold_start_mean),
                "steady_state_mean": format_number(steady_state_mean),
                "steady_state_mean_ns": format_number(steady_state_mean),
                "steady_state_rows": str(len(steady_rows)),
                "converged": "1" if requests_to_converge else "0",
                "requests_to_converge": requests_to_converge,
                "time_to_converge_ms": time_to_converge_ms,
                "convergence_threshold_ns": format_number(threshold),
                "rolling_window_requests": "10",
                "convergence_required_windows": "3",
            }
        )
    return summaries


def metric_columns() -> List[str]:
    return [f"{metric}_{stat}" for metric in METRIC_COLUMNS for stat in STATS]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_dir = args.batch_dir
    source = batch_dir / "trace_breakdown_merged.csv"
    rows = [
        row
        for row in read_csv(source)
        if row.get("phase") == "measurement"
        and row.get("workload_family") in {"latency_convergence", "latency_v2"}
    ]

    merged_columns = list(rows[0].keys()) if rows else [
        "batch_id",
        "run_id",
        "scenario",
        "topology",
        "workload_id",
        "workload_type",
        "phase",
        "request_index",
        "request_bin",
    ]
    write_csv(batch_dir / "convergence_merged.csv", merged_columns, rows)

    base_group_columns = ["scenario", "env", "topology", "workload_id", "workload_type"]
    aggregate_columns = base_group_columns + ["round_index"] + ["rows", "complete_rows", "incomplete_rows"] + metric_columns()
    write_csv(
        batch_dir / "convergence_by_round.csv",
        aggregate_columns,
        aggregate_by(rows, base_group_columns + ["round_index"]),
    )
    aggregate_columns = base_group_columns + ["request_bin"] + ["rows", "complete_rows", "incomplete_rows"] + metric_columns()
    write_csv(
        batch_dir / "convergence_by_bin.csv",
        aggregate_columns,
        aggregate_by(rows, base_group_columns + ["request_bin"]),
    )
    aggregate_columns = base_group_columns + ["key_occurrence_index"] + ["rows", "complete_rows", "incomplete_rows"] + metric_columns()
    write_csv(
        batch_dir / "convergence_by_occurrence.csv",
        aggregate_columns,
        aggregate_by(rows, base_group_columns + ["key_occurrence_index"]),
    )
    write_csv(
        batch_dir / "convergence_summary.csv",
        SUMMARY_COLUMNS,
        convergence_summary(rows, batch_dir.name),
    )

    print(f"convergence rows: {len(rows)} -> {batch_dir / 'convergence_merged.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
