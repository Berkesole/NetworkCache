#!/usr/bin/env python3
"""Analyze Oracle Direct Fetch baseline logs."""

from __future__ import annotations

import argparse
import calendar
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


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

TS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d{1,9}))?(?P<tz>Z|[+-]\d{2}:?\d{2})?\s+(?P<msg>.*)$"
)
EVENT_RE = re.compile(r"\boracle_event\b(?P<body>.*)$")


def parse_timestamped_line(line: str) -> Tuple[Optional[int], str]:
    match = TS_RE.match(line.rstrip("\n"))
    if not match:
        return None, line.rstrip("\n")
    base = f"{match.group('date')}T{match.group('time')}"
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    seconds = calendar.timegm(dt.utctimetuple())
    frac = (match.group("frac") or "").ljust(9, "0")[:9]
    return seconds * 1_000_000_000 + int(frac or "0"), match.group("msg")


def parse_event_fields(msg: str) -> Dict[str, str]:
    match = EVENT_RE.search(msg)
    if not match:
        return {}
    fields: Dict[str, str] = {}
    for part in match.group("body").strip().split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key] = value
    return fields


def iter_oracle_events(logs_dir: Path) -> Iterable[Tuple[int, Dict[str, str]]]:
    for path in sorted(logs_dir.glob("*.oracle.log")):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                ts_ns, msg = parse_timestamped_line(line)
                if ts_ns is None:
                    continue
                fields = parse_event_fields(msg)
                if fields:
                    yield ts_ns, fields


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in SUMMARY_COLUMNS})


def latency(end: Optional[int], start: Optional[int]) -> str:
    if end is None or start is None:
        return ""
    delta = end - start
    return str(delta) if delta >= 0 else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-dir", required=True, type=Path)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--env", default="kathara-oracle")
    parser.add_argument("--topology", required=True)
    parser.add_argument("--workload-id", required=True)
    parser.add_argument("--workload-type", required=True)
    parser.add_argument("--seed", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    schedule_rows = read_csv(args.schedule)

    starts: Dict[str, int] = {}
    provider_receives: Dict[str, int] = {}
    app_receives: Dict[str, int] = {}
    app_status: Dict[str, str] = {}
    value_bytes: Dict[str, str] = {}

    for ts_ns, fields in iter_oracle_events(args.logs_dir):
        request_index = fields.get("request_index", "")
        if not request_index:
            continue
        event = fields.get("event", "")
        if event == "oracle_request_start":
            starts.setdefault(request_index, ts_ns)
        elif event == "oracle_provider_receive":
            provider_receives.setdefault(request_index, ts_ns)
        elif event == "oracle_app_receive":
            app_receives.setdefault(request_index, ts_ns)
            app_status.setdefault(request_index, fields.get("status", ""))
            value_bytes.setdefault(request_index, fields.get("value_bytes", ""))

    output_rows: List[Dict[str, str]] = []
    for schedule in schedule_rows:
        request_index = schedule.get("request_index", "")
        start = starts.get(request_index)
        provider_receive = provider_receives.get(request_index)
        app_receive = app_receives.get(request_index)
        status = app_status.get(request_index, "")
        output_rows.append(
            {
                "workload_id": args.workload_id,
                "workload_type": args.workload_type,
                "seed": args.seed,
                "scenario": args.scenario,
                "env": args.env,
                "topology": args.topology,
                "phase": schedule.get("phase", ""),
                "request_index": request_index,
                "request_pair_id": schedule.get("request_pair_id", ""),
                "distance_class": schedule.get("distance_class", ""),
                "expected_cold_provider_hop": schedule.get("expected_cold_provider_hop", ""),
                "expected_warm_cache_hop": schedule.get("expected_warm_cache_hop", ""),
                "requester": schedule.get("requester", ""),
                "provider": schedule.get("provider", ""),
                "key": schedule.get("key", ""),
                "oracle_fetch_latency_ns": latency(app_receive, start),
                "oracle_provider_receive_delay_ns": latency(provider_receive, start),
                "value_bytes": value_bytes.get(request_index, schedule.get("value_bytes", "")),
                "status": status or ("ok" if app_receive is not None else ""),
            }
        )

    write_csv(args.output, output_rows)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
