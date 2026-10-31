#!/usr/bin/env python3
"""Join large-pilot request results to traces without an O(requests * events) scan."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
from collections import deque
from pathlib import Path

import generate_federation_v2 as topology


COLUMNS = [
    "request_index", "phase", "bin", "round", "request_class", "direction", "key",
    "key_rank", "bloom_positions", "requester", "owner", "provider", "service_class",
    "request_start_mono_ns", "provider_hit_mono_ns", "app_receive_mono_ns",
    "discovery_latency_ns", "return_path_latency_ns", "end_to_end_latency_ns",
    "request_forward_events", "request_frame_bytes", "serving_overlay_hops",
    "serving_cross_cluster", "serving_edge_physical_signature", "requester_rack",
    "provider_rack", "rack_relation", "cross_link_request_frames", "cross_link_data_frames",
    "cross_link_request_bytes", "cross_link_data_bytes", "correct_provider_hit_events",
    "unrelated_provider_hit_events", "unrelated_data_handle_events",
    "unrelated_data_bytes", "trace_complete",
]


def graph() -> dict[int, list[int]]:
    output = {node: list(peers) for node, peers in topology.graph().items()}
    output[1].append(33)
    output[33].append(1)
    return output


def overlay_path(start: int, target: int) -> list[int]:
    queue = deque([[start]])
    seen = {start}
    adjacency = graph()
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path
        for peer in adjacency.get(path[-1], []):
            if peer not in seen:
                seen.add(peer)
                queue.append(path + [peer])
    return []


def rack(node: int) -> str:
    local = (node - 1) % 32
    return f"{'A' if node <= 32 else 'B'}-r{local // 4 + 1}"


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


def number(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--traces", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--request-frame-bytes", type=int, default=286)
    args = parser.parse_args()
    with args.results.open(newline="", encoding="utf-8") as handle:
        requests = list(csv.DictReader(handle))
    timestamps, trace = load_events(args.traces)
    rows = []
    for request in requests:
        if request.get("status") != "ok":
            continue
        try:
            start = int(request["request_start_mono_ns"])
            end = int(request["app_receive_mono_ns"])
        except (KeyError, TypeError, ValueError):
            continue
        left = bisect.bisect_left(timestamps, start)
        right = bisect.bisect_right(timestamps, end)
        window = trace[left:right]
        key = request["key"]
        all_hits = [event for event in window if event.get("event") == "provider_hit"]
        hits = [event for event in all_hits if event.get("key") == key]
        unrelated_hits = [event for event in all_hits if event.get("key") not in ("", key)]
        unrelated_data = [event for event in window if event.get("event") == "data_handle_start"
                          and event.get("key") not in ("", key)]
        hit = min(hits, key=lambda event: event["_ts"]) if hits else None
        provider = number(hit.get("node")) if hit else 0
        requester, owner = int(request["requester"]), int(request["owner"])
        request_events = [event for event in window if event.get("event") in
                          ("request_send_done", "request_forward_done")]
        cross = [event for event in window
                 if {number(event.get("src")), number(event.get("dest"))} == {1, 33}
                 and event.get("event") in ("request_send_done", "request_forward_done",
                                              "data_send_done", "data_forward_done")]
        request_cross = [event for event in cross if "request" in str(event.get("event"))]
        data_cross = [event for event in cross if "data" in str(event.get("event"))]
        path = overlay_path(requester, provider) if provider else []
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
            "request_forward_events": len(request_events),
            "request_frame_bytes": len(request_events) * args.request_frame_bytes,
            "serving_overlay_hops": len(path) - 1 if path else "",
            "serving_cross_cluster": int(bool(provider) and (requester <= 32) != (provider <= 32)),
            "serving_edge_physical_signature": topology.physical_signature(path) if path else "",
            "requester_rack": rack(requester),
            "provider_rack": rack(provider) if provider else "",
            "rack_relation": ("cross_cluster" if provider and (requester <= 32) != (provider <= 32)
                              else "same_rack" if provider and rack(requester) == rack(provider)
                              else "cross_rack" if provider else ""),
            "cross_link_request_frames": len(request_cross),
            "cross_link_data_frames": len(data_cross),
            "cross_link_request_bytes": sum(number(event.get("value_bytes")) or args.request_frame_bytes
                                             for event in request_cross),
            "cross_link_data_bytes": sum(number(event.get("value_bytes")) for event in data_cross),
            "correct_provider_hit_events": len(hits),
            "unrelated_provider_hit_events": len(unrelated_hits),
            "unrelated_data_handle_events": len(unrelated_data),
            "unrelated_data_bytes": sum(number(event.get("value_bytes")) for event in unrelated_data),
            "trace_complete": int(bool(hit)),
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
