#!/usr/bin/env python3
"""Diagnose incomplete request completion from existing experiment logs.

This script is intentionally log-only. It does not require protocol changes or
internal tracing; it correlates summary.csv rows with timestamped node logs and
reports where DATA evidence stops for incomplete measurement requests.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from analyze_logs import (
    APP_RECEIVE_RE,
    REQUEST_START_RE,
    parse_timestamped_line,
    parse_obs_fields,
    node_from_line,
    node_from_path,
    parse_topology,
)


DATA_SEND_RE = re.compile(r"Sending DATA for key=(?P<key>[^\s,]+)\s+to Node\s+(?P<dest>\d+)")
DATA_FORWARD_RE = re.compile(r"Forwarding DATA for key=(?P<key>[^\s,]+)\s+to Node\s+(?P<dest>\d+)")
DATA_RECEIVE_RE = re.compile(r"Received DATA for key=(?P<key>[^\s,]+)")

OUTPUT_COLUMNS = [
    "batch_id",
    "run_id",
    "scenario",
    "env",
    "topology",
    "phase",
    "request_index",
    "warm_type",
    "requester",
    "provider",
    "key",
    "summary_discovery_delay_ns",
    "summary_return_delay_ns",
    "summary_miss_latency_ns",
    "summary_provider_hit_hop",
    "request_start_ts_ns",
    "provider_hit_ts_ns",
    "provider_hit_request_id",
    "last_data_ts_ns",
    "last_data_kind",
    "last_data_node",
    "last_data_dest",
    "last_data_request_id",
    "requester_neighbors",
    "data_to_requester_neighbor",
    "data_to_requester_neighbor_ts_ns",
    "data_to_requester_neighbor_line",
    "data_to_requester",
    "data_to_requester_ts_ns",
    "data_to_requester_line",
    "data_to_requester_request_id",
    "requester_data_receive",
    "requester_data_receive_request_id",
    "requester_app_receive",
    "requester_app_receive_request_id",
    "query_table_match_seen",
    "query_table_expired_seen",
    "obs_request_ids",
    "likely_query_table_expired",
    "evidence_note",
    "last_data_line",
]

COMPLETION_REQUIRED_FIELDS = [
    "discovery_delay_ns",
    "return_delay_ns",
    "miss_latency_ns",
    "provider_hit_hop",
]


@dataclass(frozen=True)
class LogEvent:
    ts_ns: int
    node: Optional[int]
    kind: str
    key: Optional[str]
    dest: Optional[int]
    line: str
    request_id: Optional[str] = None


@dataclass
class SummaryInstance:
    row: Dict[str, str]
    start: Optional[LogEvent]
    end_ns: Optional[int] = None


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in OUTPUT_COLUMNS})


def int_or_none(value: str) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_missing_completion(row: Dict[str, str]) -> bool:
    return any(not row.get(field) for field in COMPLETION_REQUIRED_FIELDS)


def iter_log_events(logs_dir: Path) -> Iterable[LogEvent]:
    for path in sorted(logs_dir.glob("*.log")):
        fallback_node = node_from_path(path)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                ts_ns, msg = parse_timestamped_line(raw_line)
                if ts_ns is None:
                    continue

                node = node_from_line(msg, fallback_node)
                obs_fields = parse_obs_fields(msg)
                if obs_fields:
                    obs_event = obs_fields.get("event", "")
                    obs_key = obs_fields.get("key")
                    obs_node = int_or_none(obs_fields.get("node", "")) or node
                    obs_dest = int_or_none(obs_fields.get("dest", ""))
                    obs_request_id = obs_fields.get("request_id") or None
                    if obs_event:
                        yield LogEvent(
                            ts_ns,
                            obs_node,
                            obs_event,
                            obs_key,
                            obs_dest,
                            msg,
                            obs_request_id,
                        )
                    continue

                request = REQUEST_START_RE.search(msg)
                if request:
                    yield LogEvent(ts_ns, node, "request_start", request.group("key"), None, msg)

                send = DATA_SEND_RE.search(msg)
                if send:
                    yield LogEvent(ts_ns, node, "data_send", send.group("key"), int(send.group("dest")), msg)

                forward = DATA_FORWARD_RE.search(msg)
                if forward:
                    yield LogEvent(
                        ts_ns,
                        node,
                        "data_forward",
                        forward.group("key"),
                        int(forward.group("dest")),
                        msg,
                    )

                receive = DATA_RECEIVE_RE.search(msg)
                if receive:
                    yield LogEvent(ts_ns, node, "data_receive", receive.group("key"), None, msg)

                app = APP_RECEIVE_RE.search(msg)
                if app:
                    yield LogEvent(ts_ns, node, "app_receive", app.group("key"), None, msg)


def resolve_run_dirs(input_path: Path) -> List[Tuple[Path, Dict[str, str]]]:
    if (input_path / "summary.csv").exists():
        return [(input_path, {"run_id": input_path.name})]

    manifest_path = input_path / "manifest.csv"
    if manifest_path.exists():
        run_dirs: List[Tuple[Path, Dict[str, str]]] = []
        for row in read_csv(manifest_path):
            summary_path = Path(row.get("summary_path", ""))
            if not summary_path:
                continue
            run_dir = (input_path / summary_path).parent
            run_dirs.append((run_dir, row))
        return run_dirs

    runs_dir = input_path / "runs"
    if runs_dir.exists():
        return [
            (summary.parent, {"run_id": summary.parent.name})
            for summary in sorted(runs_dir.glob("*/summary.csv"))
        ]

    raise SystemExit(f"cannot find summary.csv, manifest.csv, or runs/*/summary.csv under {input_path}")


def pair_summary_rows_with_starts(
    summary_rows: List[Dict[str, str]], events: List[LogEvent]
) -> List[SummaryInstance]:
    starts_by_request: Dict[Tuple[Optional[int], str], Deque[LogEvent]] = defaultdict(deque)
    for event in events:
        if event.kind == "request_start" and event.key:
            starts_by_request[(event.node, event.key)].append(event)

    instances: List[SummaryInstance] = []
    for row in summary_rows:
        requester = int_or_none(row.get("requester", ""))
        key = row.get("key", "")
        start_queue = starts_by_request.get((requester, key), deque())
        start = start_queue.popleft() if start_queue else None
        instances.append(SummaryInstance(row=row, start=start))

    starts_by_key: Dict[str, List[SummaryInstance]] = defaultdict(list)
    for instance in instances:
        if instance.start is None:
            continue
        starts_by_key[instance.row.get("key", "")].append(instance)

    for key_instances in starts_by_key.values():
        key_instances.sort(key=lambda item: item.start.ts_ns if item.start else -1)
        for index, instance in enumerate(key_instances):
            if index + 1 < len(key_instances) and key_instances[index + 1].start:
                instance.end_ns = key_instances[index + 1].start.ts_ns - 1
    return instances


def in_window(event: LogEvent, start_ns: int, end_ns: Optional[int]) -> bool:
    if event.ts_ns < start_ns:
        return False
    return end_ns is None or event.ts_ns <= end_ns


def event_ts(event: Optional[LogEvent]) -> str:
    return str(event.ts_ns) if event is not None else ""


def bool_text(value: bool) -> str:
    return "yes" if value else "no"


def diagnose_instance(
    *,
    batch_id: str,
    run_id: str,
    manifest_row: Dict[str, str],
    instance: SummaryInstance,
    events: List[LogEvent],
    graph: Dict[int, List[int]],
) -> Dict[str, str]:
    row = instance.row
    requester = int_or_none(row.get("requester", ""))
    key = row.get("key", "")
    requester_neighbors = sorted(graph.get(requester or -1, []))

    if instance.start is None:
        return build_output_row(
            batch_id=batch_id,
            run_id=run_id,
            manifest_row=manifest_row,
            summary_row=row,
            request_start_ts_ns="",
            provider_hit=None,
            last_data=None,
            requester_neighbors=requester_neighbors,
            data_to_requester_neighbor=False,
            data_to_requester_neighbor_event=None,
            data_to_requester=False,
            data_to_requester_event=None,
            requester_data_receive=False,
            requester_data_receive_event=None,
            requester_app_receive=False,
            requester_app_receive_event=None,
            query_table_match_seen=False,
            query_table_expired_seen=False,
            obs_request_ids=[],
            likely_query_table_expired=False,
            evidence_note="request_start_not_found_in_logs",
        )

    window_events = [
        event
        for event in events
        if (event.key == key or event.key is None) and in_window(event, instance.start.ts_ns, instance.end_ns)
    ]
    provider_hit = next(
        (event for event in window_events if event.kind in {"provider_hit", "data_send"}),
        None,
    )
    candidate_request_ids = {
        event.request_id
        for event in window_events
        if event.request_id and (provider_hit is None or event.request_id == provider_hit.request_id)
    }
    if provider_hit and provider_hit.request_id:
        candidate_request_ids = {provider_hit.request_id}

    def same_request(event: LogEvent) -> bool:
        return not candidate_request_ids or event.request_id in candidate_request_ids

    data_transfers = [
        event
        for event in window_events
        if event.kind in {"data_send", "data_forward", "data_receive"} and same_request(event)
    ]
    last_data = data_transfers[-1] if data_transfers else None
    requester_neighbor_set = set(requester_neighbors)
    data_to_requester_neighbor = any(
        (event.dest in requester_neighbor_set) or (event.node in requester_neighbor_set)
        for event in window_events
        if event.kind in {"data_send", "data_forward", "data_receive"} and same_request(event)
    )
    data_to_requester_neighbor_event = next(
        (
            event
            for event in window_events
            if event.kind in {"data_send", "data_forward", "data_receive"}
            and same_request(event)
            and ((event.dest in requester_neighbor_set) or (event.node in requester_neighbor_set))
        ),
        None,
    )
    data_to_requester = any(
        event.dest == requester
        for event in window_events
        if event.kind in {"data_send", "data_forward"} and same_request(event)
    )
    data_to_requester_event = next(
        (
            event
            for event in window_events
            if event.kind in {"data_send", "data_forward"} and same_request(event) and event.dest == requester
        ),
        None,
    )
    requester_data_receive = any(
        event.kind == "data_receive" and event.node == requester and same_request(event)
        for event in window_events
    )
    requester_data_receive_event = next(
        (
            event
            for event in window_events
            if event.kind == "data_receive" and event.node == requester and same_request(event)
        ),
        None,
    )
    requester_app_receive = any(
        event.kind == "app_receive" and event.node == requester and same_request(event)
        for event in window_events
    )
    requester_app_receive_event = next(
        (
            event
            for event in window_events
            if event.kind == "app_receive" and event.node == requester and same_request(event)
        ),
        None,
    )
    query_table_match_seen = any(event.kind == "query_table_match" for event in window_events)
    query_table_expired_seen = any(
        event.kind == "query_table_expired" for event in window_events
    )

    likely_query_table_expired = (
        query_table_expired_seen
        or provider_hit is not None
        and not requester_app_receive
        and not row.get("miss_latency_ns")
        and (data_to_requester_neighbor or data_to_requester or last_data is not None)
    )

    if requester_app_receive:
        note = "app_receive_seen_but_summary_marked_missing"
    elif requester_data_receive:
        note = "requester_data_receive_without_app_receive"
    elif data_to_requester:
        note = "data_sent_to_requester_without_receive_log_possible_late_or_lost"
    elif data_to_requester_neighbor:
        note = "data_reached_requester_neighbor_but_not_requester_possible_query_table_expired"
    elif last_data is not None:
        note = "data_return_visible_but_not_near_requester"
    elif provider_hit is not None:
        note = "provider_hit_without_observed_data_return"
    elif query_table_expired_seen:
        note = "query_table_expired_without_provider_or_data_return"
    else:
        note = "no_provider_or_data_return_seen"

    return build_output_row(
        batch_id=batch_id,
        run_id=run_id,
        manifest_row=manifest_row,
        summary_row=row,
        request_start_ts_ns=str(instance.start.ts_ns),
        provider_hit=provider_hit,
        last_data=last_data,
        requester_neighbors=requester_neighbors,
        data_to_requester_neighbor=data_to_requester_neighbor,
        data_to_requester_neighbor_event=data_to_requester_neighbor_event,
        data_to_requester=data_to_requester,
        data_to_requester_event=data_to_requester_event,
        requester_data_receive=requester_data_receive,
        requester_data_receive_event=requester_data_receive_event,
        requester_app_receive=requester_app_receive,
        requester_app_receive_event=requester_app_receive_event,
        query_table_match_seen=query_table_match_seen,
        query_table_expired_seen=query_table_expired_seen,
        obs_request_ids=sorted(candidate_request_ids),
        likely_query_table_expired=likely_query_table_expired,
        evidence_note=note,
    )


def build_output_row(
    *,
    batch_id: str,
    run_id: str,
    manifest_row: Dict[str, str],
    summary_row: Dict[str, str],
    request_start_ts_ns: str,
    provider_hit: Optional[LogEvent],
    last_data: Optional[LogEvent],
    requester_neighbors: Sequence[int],
    data_to_requester_neighbor: bool,
    data_to_requester_neighbor_event: Optional[LogEvent],
    data_to_requester: bool,
    data_to_requester_event: Optional[LogEvent],
    requester_data_receive: bool,
    requester_data_receive_event: Optional[LogEvent],
    requester_app_receive: bool,
    requester_app_receive_event: Optional[LogEvent],
    query_table_match_seen: bool,
    query_table_expired_seen: bool,
    obs_request_ids: Sequence[str],
    likely_query_table_expired: bool,
    evidence_note: str,
) -> Dict[str, str]:
    return {
        "batch_id": batch_id,
        "run_id": run_id,
        "scenario": summary_row.get("scenario") or manifest_row.get("scenario", ""),
        "env": summary_row.get("env", ""),
        "topology": summary_row.get("topology") or manifest_row.get("topology", ""),
        "phase": summary_row.get("phase", ""),
        "request_index": summary_row.get("request_index", ""),
        "warm_type": summary_row.get("warm_type", ""),
        "requester": summary_row.get("requester", ""),
        "provider": summary_row.get("provider", ""),
        "key": summary_row.get("key", ""),
        "summary_discovery_delay_ns": summary_row.get("discovery_delay_ns", ""),
        "summary_return_delay_ns": summary_row.get("return_delay_ns", ""),
        "summary_miss_latency_ns": summary_row.get("miss_latency_ns", ""),
        "summary_provider_hit_hop": summary_row.get("provider_hit_hop", ""),
        "request_start_ts_ns": request_start_ts_ns,
        "provider_hit_ts_ns": event_ts(provider_hit),
        "provider_hit_request_id": provider_hit.request_id if provider_hit and provider_hit.request_id else "",
        "last_data_ts_ns": event_ts(last_data),
        "last_data_kind": last_data.kind if last_data else "",
        "last_data_node": str(last_data.node) if last_data and last_data.node is not None else "",
        "last_data_dest": str(last_data.dest) if last_data and last_data.dest is not None else "",
        "last_data_request_id": last_data.request_id if last_data and last_data.request_id else "",
        "requester_neighbors": ";".join(str(node) for node in requester_neighbors),
        "data_to_requester_neighbor": bool_text(data_to_requester_neighbor),
        "data_to_requester_neighbor_ts_ns": event_ts(data_to_requester_neighbor_event),
        "data_to_requester_neighbor_line": data_to_requester_neighbor_event.line
        if data_to_requester_neighbor_event
        else "",
        "data_to_requester": bool_text(data_to_requester),
        "data_to_requester_ts_ns": event_ts(data_to_requester_event),
        "data_to_requester_line": data_to_requester_event.line if data_to_requester_event else "",
        "data_to_requester_request_id": data_to_requester_event.request_id
        if data_to_requester_event and data_to_requester_event.request_id
        else "",
        "requester_data_receive": bool_text(requester_data_receive),
        "requester_data_receive_request_id": requester_data_receive_event.request_id
        if requester_data_receive_event and requester_data_receive_event.request_id
        else "",
        "requester_app_receive": bool_text(requester_app_receive),
        "requester_app_receive_request_id": requester_app_receive_event.request_id
        if requester_app_receive_event and requester_app_receive_event.request_id
        else "",
        "query_table_match_seen": bool_text(query_table_match_seen),
        "query_table_expired_seen": bool_text(query_table_expired_seen),
        "obs_request_ids": ";".join(obs_request_ids),
        "likely_query_table_expired": bool_text(likely_query_table_expired),
        "evidence_note": evidence_note,
        "last_data_line": last_data.line if last_data else "",
    }


def diagnose_run(
    *,
    batch_id: str,
    run_dir: Path,
    manifest_row: Dict[str, str],
    phase: str,
) -> List[Dict[str, str]]:
    summary_rows = read_csv(run_dir / "summary.csv")
    events = sorted(iter_log_events(run_dir / "logs"), key=lambda event: event.ts_ns)
    graph = parse_topology(run_dir / "topology.yaml")
    instances = pair_summary_rows_with_starts(summary_rows, events)
    run_id = manifest_row.get("run_id") or run_dir.name

    diagnostics: List[Dict[str, str]] = []
    for instance in instances:
        if phase and instance.row.get("phase", "") != phase:
            continue
        if not is_missing_completion(instance.row):
            continue
        diagnostics.append(
            diagnose_instance(
                batch_id=batch_id,
                run_id=run_id,
                manifest_row=manifest_row,
                instance=instance,
                events=events,
                graph=graph,
            )
        )
    return diagnostics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Run directory or batch result directory")
    parser.add_argument("--output", type=Path, help="Output CSV path")
    parser.add_argument(
        "--phase",
        default="measurement",
        help="Only diagnose this phase; use an empty string to include all phases",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input
    batch_id = input_path.name if (input_path / "manifest.csv").exists() else ""
    run_dirs = resolve_run_dirs(input_path)
    rows: List[Dict[str, str]] = []
    for run_dir, manifest_row in run_dirs:
        rows.extend(
            diagnose_run(
                batch_id=batch_id,
                run_dir=run_dir,
                manifest_row=manifest_row,
                phase=args.phase,
            )
        )

    output = args.output or (input_path / "completion_diagnostics.csv")
    write_csv(output, rows)
    print(f"wrote {len(rows)} completion diagnostic rows to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
