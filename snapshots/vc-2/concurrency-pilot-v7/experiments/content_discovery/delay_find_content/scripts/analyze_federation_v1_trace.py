#!/usr/bin/env python3
"""Join federation request results with monotonic NetworkCache trace events."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


COLUMNS = ["request_index", "phase", "round", "direction", "key", "requester",
           "owner", "provider", "service_class", "request_start_mono_ns",
           "provider_hit_mono_ns", "app_receive_mono_ns", "discovery_latency_ns",
           "return_path_latency_ns", "end_to_end_latency_ns", "cross_link_request_frames",
           "cross_link_data_frames", "cross_link_request_bytes", "cross_link_data_bytes",
           "trace_complete"]


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
        request_frames = sum("request" in event["event"] for event in cross)
        data_frames = sum("data" in event["event"] for event in cross)
        request_bytes = sum(int(event.get("value_bytes") or 158) for event in cross if "request" in event["event"])
        data_bytes = sum(int(event.get("value_bytes") or 0) for event in cross if "data" in event["event"])
        row = {key: request.get(key, "") for key in ("request_index", "phase", "round", "direction", "key", "requester", "owner")}
        row.update({"provider": provider or "", "service_class": "original_owner" if provider == owner else "cache" if provider else "",
                    "request_start_mono_ns": start, "provider_hit_mono_ns": hit["ts_mono_ns"] if hit else "",
                    "app_receive_mono_ns": end, "discovery_latency_ns": int(hit["ts_mono_ns"]) - start if hit else "",
                    "return_path_latency_ns": end - int(hit["ts_mono_ns"]) if hit else "",
                    "end_to_end_latency_ns": end - start, "cross_link_request_frames": request_frames,
                    "cross_link_data_frames": data_frames, "cross_link_request_bytes": request_bytes,
                    "cross_link_data_bytes": data_bytes, "trace_complete": 1 if hit else 0})
        rows.append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS); writer.writeheader(); writer.writerows(rows)
    return 0


if __name__ == "__main__": raise SystemExit(main())
