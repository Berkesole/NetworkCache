#!/usr/bin/env python3
"""Analyze env-gated C++ JSONL event traces for real timing breakdowns."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEDULE_COLUMNS = [
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
    "measurement_position",
    "round_index",
    "key_occurrence_index",
    "popularity_rank",
    "measurement_elapsed_ms",
    "request_bin",
    "workload_family",
]
SUMMARY_COLUMNS = SCHEDULE_COLUMNS + [
    "scenario",
    "env",
    "topology",
    "warm_type",
    "requester",
    "provider",
    "key",
    "request_id",
    "discovery_delay_ns",
    "return_delay_ns",
    "miss_latency_ns",
    "request_packets",
    "data_packets",
    "aggregate_flushes",
    "provider_hit_hop",
    "trace_complete",
]
BREAKDOWN_COLUMNS = SUMMARY_COLUMNS + [
    "service_class",
    "request_to_send_ns",
    "tcp_send_ns",
    "send_to_handle_ns",
    "provider_lookup_ns",
    "request_to_hit_ns",
    "data_send_ns",
    "data_to_app_handle_ns",
    "app_delivery_ns",
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
GROUP_COLUMNS = [
    "scenario",
    "env",
    "topology",
    "phase",
    "warm_type",
    "distance_class",
    "service_class",
]
STATS = ["count", "mean", "median", "p95", "stddev", "min", "max"]


@dataclass(frozen=True)
class TraceEvent:
    ts_epoch_ns: int
    ts_mono_ns: int
    run_id: str
    node: int
    event: str
    request_id: str
    key: str
    src: str
    dest: str
    packet_type: str
    hop_count: str
    via: str
    tick: str
    value_bytes: str


@dataclass
class RequestRecord:
    start: TraceEvent
    schedule: Dict[str, str]
    key_seen_count: int


def read_trace_events(traces_dir: Path, clock: str = "monotonic") -> List[TraceEvent]:
    events: List[TraceEvent] = []
    for path in sorted(traces_dir.glob("*.jsonl")):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, raw in enumerate(handle, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"invalid JSON in {path}:{line_number}: {exc}") from exc
                try:
                    node = int(data.get("node", 0))
                    ts_epoch_ns = int(data.get("ts_epoch_ns", 0))
                    ts_mono_ns = int(data.get("ts_mono_ns", 0))
                except (TypeError, ValueError) as exc:
                    raise SystemExit(f"invalid timestamp/node in {path}:{line_number}") from exc
                events.append(
                    TraceEvent(
                        # The rest of the analyzer uses ts_epoch_ns as its
                        # selected ordering/delta clock for backward
                        # compatibility. Formal latency-v2 runs select the
                        # monotonic timestamp here.
                        ts_epoch_ns=ts_mono_ns if clock == "monotonic" else ts_epoch_ns,
                        ts_mono_ns=ts_mono_ns,
                        run_id=str(data.get("run_id", "")),
                        node=node,
                        event=str(data.get("event", "")),
                        request_id=str(data.get("request_id", "")),
                        key=str(data.get("key", "")),
                        src=str(data.get("src", "")),
                        dest=str(data.get("dest", "")),
                        packet_type=str(data.get("packet_type", "")),
                        hop_count=str(data.get("hop_count", "")),
                        via=str(data.get("via", "")),
                        tick=str(data.get("tick", "")),
                        value_bytes=str(data.get("value_bytes", "")),
                    )
                )
    events.sort(key=lambda event: (event.ts_epoch_ns, event.node, event.event))
    return events


def parse_topology(path: Optional[Path]) -> Dict[int, List[int]]:
    if not path or not path.exists():
        return {}
    graph: Dict[int, List[int]] = {}
    current: Optional[int] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- id:"):
            try:
                current = int(stripped.split(":", 1)[1].strip())
                graph.setdefault(current, [])
            except ValueError:
                current = None
            continue
        if current is not None and stripped.startswith("neighbors:"):
            raw = stripped.split(":", 1)[1].strip()
            raw = raw.strip("[]")
            graph[current] = [int(part.strip()) for part in raw.split(",") if part.strip()]
    return graph


def bfs_distance(graph: Dict[int, List[int]], src: Optional[int], dest: Optional[int]) -> str:
    if src is None or dest is None or not graph:
        return ""
    if src == dest:
        return "0"
    seen = {src}
    queue: Deque[Tuple[int, int]] = deque([(src, 0)])
    while queue:
        node, dist = queue.popleft()
        for neighbor in graph.get(node, []):
            if neighbor == dest:
                return str(dist + 1)
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, dist + 1))
    return ""


def load_schedule_metadata(path: Optional[Path]) -> Tuple[Dict[str, str], Dict[Tuple[int, str], Deque[Dict[str, str]]]]:
    if not path or not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    workload = {
        "workload_id": str(data.get("workload_id", "")),
        "workload_type": str(data.get("workload_type", "")),
        "seed": str(data.get("seed", "")),
    }
    queues: Dict[Tuple[int, str], Deque[Dict[str, str]]] = defaultdict(deque)
    for request in data.get("requests", []):
        node = int(request.get("node", 0))
        key = str(request.get("key", ""))
        queues[(node, key)].append(
            {
                "phase": str(request.get("phase", "")),
                "request_index": str(request.get("request_index", "")),
                "request_pair_id": str(request.get("request_pair_id", "")),
                "distance_class": str(request.get("distance_class", "")),
                "expected_cold_provider_hop": str(request.get("expected_cold_provider_hop", "")),
                "expected_warm_cache_hop": str(request.get("expected_warm_cache_hop", "")),
                "measurement_position": str(request.get("measurement_position", "")),
                "round_index": str(request.get("round_index", "")),
                "key_occurrence_index": str(request.get("key_occurrence_index", "")),
                "popularity_rank": str(request.get("popularity_rank", "")),
                "measurement_elapsed_ms": str(request.get("measurement_elapsed_ms", "")),
                "request_bin": str(request.get("request_bin", "")),
                "workload_family": str(request.get("workload_family", data.get("workload_family", ""))),
                "scheduled_provider": str(request.get("provider", "")),
                "tick": str(request.get("tick", "")),
            }
        )
    return workload, queues


def contains_key(event_key: str, key: str) -> bool:
    return key in [part for part in event_key.split(";") if part]


def latency(end: Optional[TraceEvent], start: Optional[TraceEvent]) -> str:
    if end is None or start is None:
        return ""
    delta = end.ts_epoch_ns - start.ts_epoch_ns
    return str(delta) if delta >= 0 else ""


def numeric_latency(end: Optional[TraceEvent], start: Optional[TraceEvent]) -> Optional[int]:
    text = latency(end, start)
    return int(text) if text else None


def first_event(
    events: Sequence[TraceEvent],
    *,
    name: str,
    start_ns: int,
    end_ns: Optional[int] = None,
    node: Optional[int] = None,
    key: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Optional[TraceEvent]:
    for event in events:
        if event.event != name:
            continue
        if event.ts_epoch_ns < start_ns:
            continue
        if end_ns is not None and event.ts_epoch_ns > end_ns:
            continue
        if node is not None and event.node != node:
            continue
        if key is not None and event.key != key and not contains_key(event.key, key):
            continue
        if request_id is not None and event.request_id != request_id:
            continue
        return event
    return None


def first_event_prefer_request_id(
    events: Sequence[TraceEvent],
    *,
    name: str,
    start_ns: int,
    end_ns: Optional[int] = None,
    node: Optional[int] = None,
    key: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Optional[TraceEvent]:
    if request_id:
        event = first_event(
            events,
            name=name,
            start_ns=start_ns,
            end_ns=end_ns,
            node=node,
            key=key,
            request_id=request_id,
        )
        if event is not None:
            return event
    return first_event(
        events,
        name=name,
        start_ns=start_ns,
        end_ns=end_ns,
        node=node,
        key=key,
        request_id=None,
    )


def count_events(
    events: Sequence[TraceEvent],
    *,
    names: Sequence[str],
    start_ns: int,
    end_ns: Optional[int],
    request_id: Optional[str] = None,
) -> int:
    names_set = set(names)
    total = 0
    for event in events:
        if event.event not in names_set:
            continue
        if event.ts_epoch_ns < start_ns:
            continue
        if end_ns is not None and event.ts_epoch_ns > end_ns:
            continue
        if request_id is not None and event.request_id != request_id:
            continue
        total += 1
    return total


def build_records(
    events: Sequence[TraceEvent],
    schedule_queues: Dict[Tuple[int, str], Deque[Dict[str, str]]],
) -> List[RequestRecord]:
    by_key_seen: Dict[str, int] = defaultdict(int)
    records: List[RequestRecord] = []
    starts = [
        event
        for event in events
        if event.event == "request_start" and event.key
    ]
    starts.sort(key=lambda event: event.ts_epoch_ns)
    for start in starts:
        seen = by_key_seen[start.key]
        by_key_seen[start.key] += 1
        schedule_queue = schedule_queues.get((start.node, start.key), deque())
        schedule = schedule_queue.popleft() if schedule_queue else {}
        records.append(RequestRecord(start=start, schedule=schedule, key_seen_count=seen))
    return records


def request_window_end(records: Sequence[RequestRecord], index: int) -> Optional[int]:
    start = records[index].start
    later = [
        record.start.ts_epoch_ns
        for record in records[index + 1 :]
        if record.start.node == start.node and record.start.key == start.key
    ]
    return min(later) - 1 if later else None


def format_optional(value: Optional[int]) -> str:
    return "" if value is None else str(value)


def service_class(row: Dict[str, str]) -> str:
    env = row.get("env", "")
    if env == "kathara-oracle-nc-unicast":
        return "oracle_nc_unicast"
    if row.get("warm_type") == "cold":
        return "nc_cold"
    if row.get("provider_hit_hop") == "0":
        return "nc_warm_local"
    return "nc_warm_nearby"


def build_rows(args: argparse.Namespace, events: List[TraceEvent]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    graph = parse_topology(args.topology_file)
    workload, queues = load_schedule_metadata(args.schedule_metadata)
    records = build_records(events, queues)
    summary_rows: List[Dict[str, str]] = []
    breakdown_rows: List[Dict[str, str]] = []

    for index, record in enumerate(records):
        start = record.start
        window_end = request_window_end(records, index)
        send_start = first_event(
            events,
            name="request_send_start",
            node=start.node,
            key=start.key,
            start_ns=start.ts_epoch_ns,
            end_ns=window_end,
        )
        request_id = send_start.request_id if send_start else "0"
        send_done = first_event(
            events,
            name="request_send_done",
            node=start.node,
            key=start.key,
            request_id=request_id if request_id != "0" else None,
            start_ns=start.ts_epoch_ns,
            end_ns=window_end,
        )
        app_receive = first_event_prefer_request_id(
            events,
            name="app_receive",
            node=start.node,
            key=start.key,
            request_id=request_id if request_id != "0" else None,
            start_ns=start.ts_epoch_ns,
            end_ns=window_end,
        )
        effective_end = app_receive.ts_epoch_ns if app_receive else window_end
        provider_hit = first_event_prefer_request_id(
            events,
            name="provider_hit",
            key=start.key,
            request_id=request_id if request_id != "0" else None,
            start_ns=start.ts_epoch_ns,
            end_ns=effective_end,
        )
        provider_node = provider_hit.node if provider_hit else None
        provider_request_id = provider_hit.request_id if provider_hit else request_id
        request_handle_start = first_event_prefer_request_id(
            events,
            name="request_handle_start",
            node=provider_node,
            request_id=provider_request_id if provider_request_id != "0" else None,
            start_ns=send_done.ts_epoch_ns if send_done else start.ts_epoch_ns,
            end_ns=provider_hit.ts_epoch_ns if provider_hit else effective_end,
        )
        data_send_start = first_event_prefer_request_id(
            events,
            name="data_send_start",
            node=provider_node,
            key=start.key,
            request_id=provider_request_id if provider_request_id != "0" else None,
            start_ns=provider_hit.ts_epoch_ns if provider_hit else start.ts_epoch_ns,
            end_ns=effective_end,
        )
        data_send_done = first_event_prefer_request_id(
            events,
            name="data_send_done",
            node=provider_node,
            key=start.key,
            request_id=provider_request_id if provider_request_id != "0" else None,
            start_ns=data_send_start.ts_epoch_ns if data_send_start else start.ts_epoch_ns,
            end_ns=effective_end,
        )
        data_handle_start = first_event_prefer_request_id(
            events,
            name="data_handle_start",
            node=start.node,
            key=start.key,
            request_id=provider_request_id if provider_request_id != "0" else None,
            start_ns=data_send_done.ts_epoch_ns if data_send_done else start.ts_epoch_ns,
            end_ns=effective_end,
        )
        if provider_hit is None and app_receive is not None:
            provider_hit = app_receive
            provider_node = start.node
        warm_type = args.warm_type if args.warm_type != "auto" else (
            "path_materialization" if record.key_seen_count > 0 else "cold"
        )
        common = {
            "workload_id": workload.get("workload_id", ""),
            "workload_type": workload.get("workload_type", ""),
            "seed": workload.get("seed", ""),
            "phase": record.schedule.get("phase", ""),
            "request_index": record.schedule.get("request_index", ""),
            "is_first_key_request": "1" if record.key_seen_count == 0 else "0",
            "request_pair_id": record.schedule.get("request_pair_id", ""),
            "distance_class": record.schedule.get("distance_class", ""),
            "expected_cold_provider_hop": record.schedule.get("expected_cold_provider_hop", ""),
            "expected_warm_cache_hop": record.schedule.get("expected_warm_cache_hop", ""),
            "measurement_position": record.schedule.get("measurement_position", ""),
            "round_index": record.schedule.get("round_index", ""),
            "key_occurrence_index": record.schedule.get("key_occurrence_index", ""),
            "popularity_rank": record.schedule.get("popularity_rank", ""),
            "measurement_elapsed_ms": record.schedule.get("measurement_elapsed_ms", ""),
            "request_bin": record.schedule.get("request_bin", ""),
            "workload_family": record.schedule.get("workload_family", ""),
            "scenario": args.scenario,
            "env": args.env,
            "topology": args.topology,
            "warm_type": warm_type,
            "requester": str(start.node),
            "provider": str(provider_node or ""),
            "key": start.key,
            "request_id": request_id,
            "discovery_delay_ns": latency(provider_hit, start),
            "return_delay_ns": latency(app_receive, provider_hit) if provider_hit else "",
            "miss_latency_ns": latency(app_receive, start),
            "request_packets": str(count_events(
                events,
                names=["request_send_done", "request_forward_done"],
                start_ns=start.ts_epoch_ns,
                end_ns=effective_end,
                request_id=None,
            )),
            "data_packets": str(count_events(
                events,
                names=["data_send_done", "data_forward_done"],
                start_ns=start.ts_epoch_ns,
                end_ns=effective_end,
                request_id=None,
            )),
            "aggregate_flushes": str(count_events(
                events,
                names=["aggregate_flush"],
                start_ns=start.ts_epoch_ns,
                end_ns=effective_end,
                request_id=None,
            )),
            "provider_hit_hop": bfs_distance(graph, start.node, provider_node),
        }
        common["trace_complete"] = "1" if all(
            common.get(column)
            for column in ["discovery_delay_ns", "return_delay_ns", "miss_latency_ns", "provider_hit_hop"]
        ) else "0"
        breakdown = {
            **common,
            "service_class": "",
            "request_to_send_ns": format_optional(numeric_latency(send_start, start)),
            "tcp_send_ns": format_optional(numeric_latency(send_done, send_start)),
            "send_to_handle_ns": format_optional(numeric_latency(request_handle_start, send_done)),
            "provider_lookup_ns": format_optional(numeric_latency(provider_hit, request_handle_start)),
            "request_to_hit_ns": latency(provider_hit, start),
            "data_send_ns": format_optional(numeric_latency(data_send_done, data_send_start)),
            "data_to_app_handle_ns": format_optional(numeric_latency(data_handle_start, data_send_done)),
            "app_delivery_ns": format_optional(numeric_latency(app_receive, data_handle_start)),
        }
        breakdown["service_class"] = service_class(breakdown)
        summary_rows.append(common)
        breakdown_rows.append(breakdown)

    return summary_rows, breakdown_rows


def parse_number(value: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def p95(values: Sequence[float]) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def format_number(value: float) -> str:
    if math.isnan(value) or math.isinf(value):
        return ""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def aggregate_breakdown(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
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


def write_csv(path: Path, rows: Sequence[Dict[str, str]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces-dir", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--breakdown-output", required=True, type=Path)
    parser.add_argument("--by-class-output", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--topology", required=True)
    parser.add_argument("--topology-file", type=Path)
    parser.add_argument("--schedule-metadata", type=Path)
    parser.add_argument("--warm-type", default="auto", choices=["auto", "cold", "path_materialization"])
    parser.add_argument("--clock", default="monotonic", choices=["monotonic", "epoch"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    events = read_trace_events(args.traces_dir, args.clock)
    summary_rows, breakdown_rows = build_rows(args, events)
    aggregate_rows = aggregate_breakdown(breakdown_rows)
    aggregate_columns = GROUP_COLUMNS + [
        f"{metric}_{stat}" for metric in METRIC_COLUMNS for stat in STATS
    ]
    write_csv(args.summary_output, summary_rows, SUMMARY_COLUMNS)
    write_csv(args.breakdown_output, breakdown_rows, BREAKDOWN_COLUMNS)
    write_csv(args.by_class_output, aggregate_rows, aggregate_columns)
    print(f"wrote {len(summary_rows)} trace summary rows to {args.summary_output}")
    print(f"wrote {len(breakdown_rows)} trace breakdown rows to {args.breakdown_output}")
    print(f"wrote {len(aggregate_rows)} trace aggregate rows to {args.by_class_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
