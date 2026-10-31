#!/usr/bin/env python3
"""Classify latency-v2 runs from the authoritative measurement trace."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Dict, List


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def expected_measurements(run_dir: Path) -> int:
    metadata_path = run_dir / "schedule_metadata.json"
    if not metadata_path.exists():
        metadata_path = run_dir / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    value = metadata.get("measurement_requests")
    if isinstance(value, int):
        return value
    requests = metadata.get("requests")
    if isinstance(requests, list):
        return sum(request.get("phase") == "measurement" for request in requests)
    return 0


def has_complete_measurement_trace(run_dir: Path) -> bool:
    expected = expected_measurements(run_dir)
    if expected <= 0:
        return False
    measurement_rows = [
        row for row in read_csv(run_dir / "trace_breakdown.csv")
        if row.get("phase") == "measurement"
    ]
    return len(measurement_rows) == expected and all(
        row.get("trace_complete") == "1" and row.get("miss_latency_ns")
        for row in measurement_rows
    )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} RUN_DIR")
    print("ok" if has_complete_measurement_trace(Path(sys.argv[1])) else "incomplete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
