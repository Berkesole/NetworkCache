#!/usr/bin/env python3
"""Join latency-v4 requester results to trace events using explicit proxy labels."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
from collections import Counter
from pathlib import Path

import generate_latency_v4_bf1024_h3_c512 as workload


COLUMNS = [
    "request_index", "phase", "round", "bin", "key", "key_rank", "key_u",
    "requester", "owner", "requester_role", "owner_role",
    "original_owner_overlay_hops", "overlay_edge_physical_signature",
    "expected_value_bytes", "end_to_end_latency_ns", "packet_request_id",
    "first_observed_provider", "first_observed_service_class_proxy",
    "first_observed_provider_hit_mono_ns", "discovery_latency_proxy_ns",
    "return_path_latency_proxy_ns", "serving_overlay_hops_proxy",
    "serving_edge_physical_signature_proxy", "matching_provider_hit_events",
    "multiple_matching_provider_hits", "unrelated_provider_hit_events",
    "unrelated_data_events", "preferred_logical_request_events",
    "preferred_logical_request_bytes_equivalent", "fallback_logical_request_events",
    "fallback_logical_request_bytes_equivalent", "origin_fallback_triggered_events",
    "source_fallback_triggered", "fallback_trigger_to_delivery_ns",
    "immediate_expansion_events", "duplicate_request_suppressed_events",
    "query_expired_unsatisfied_events_in_delivery_window", "application_delivery_events",
    "trace_complete_proxy",
]


def number(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def load_events(root: Path) -> tuple[list[int], list[dict]]:
    events: list[dict] = []
    for path in sorted(root.glob("*.jsonl")):
        for line in path.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
                event["_ts"] = int(event.get("ts_mono_ns", 0))
                if event["_ts"]:
                    events.append(event)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    events.sort(key=lambda event: event["_ts"])
    return [event["_ts"] for event in events], events


def event_bytes(event: dict, default: int) -> int:
    return number(event.get("frame_bytes")) or default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--traces", required=True, type=Path)
    parser.add_argument("--topology", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--request-frame-bytes", type=int, default=166)
    args = parser.parse_args()
    with args.results.open(newline="", encoding="utf-8") as handle:
        requests = list(csv.DictReader(handle))
    timestamps, events = load_events(args.traces)
    delivery_counts = Counter(
        (str(event.get("key", "")), number(event.get("node")),
         str(event.get("request_id", "")))
        for event in events if event.get("event") == "app_receive"
    )
    graph = workload.graph(args.topology)
    rows: list[dict] = []
    for request in requests:
        if request.get("status") != "ok":
            continue
        start = number(request.get("request_start_mono_ns"))
        end = number(request.get("app_receive_mono_ns"))
        if not start or end < start:
            continue
        window = events[bisect.bisect_left(timestamps, start):bisect.bisect_right(timestamps, end)]
        key = request["key"]
        requester = int(request["requester"])
        owner = int(request["owner"])
        hits = [event for event in window if event.get("event") == "provider_hit"
                and event.get("key") == key]
        unrelated_hits = [event for event in window if event.get("event") == "provider_hit"
                          and event.get("key") not in ("", key)]
        unrelated_data = [event for event in window if event.get("event") == "data_handle_start"
                          and event.get("key") not in ("", key)]
        first = min(hits, key=lambda event: event["_ts"]) if hits else None
        provider = number(first.get("node")) if first else 0
        path = workload.shortest_path(graph, requester, provider) if provider else []
        physical = "".join("S" if workload.rack(a) == workload.rack(b) else "C"
                           for a, b in zip(path, path[1:]))
        preferred = [event for event in window if event.get("event") in
                     ("request_send_done", "request_forward_done")
                     and str(event.get("fallback", "0")).lower() not in ("1", "true")]
        fallback = [event for event in window if event.get("event") == "fallback_forward"]
        triggered = [event for event in window if event.get("event") == "origin_fallback_triggered"
                     and number(event.get("node")) == requester]
        trigger_ts = min((event["_ts"] for event in triggered), default=0)
        packet_request_id = str(request.get("packet_request_id", ""))
        delivery_count = (
            delivery_counts[(key, requester, packet_request_id)]
            if packet_request_id not in ("", "0") else
            sum(event.get("event") == "app_receive" and event.get("key") == key
                and number(event.get("node")) == requester for event in window)
        )
        hit_ts = first["_ts"] if first else 0
        row = {column: request.get(column, "") for column in COLUMNS}
        row.update({
            "end_to_end_latency_ns": end - start,
            "packet_request_id": packet_request_id,
            "first_observed_provider": provider or "",
            "first_observed_service_class_proxy": (
                "original_owner" if provider == owner else "cache" if provider else ""),
            "first_observed_provider_hit_mono_ns": hit_ts or "",
            "discovery_latency_proxy_ns": hit_ts - start if first else "",
            "return_path_latency_proxy_ns": end - hit_ts if first else "",
            "serving_overlay_hops_proxy": len(path) - 1 if path else "",
            "serving_edge_physical_signature_proxy": physical,
            "matching_provider_hit_events": len(hits),
            "multiple_matching_provider_hits": int(len(hits) > 1),
            "unrelated_provider_hit_events": len(unrelated_hits),
            "unrelated_data_events": len(unrelated_data),
            "preferred_logical_request_events": len(preferred),
            "preferred_logical_request_bytes_equivalent": sum(
                event_bytes(event, args.request_frame_bytes) for event in preferred),
            "fallback_logical_request_events": len(fallback),
            "fallback_logical_request_bytes_equivalent": sum(
                event_bytes(event, args.request_frame_bytes) for event in fallback),
            "origin_fallback_triggered_events": len(triggered),
            "source_fallback_triggered": int(bool(triggered)),
            "fallback_trigger_to_delivery_ns": end - trigger_ts if trigger_ts else "",
            "immediate_expansion_events": sum(event.get("event") == "immediate_dead_end_fallback"
                                              for event in window),
            "duplicate_request_suppressed_events": sum(
                event.get("event") == "duplicate_request_suppressed" for event in window),
            "query_expired_unsatisfied_events_in_delivery_window": sum(
                event.get("event") == "query_expired_unsatisfied" for event in window),
            "application_delivery_events": delivery_count,
            "trace_complete_proxy": int(bool(first)),
        })
        rows.append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
