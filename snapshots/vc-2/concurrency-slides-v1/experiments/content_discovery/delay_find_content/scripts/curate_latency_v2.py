#!/usr/bin/env python3
"""Select deterministic, request-complete latency-v2 repetitions."""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


RUN_COVERAGE = 0.95
BIN_COVERAGE = 0.70
REPETITIONS_PER_SEED = 4
SEEDS = (1, 2, 3, 4, 5)
MAX_CLOCK_SPREAD_MS = 5.0

CANDIDATE_COLUMNS = [
    "config", "seed", "attempt_batch", "attempt_order", "run_id", "scenario",
    "manifest_status", "summary_path", "metadata_path", "trace_path",
    "scheduled_measurement_rows", "usable_measurement_rows", "coverage",
    "bin_width", "analysis_bin_count", "min_usable_rows_per_bin",
    "clock_offset_spread_ms", "eligible", "selected", "selection_index",
]


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in CANDIDATE_COLUMNS})


def resolve(batch: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else batch / path


def bin_width(config: str) -> int:
    return 10 if config.startswith("controlled-") else 20


def usable_trace(row: Dict[str, str]) -> bool:
    required = (
        "request_id", "provider", "discovery_delay_ns", "return_delay_ns",
        "miss_latency_ns", "provider_hit_hop",
    )
    return (
        row.get("phase") == "measurement"
        and row.get("trace_complete") == "1"
        and all(row.get(column, "") != "" for column in required)
    )


def clock_offset_spread_ms(run_dir: Path) -> float:
    offsets: List[float] = []
    for path in sorted((run_dir / "traces").glob("*.jsonl")):
        values: List[int] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                data = json.loads(raw)
                values.append(int(data["ts_epoch_ns"]) - int(data["ts_mono_ns"]))
                if len(values) >= 32:
                    break
        if values:
            offsets.append(statistics.median(values))
    if len(offsets) < 2:
        raise ValueError("fewer than two trace clocks")
    return (max(offsets) - min(offsets)) / 1_000_000.0


def evaluate_candidate(
    config: str,
    batch: Path,
    manifest_row: Dict[str, str],
    attempt_order: int,
    check_clock: bool,
) -> Dict[str, object]:
    summary_path = resolve(batch, manifest_row.get("summary_path", ""))
    metadata_path = resolve(batch, manifest_row.get("metadata_path", ""))
    run_dir = summary_path.parent
    schedule_path = run_dir / "schedule_metadata.json"
    trace_path = run_dir / "trace_breakdown.csv"
    width = bin_width(config)
    output: Dict[str, object] = {
        "config": config,
        "seed": manifest_row.get("seed", ""),
        "attempt_batch": batch.name,
        "attempt_order": attempt_order,
        "run_id": manifest_row.get("run_id", ""),
        "scenario": manifest_row.get("scenario", ""),
        "manifest_status": manifest_row.get("status", ""),
        "summary_path": str(summary_path.resolve()),
        "metadata_path": str(metadata_path.resolve()),
        "trace_path": str(trace_path.resolve()),
        "scheduled_measurement_rows": 0,
        "usable_measurement_rows": 0,
        "coverage": "",
        "bin_width": width,
        "analysis_bin_count": 0,
        "min_usable_rows_per_bin": 0,
        "clock_offset_spread_ms": "",
        "eligible": "0",
        "selected": "0",
        "selection_index": "",
    }
    if manifest_row.get("status") == "driver_failed":
        return output
    if not all(path.exists() for path in (summary_path, metadata_path, schedule_path, trace_path)):
        return output
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return output
    if metadata.get("trace_clock") != "monotonic_ns" or str(metadata.get("trace_enabled")) != "1":
        return output
    measurement = [row for row in schedule.get("requests", []) if row.get("phase") == "measurement"]
    if not measurement:
        return output
    position_by_index = {
        str(row["request_index"]): int(row["measurement_position"])
        for row in measurement
    }
    usable_by_index: Dict[str, Dict[str, str]] = {}
    for row in read_csv(trace_path):
        request_index = row.get("request_index", "")
        if request_index in position_by_index and usable_trace(row):
            usable_by_index[request_index] = row
    scheduled = len(measurement)
    analysis_bins = math.ceil(scheduled / width)
    usable_per_bin = [0] * analysis_bins
    scheduled_per_bin = [0] * analysis_bins
    for position in position_by_index.values():
        scheduled_per_bin[(position - 1) // width] += 1
    for request_index in usable_by_index:
        usable_per_bin[(position_by_index[request_index] - 1) // width] += 1
    usable = len(usable_by_index)
    output.update(
        {
            "scheduled_measurement_rows": scheduled,
            "usable_measurement_rows": usable,
            "coverage": f"{usable / scheduled:.6f}",
            "analysis_bin_count": analysis_bins,
            "min_usable_rows_per_bin": min(usable_per_bin),
        }
    )
    clock_ok = True
    if check_clock:
        try:
            spread = clock_offset_spread_ms(run_dir)
            output["clock_offset_spread_ms"] = f"{spread:.6f}"
            clock_ok = spread <= MAX_CLOCK_SPREAD_MS
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            clock_ok = False
    overall_ok = usable >= math.ceil(RUN_COVERAGE * scheduled)
    bins_ok = all(
        actual >= math.ceil(BIN_COVERAGE * expected)
        for actual, expected in zip(usable_per_bin, scheduled_per_bin)
    )
    output["eligible"] = "1" if overall_ok and bins_ok and clock_ok else "0"
    return output


def discover_candidates(
    results_root: Path,
    configs: Sequence[str],
    check_clock: bool = False,
) -> List[Dict[str, object]]:
    candidates: List[Dict[str, object]] = []
    for config in configs:
        attempt_order = 0
        for batch in sorted(results_root.glob(f"{config}-attempts-*")):
            for manifest_row in read_csv(batch / "manifest.csv"):
                attempt_order += 1
                candidates.append(
                    evaluate_candidate(config, batch, manifest_row, attempt_order, check_clock)
                )
    return candidates


def select_candidates(
    candidates: Sequence[Dict[str, object]],
    configs: Sequence[str],
) -> Tuple[List[Dict[str, object]], Dict[str, Dict[str, object]]]:
    groups: Dict[Tuple[str, int], List[Dict[str, object]]] = defaultdict(list)
    for row in candidates:
        if row.get("eligible") == "1":
            try:
                seed = int(row["seed"])
            except (TypeError, ValueError):
                continue
            if seed in SEEDS:
                groups[(str(row["config"]), seed)].append(dict(row))
    selected: List[Dict[str, object]] = []
    status: Dict[str, Dict[str, object]] = {}
    for config in configs:
        seed_counts: Dict[int, int] = {}
        config_rows: List[Dict[str, object]] = []
        for seed in SEEDS:
            rows = groups.get((config, seed), [])[:REPETITIONS_PER_SEED]
            seed_counts[seed] = len(rows)
            for selection_index, row in enumerate(rows, start=1):
                row["selected"] = "1"
                row["selection_index"] = selection_index
                config_rows.append(row)
        ready = all(seed_counts[seed] == REPETITIONS_PER_SEED for seed in SEEDS)
        status[config] = {
            "latency_status": "READY" if ready else "NEEDS_MORE",
            "selected_runs": len(config_rows),
            "selected_seed_counts": ";".join(
                f"seed{seed:03d}={seed_counts[seed]}" for seed in SEEDS
            ),
            "usable_measurement_rows": sum(int(row["usable_measurement_rows"]) for row in config_rows),
            "scheduled_measurement_rows": sum(int(row["scheduled_measurement_rows"]) for row in config_rows),
            "min_usable_rows_per_bin": min(
                (int(row["min_usable_rows_per_bin"]) for row in config_rows), default=0
            ),
        }
        selected.extend(config_rows)
    return selected, status


def build_selection(
    results_root: Path,
    configs: Sequence[str],
    check_clock: bool = False,
    write: bool = True,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], Dict[str, Dict[str, object]]]:
    candidates = discover_candidates(results_root, configs, check_clock)
    selected, status = select_candidates(candidates, configs)
    selected_keys = {
        (str(row["config"]), str(row["attempt_batch"]), str(row["run_id"])): row
        for row in selected
    }
    output_candidates: List[Dict[str, object]] = []
    for source in candidates:
        row = dict(source)
        selected_row = selected_keys.get(
            (str(row["config"]), str(row["attempt_batch"]), str(row["run_id"]))
        )
        if selected_row:
            row["selected"] = "1"
            row["selection_index"] = selected_row["selection_index"]
        output_candidates.append(row)
    if write:
        write_csv(results_root / "latency_candidates.csv", output_candidates)
        write_csv(results_root / "latency_selection.csv", selected)
    return output_candidates, selected, status
