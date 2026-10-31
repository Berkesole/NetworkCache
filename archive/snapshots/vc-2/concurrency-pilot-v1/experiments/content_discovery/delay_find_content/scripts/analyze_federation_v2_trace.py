#!/usr/bin/env python3
"""Join federation request results with monotonic NetworkCache trace events."""

from __future__ import annotations

import argparse
import csv
import json
from collections import deque
from pathlib import Path

import generate_federation_v2 as generator


COLUMNS = ["request_index", "phase", "round", "request_class", "direction", "key", "requester",
           "owner", "provider", "service_class", "request_start_mono_ns",
           "provider_hit_mono_ns", "app_receive_mono_ns", "discovery_latency_ns",
           "return_path_latency_ns", "end_to_end_latency_ns", "request_forward_events",
           "request_frame_bytes", "serving_overlay_hops", "serving_cross_cluster",
           "serving_edge_physical_signature",
           "requester_rack", "provider_rack", "rack_relation", "cross_link_request_frames",
           "cross_link_data_frames", "cross_link_request_bytes", "cross_link_data_bytes",
           "trace_complete"]


def overlay_hops(start: int, target: int) -> int | None:
    adjacency = {node: list(peers) for node, peers in generator.graph().items()}
    adjacency[1].append(33); adjacency[33].append(1)
    queue = deque([(start, 0)]); seen = {start}
    while queue:
        node, distance = queue.popleft()
        if node == target: return distance
        for peer in adjacency.get(node, []):
            if peer not in seen: seen.add(peer); queue.append((peer, distance + 1))
    return None


def rack(node: int) -> str:
    local = (node - 1) % 32
    return f"{'A' if node <= 32 else 'B'}-r{local // 4 + 1}"


def events(root: Path) -> list[dict]:
    output = []
    for path in sorted(root.glob("*.jsonl")):
        for line in path.read_text(errors="replace").splitlines():
            try: output.append(json.loads(line))
            except json.JSONDecodeError: pass
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--traces", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.results.open(newline="") as handle: requests = list(csv.DictReader(handle))
    trace = events(args.traces); rows = []
    for request in requests:
        if request.get("status") != "ok" or not request.get("request_start_mono_ns") or not request.get("app_receive_mono_ns"):
            continue
        start, end = int(request["request_start_mono_ns"]), int(request["app_receive_mono_ns"])
        key = request["key"]
        window = [event for event in trace if start <= int(event.get("ts_mono_ns", 0)) <= end]
        hits = [event for event in window if event.get("event") == "provider_hit" and event.get("key") == key]
        hit = min(hits, key=lambda event: int(event["ts_mono_ns"])) if hits else None
        provider = int(hit["node"]) if hit else 0; owner = int(request["owner"])
        def numeric(value: object) -> int:
            text = str(value or "")
            return int(text) if text.isdigit() else 0
        cross = [event for event in window if {numeric(event.get("src")), numeric(event.get("dest"))} == {1, 33}
                 and event.get("event") in ("request_send_done", "request_forward_done", "data_send_done", "data_forward_done")]
        request_events = [event for event in window if event.get("event") in
                          ("request_send_done", "request_forward_done")]
        request_frames = sum("request" in event["event"] for event in cross)
        data_frames = sum("data" in event["event"] for event in cross)
        request_bytes = sum(int(event.get("value_bytes") or 158) for event in cross if "request" in event["event"])
        data_bytes = sum(int(event.get("value_bytes") or 0) for event in cross if "data" in event["event"])
        requester = int(request["requester"])
        row = {key: request.get(key, "") for key in ("request_index", "phase", "round", "request_class", "direction", "key", "requester", "owner")}
        row.update({"provider": provider or "", "service_class": "original_owner" if provider == owner else "cache" if provider else "",
                    "request_start_mono_ns": start, "provider_hit_mono_ns": hit["ts_mono_ns"] if hit else "",
                    "app_receive_mono_ns": end, "discovery_latency_ns": int(hit["ts_mono_ns"]) - start if hit else "",
                    "return_path_latency_ns": end - int(hit["ts_mono_ns"]) if hit else "",
                    "end_to_end_latency_ns": end - start,
                    "request_forward_events": len(request_events),
                    "request_frame_bytes": len(request_events) * 158,
                    "serving_overlay_hops": overlay_hops(requester, provider) if provider else "",
                    "serving_edge_physical_signature": generator.physical_signature(
                        generator.shortest_overlay_path(requester, provider, federated=True)) if provider else "",
                    "serving_cross_cluster": int(bool(provider) and (requester <= 32) != (provider <= 32)),
                    "requester_rack": rack(requester), "provider_rack": rack(provider) if provider else "",
                    "rack_relation": "cross_cluster" if provider and (requester <= 32) != (provider <= 32)
                                     else "same_rack" if provider and rack(requester) == rack(provider)
                                     else "cross_rack" if provider else "",
                    "cross_link_request_frames": request_frames,
                    "cross_link_data_frames": data_frames, "cross_link_request_bytes": request_bytes,
                    "cross_link_data_bytes": data_bytes, "trace_complete": 1 if hit else 0})
        rows.append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS); writer.writeheader(); writer.writerows(rows)
    return 0


if __name__ == "__main__": raise SystemExit(main())
