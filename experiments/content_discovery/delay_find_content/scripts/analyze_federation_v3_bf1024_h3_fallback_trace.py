#!/usr/bin/env python3
"""Join fallback-pilot request results with traces and staged-search metrics."""

from __future__ import annotations

import argparse
import bisect
import csv
from pathlib import Path

import analyze_federation_v3_bf2048_h3_trace as base


FALLBACK_COLUMNS = [
    "packet_request_id", "normal_request_frames", "normal_request_bytes", "fallback_request_frames",
    "fallback_request_bytes", "query_route_selected_events",
    "immediate_dead_end_fallback_events", "origin_fallback_armed_events",
    "origin_fallback_triggered_events", "fallback_forward_events",
    "query_key_satisfied_events", "query_complete_events",
    "duplicate_request_suppressed_events", "query_expired_unsatisfied_events",
    "application_delivery_events", "fallback_triggered", "fallback_recovery_latency_ns",
]
COLUMNS = base.COLUMNS + FALLBACK_COLUMNS


def number(value: object) -> int:
    return base.number(value)


def event_bytes(event: dict, default: int) -> int:
    return number(event.get("frame_bytes")) or number(event.get("value_bytes")) or default


def count(window: list[dict], name: str) -> int:
    return sum(event.get("event") == name for event in window)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--traces", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--request-frame-bytes", type=int, default=166)
    args = parser.parse_args()
    with args.results.open(newline="", encoding="utf-8") as handle:
        requests = list(csv.DictReader(handle))
    timestamps, trace = base.load_events(args.traces)
    rows = []
    for request in requests:
        if request.get("status") != "ok":
            continue
        try:
            start = int(request["request_start_mono_ns"])
            end = int(request["app_receive_mono_ns"])
        except (KeyError, TypeError, ValueError):
            continue
        window = trace[bisect.bisect_left(timestamps, start):bisect.bisect_right(timestamps, end)]
        key = request["key"]
        requester, owner = int(request["requester"]), int(request["owner"])
        hits = [event for event in window if event.get("event") == "provider_hit" and event.get("key") == key]
        unrelated_hits = [event for event in window if event.get("event") == "provider_hit"
                          and event.get("key") not in ("", key)]
        unrelated_data = [event for event in window if event.get("event") == "data_handle_start"
                          and event.get("key") not in ("", key)]
        hit = min(hits, key=lambda event: event["_ts"]) if hits else None
        provider = number(hit.get("node")) if hit else 0
        normal = [event for event in window if event.get("event") in
                  ("request_send_done", "request_forward_done")
                  and str(event.get("fallback", "0")).lower() not in ("1", "true")]
        fallback = [event for event in window if event.get("event") == "fallback_forward"]
        cross = [event for event in window
                 if {number(event.get("src")), number(event.get("dest"))} == {1, 33}
                 and event.get("event") in ("request_send_done", "request_forward_done",
                                              "fallback_forward", "data_send_done", "data_forward_done")]
        request_cross = [event for event in cross if event.get("event") in
                         ("request_send_done", "request_forward_done", "fallback_forward")]
        data_cross = [event for event in cross if event.get("event") in
                      ("data_send_done", "data_forward_done")]
        path = base.overlay_path(requester, provider) if provider else []
        triggered = [event for event in window if event.get("event") == "origin_fallback_triggered"]
        trigger_ts = min((event["_ts"] for event in triggered), default=0)
        packet_request_id = str(request.get("packet_request_id", ""))
        delivery_scope = trace if packet_request_id not in ("", "0") else window
        deliveries = [event for event in delivery_scope if event.get("event") == "app_receive"
                      and event.get("key") == key and number(event.get("node")) == requester
                      and (packet_request_id in ("", "0") or
                           str(event.get("request_id", "")) == packet_request_id)]

        row = {column: request.get(column, "") for column in
               ("request_index", "phase", "bin", "round", "request_class", "direction",
                "key", "key_rank", "bloom_positions", "requester", "owner")}
        hit_ts = hit["_ts"] if hit else 0
        row.update({
            "provider": provider or "",
            "service_class": "original_owner" if provider == owner else "cache" if provider else "",
            "request_start_mono_ns": start,
            "provider_hit_mono_ns": hit_ts or "",
            "app_receive_mono_ns": end,
            "discovery_latency_ns": hit_ts - start if hit else "",
            "return_path_latency_ns": end - hit_ts if hit else "",
            "end_to_end_latency_ns": end - start,
            "request_forward_events": len(normal) + len(fallback),
            "request_frame_bytes": sum(event_bytes(event, args.request_frame_bytes) for event in normal + fallback),
            "serving_overlay_hops": len(path) - 1 if path else "",
            "serving_cross_cluster": int(bool(provider) and (requester <= 32) != (provider <= 32)),
            "serving_edge_physical_signature": base.topology.physical_signature(path) if path else "",
            "requester_rack": base.rack(requester),
            "provider_rack": base.rack(provider) if provider else "",
            "rack_relation": ("cross_cluster" if provider and (requester <= 32) != (provider <= 32)
                              else "same_rack" if provider and base.rack(requester) == base.rack(provider)
                              else "cross_rack" if provider else ""),
            "cross_link_request_frames": len(request_cross),
            "cross_link_data_frames": len(data_cross),
            "cross_link_request_bytes": sum(event_bytes(event, args.request_frame_bytes) for event in request_cross),
            "cross_link_data_bytes": sum(number(event.get("value_bytes")) for event in data_cross),
            "correct_provider_hit_events": len(hits),
            "unrelated_provider_hit_events": len(unrelated_hits),
            "unrelated_data_handle_events": len(unrelated_data),
            "unrelated_data_bytes": sum(number(event.get("value_bytes")) for event in unrelated_data),
            "trace_complete": int(bool(hit)),
            "packet_request_id": packet_request_id,
            "normal_request_frames": len(normal),
            "normal_request_bytes": sum(event_bytes(event, args.request_frame_bytes) for event in normal),
            "fallback_request_frames": len(fallback),
            "fallback_request_bytes": sum(event_bytes(event, args.request_frame_bytes) for event in fallback),
            "query_route_selected_events": count(window, "query_route_selected"),
            "immediate_dead_end_fallback_events": count(window, "immediate_dead_end_fallback"),
            "origin_fallback_armed_events": count(window, "origin_fallback_armed"),
            "origin_fallback_triggered_events": len(triggered),
            "fallback_forward_events": len(fallback),
            "query_key_satisfied_events": count(window, "query_key_satisfied"),
            "query_complete_events": count(window, "query_complete"),
            "duplicate_request_suppressed_events": count(window, "duplicate_request_suppressed"),
            "query_expired_unsatisfied_events": count(window, "query_expired_unsatisfied"),
            "application_delivery_events": len(deliveries),
            "fallback_triggered": int(bool(triggered)),
            "fallback_recovery_latency_ns": end - trigger_ts if trigger_ts else "",
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
