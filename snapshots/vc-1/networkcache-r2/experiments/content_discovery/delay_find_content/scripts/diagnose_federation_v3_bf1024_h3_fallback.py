#!/usr/bin/env python3
"""Write a deterministic, value-independent diagnostic for a failed pilot run."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


EVENTS = (
    "query_route_selected", "immediate_dead_end_fallback", "origin_fallback_armed",
    "origin_fallback_triggered", "fallback_forward", "query_key_satisfied",
    "query_complete", "duplicate_request_suppressed", "query_expired_unsatisfied",
)


def csv_rows(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def json_object(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def trace_events(root: Path) -> list[dict]:
    output = []
    for path in sorted((root / "traces").glob("*.jsonl")):
        for line in path.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
                event["trace_file"] = path.name
                output.append(event)
            except json.JSONDecodeError:
                continue
    return sorted(output, key=lambda event: int(event.get("ts_mono_ns") or 0))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.run_dir
    schedule = csv_rows(root / "schedule.tsv", "\t")
    results = csv_rows(root / "request_results.csv")
    completed_indexes = {int(row["request_index"]) for row in results if row.get("status") == "ok"}
    failed_rows = [row for row in results if row.get("status") != "ok"]
    missing = [row for row in schedule if int(row["request_index"]) not in completed_indexes]
    target = failed_rows[0] if failed_rows else missing[0] if missing else {}
    key = target.get("key", "")
    events = trace_events(root)
    event_counts = Counter(str(event.get("event", "")) for event in events)
    target_events = [event for event in events if key and event.get("key") == key]
    summary = json_object(root / "completion_summary.json")
    if not summary:
        summary = {
            "scheduled": len(schedule),
            "issued": len(results),
            "completed": len(completed_indexes),
        }
    diagnostic = {
        "status": json_object(root / "metadata.json").get("status", "unknown"),
        "completion": summary,
        "first_unsatisfied_request": target,
        "fallback_event_counts": {name: event_counts[name] for name in EVENTS},
        "target_event_counts": dict(sorted(Counter(
            str(event.get("event", "")) for event in target_events).items())),
        "target_last_events": target_events[-50:],
        "driver_stderr_tail": (root / "driver.stderr.log").read_text(errors="replace").splitlines()[-50:]
        if (root / "driver.stderr.log").exists() else [],
    }
    (root / "failure_diagnostic.json").write_text(
        json.dumps(diagnostic, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote deterministic failure diagnostic to {root / 'failure_diagnostic.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
