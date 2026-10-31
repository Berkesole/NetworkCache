#!/usr/bin/env python3
"""Analyze timestamped rsync-node-dev logs for content discovery experiments.

The analyzer intentionally consumes existing stdout/TCP logs only. It does not
require internal JSONL tracing or protocol changes.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

CSV_COLUMNS = [
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

TS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d{1,9}))?(?P<tz>Z|[+-]\d{2}:?\d{2})?\s+(?P<msg>.*)$"
)
NODE_FILE_RE = re.compile(r"node[-_]?(?P<node>\d+)\.log$")
NODE_LINE_RE = re.compile(r"\[Node\s+(?P<node>\d+)\]")
REQUEST_START_RE = re.compile(r"User wants file:\s*(?P<key>[^\s,]+)")
PROVIDER_HIT_RE = re.compile(r"Sending DATA for key=(?P<key>[^\s,]+)\s+to Node\s+(?P<dest>\d+)")
APP_RECEIVE_RE = re.compile(r"\[App\]\s+Received data for key=(?P<key>[^\s,]+)")
OBS_RE = re.compile(r"\[Obs\]\s+(?P<body>.*)$")


@dataclass(frozen=True)
class Event:
    ts_ns: int
    node: Optional[int]
    kind: str
    key: Optional[str]
    line: str


@dataclass(frozen=True)
class RequestRecord:
    start: Event
    schedule_fields: Dict[str, str]
    key_count: int


def parse_timestamped_line(line: str) -> Tuple[Optional[int], str]:
    match = TS_RE.match(line.rstrip("\n"))
    if not match:
        return None, line.rstrip("\n")

    base = f"{match.group('date')}T{match.group('time')}"
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    seconds = calendar.timegm(dt.utctimetuple())
    frac = (match.group("frac") or "").ljust(9, "0")[:9]
    return seconds * 1_000_000_000 + int(frac or "0"), match.group("msg")


def node_from_path(path: Path) -> Optional[int]:
    match = NODE_FILE_RE.search(path.name)
    return int(match.group("node")) if match else None


def node_from_line(line: str, fallback: Optional[int]) -> Optional[int]:
    match = NODE_LINE_RE.search(line)
    return int(match.group("node")) if match else fallback


def parse_obs_fields(line: str) -> Dict[str, str]:
    match = OBS_RE.search(line)
    if not match:
        return {}
    fields: Dict[str, str] = {}
    for part in match.group("body").split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key] = value
    return fields


def iter_events(logs_dir: Path) -> Iterable[Event]:
    for path in sorted(logs_dir.glob("*.log")):
        fallback_node = node_from_path(path)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                ts_ns, msg = parse_timestamped_line(raw_line)
                if ts_ns is None:
                    # Kathara/shared-file logs may not carry timestamps yet. Keep the
                    # analyzer conservative instead of inventing event times.
                    continue

                node = node_from_line(msg, fallback_node)
                obs_fields = parse_obs_fields(msg)
                if obs_fields:
                    obs_node = int(obs_fields.get("node", node or 0))
                    obs_event = obs_fields.get("event", "")
                    obs_key = obs_fields.get("key")
                    if obs_event == "provider_hit" and obs_key:
                        yield Event(ts_ns, obs_node, "provider_hit", obs_key, msg)
                    elif obs_event == "app_receive" and obs_key:
                        yield Event(ts_ns, obs_node, "app_receive", obs_key, msg)
                    continue

                request = REQUEST_START_RE.search(msg)
                if request:
                    yield Event(ts_ns, node, "request_start", request.group("key"), msg)

                provider = PROVIDER_HIT_RE.search(msg)
                if provider:
                    yield Event(ts_ns, node, "provider_hit", provider.group("key"), msg)

                app = APP_RECEIVE_RE.search(msg)
                if app:
                    yield Event(ts_ns, node, "app_receive", app.group("key"), msg)

                if "sent REQUEST packet" in msg:
                    yield Event(ts_ns, node, "request_packet", None, msg)
                if "sent DATA packet" in msg:
                    yield Event(ts_ns, node, "data_packet", None, msg)
                if "flush AGGREGATE" in msg:
                    yield Event(ts_ns, node, "aggregate_flush", None, msg)
                if "via SyncTable" in msg:
                    yield Event(ts_ns, node, "sync_table_hit", None, msg)
                if "no SyncTable hit" in msg:
                    yield Event(ts_ns, node, "sync_table_miss", None, msg)


def parse_topology(path: Optional[Path]) -> Dict[int, List[int]]:
    if not path:
        return {}
    if not path.exists():
        return {}

    graph: Dict[int, List[int]] = {}
    current: Optional[int] = None
    id_re = re.compile(r"^\s*-\s*id:\s*(\d+)\s*$")
    neighbors_re = re.compile(r"^\s*neighbors:\s*\[(.*?)\]\s*$")

    for line in path.read_text(encoding="utf-8").splitlines():
        id_match = id_re.match(line)
        if id_match:
            current = int(id_match.group(1))
            graph.setdefault(current, [])
            continue
        neighbors_match = neighbors_re.match(line)
        if neighbors_match and current is not None:
            raw = neighbors_match.group(1).strip()
            graph[current] = [int(part.strip()) for part in raw.split(",") if part.strip()]

    return graph


def bfs_distance(graph: Dict[int, List[int]], src: Optional[int], dest: Optional[int]) -> str:
    if src is None or dest is None or not graph:
        return ""
    if src == dest:
        return "0"
    seen = {src}
    queue = deque([(src, 0)])
    while queue:
        node, dist = queue.popleft()
        for neighbor in graph.get(node, []):
            if neighbor == dest:
                return str(dist + 1)
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, dist + 1))
    return ""


def load_schedule_metadata(path: Optional[Path]) -> Tuple[Dict[str, str], Dict[Tuple[Optional[int], str], deque[Dict[str, str]]]]:
    if not path or not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    workload = {
        "workload_id": str(data.get("workload_id", "")),
        "workload_type": str(data.get("workload_type", "")),
        "seed": str(data.get("seed", "")),
    }
    queues: Dict[Tuple[Optional[int], str], deque[Dict[str, str]]] = defaultdict(deque)
    for request in data.get("requests", []):
        key = str(request.get("key", ""))
        node_value = request.get("node")
        node = int(node_value) if node_value is not None else None
        queues[(node, key)].append(
            {
                "phase": str(request.get("phase", "")),
                "request_index": str(request.get("request_index", "")),
                "request_pair_id": str(request.get("request_pair_id", "")),
                "distance_class": str(request.get("distance_class", "")),
                "expected_cold_provider_hop": str(request.get("expected_cold_provider_hop", "")),
                "expected_warm_cache_hop": str(request.get("expected_warm_cache_hop", "")),
            }
        )
    return workload, queues


def in_window(event: Event, start_ns: int, end_ns: Optional[int]) -> bool:
    if event.ts_ns < start_ns:
        return False
    if end_ns is not None and event.ts_ns > end_ns:
        return False
    return True


def first_event(events: List[Event], *, kind: str, key: str, start_ns: int, end_ns: Optional[int], node: Optional[int] = None) -> Optional[Event]:
    for event in events:
        if event.kind != kind or event.key != key:
            continue
        if node is not None and event.node != node:
            continue
        if in_window(event, start_ns, end_ns):
            return event
    return None


def count_kind(events: List[Event], kind: str, start_ns: int, end_ns: Optional[int]) -> int:
    return sum(1 for event in events if event.kind == kind and in_window(event, start_ns, end_ns))


def latency(end: Optional[Event], start: Event) -> str:
    if end is None:
        return ""
    delta = end.ts_ns - start.ts_ns
    return str(delta) if delta >= 0 else ""


def build_request_records(
    starts: List[Event],
    schedule_queues: Dict[Tuple[Optional[int], str], deque[Dict[str, str]]],
) -> List[RequestRecord]:
    by_key_seen: defaultdict[str, int] = defaultdict(int)
    records: List[RequestRecord] = []

    for start in starts:
        key_count = by_key_seen[start.key or ""]
        by_key_seen[start.key or ""] += 1
        schedule = schedule_queues.get((start.node, start.key or ""), deque())
        schedule_fields = schedule.popleft() if schedule else {}
        records.append(RequestRecord(start=start, schedule_fields=schedule_fields, key_count=key_count))
    return records


def request_window_end(records: List[RequestRecord], index: int) -> Optional[int]:
    start = records[index].start
    later_starts = [
        record.start.ts_ns
        for record in records[index + 1 :]
        if record.start.key == start.key
    ]
    next_start_ns = min(later_starts) if later_starts else None
    return next_start_ns - 1 if next_start_ns is not None else None


def build_rows(args: argparse.Namespace, events: List[Event]) -> List[Dict[str, str]]:
    graph = parse_topology(args.topology_file)
    workload_fields, schedule_queues = load_schedule_metadata(args.schedule_metadata)
    starts = [event for event in events if event.kind == "request_start" and event.key]
    starts.sort(key=lambda event: event.ts_ns)
    records = build_request_records(starts, schedule_queues)
    rows: List[Dict[str, str]] = []

    for index, record in enumerate(records):
        start = record.start
        provisional_end = request_window_end(records, index)

        app_receive = first_event(
            events,
            kind="app_receive",
            key=start.key or "",
            start_ns=start.ts_ns,
            end_ns=provisional_end,
            node=start.node,
        )
        end_ns = app_receive.ts_ns if app_receive else provisional_end
        provider_hit = first_event(
            events,
            kind="provider_hit",
            key=start.key or "",
            start_ns=start.ts_ns,
            end_ns=end_ns,
        )
        request_packets = count_kind(events, "request_packet", start.ts_ns, end_ns)
        data_packets = count_kind(events, "data_packet", start.ts_ns, end_ns)
        aggregate_flushes = count_kind(events, "aggregate_flush", start.ts_ns, end_ns)
        sync_table_hits = count_kind(events, "sync_table_hit", start.ts_ns, end_ns)
        sync_table_misses = count_kind(events, "sync_table_miss", start.ts_ns, end_ns)
        if provider_hit is None and app_receive is not None:
            provider_hit = Event(
                app_receive.ts_ns,
                start.node,
                "provider_hit",
                start.key,
                "derived local cache hit",
            )

        if args.warm_type == "cold":
            row_warm_type = "cold"
        elif record.key_count > 0:
            row_warm_type = "path_materialization"
        else:
            row_warm_type = "cold"
        schedule_fields = record.schedule_fields

        rows.append(
            {
                "workload_id": workload_fields.get("workload_id", ""),
                "workload_type": workload_fields.get("workload_type", ""),
                "seed": workload_fields.get("seed", ""),
                "phase": schedule_fields.get("phase", ""),
                "request_index": schedule_fields.get("request_index", ""),
                "is_first_key_request": "1" if record.key_count == 0 else "0",
                "request_pair_id": schedule_fields.get("request_pair_id", ""),
                "distance_class": schedule_fields.get("distance_class", ""),
                "expected_cold_provider_hop": schedule_fields.get("expected_cold_provider_hop", ""),
                "expected_warm_cache_hop": schedule_fields.get("expected_warm_cache_hop", ""),
                "scenario": args.scenario,
                "env": args.env,
                "topology": args.topology,
                "warm_type": row_warm_type,
                "requester": str(start.node or ""),
                "provider": str(provider_hit.node if provider_hit and provider_hit.node is not None else ""),
                "key": start.key or "",
                "discovery_delay_ns": latency(provider_hit, start),
                "return_delay_ns": latency(app_receive, provider_hit) if provider_hit and app_receive else "",
                "miss_latency_ns": latency(app_receive, start),
                "request_packets": str(request_packets),
                "data_packets": str(data_packets),
                "aggregate_flushes": str(aggregate_flushes),
                "sync_table_hits": str(sync_table_hits),
                "sync_table_misses": str(sync_table_misses),
                "provider_hit_hop": bfs_distance(
                    graph, start.node, provider_hit.node if provider_hit else None
                ),
            }
        )

    return rows


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument(
        "--env",
        required=True,
        choices=["docker", "kathara", "mock", "kathara-oracle-nc-unicast"],
    )
    parser.add_argument("--topology", required=True)
    parser.add_argument("--topology-file", type=Path)
    parser.add_argument("--schedule-metadata", type=Path)
    parser.add_argument("--warm-type", default="auto", choices=["auto", "cold", "path_materialization"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    events = sorted(iter_events(args.logs_dir), key=lambda event: event.ts_ns)
    rows = build_rows(args, events)
    write_csv(args.output, rows)
    print(f"wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
