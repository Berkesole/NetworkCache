#!/usr/bin/env python3
"""Build clock-independent per-request Cross-host Federation latency components."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


COLUMNS = [
    "request_index", "method", "phase", "bin", "request_class", "direction",
    "key", "requester", "owner", "provider", "service_class",
    "packet_request_id", "end_to_end_ns", "requester_domain_egress_ns",
    "dci_roundtrip_residual_ns", "serving_domain_discovery_ns",
    "provider_processing_ns", "serving_domain_return_ns",
    "requester_domain_delivery_ns", "dci_request_frames", "dci_request_bytes",
    "dci_data_frames", "dci_data_bytes", "component_sum_ns", "closure_error_ns",
    "preferred_logical_request_events", "fallback_logical_request_events",
    "cache_service_fraction_proxy", "serving_overlay_hops_proxy",
    "trace_complete", "trace_reason",
]


def node_domain(node: int) -> str:
    return "A" if node <= 32 else "B"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(paths: Iterable[Path], domain: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                event["_domain"] = domain
                event["_path"] = str(path)
                result.append(event)
    return result


def number(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def matching_events(events: list[dict[str, Any]], *, domain: str, key: str,
                    request_id: str, request_index: str,
                    method: str) -> list[dict[str, Any]]:
    expected = request_id if method == "nc" else request_index
    return [
        event for event in events
        if event.get("_domain") == domain
        and str(event.get("key", "")) == key
        and str(event.get("request_id") if method == "nc"
                else event.get("request_index") or "") == expected
    ]


def index_traces(events: list[dict[str, Any]], method: str) \
        -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Index traces by the identity already enforced by matching_events."""
    result: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        identifier = str(
            event.get("request_id") if method == "nc"
            else event.get("request_index") or ""
        )
        result[(str(event.get("key", "")), identifier)].append(event)
    return result


def index_borders(events: list[dict[str, Any]]) \
        -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        result[str(event.get("request_index", ""))].append(event)
    return result


def latest_unique(events: list[dict[str, Any]], before_ns: int,
                  predicate) -> tuple[dict[str, Any] | None, str]:
    candidates = [event for event in events
                  if number(event.get("ts_mono_ns")) <= before_ns and predicate(event)]
    if not candidates:
        return None, "causal_predecessor_missing"
    latest_ns = max(number(event.get("ts_mono_ns")) for event in candidates)
    latest = [event for event in candidates
              if number(event.get("ts_mono_ns")) == latest_ns]
    if len(latest) != 1:
        return None, "causal_predecessor_ambiguous"
    return latest[0], "ok"


def provider_hit_for_send(events: list[dict[str, Any]], send: dict[str, Any]) \
        -> tuple[dict[str, Any] | None, str]:
    provider = number(send.get("node"))
    return latest_unique(
        events, number(send.get("ts_mono_ns")),
        lambda event: event.get("event") == "provider_hit"
        and number(event.get("node")) == provider,
    )


def reverse_data_chain(events: list[dict[str, Any]], outgoing: dict[str, Any],
                       domain: str) -> tuple[int, dict[str, Any] | None,
                                             dict[str, Any] | None, int, str]:
    """Walk the temporally accepted DATA chain back to its provider.

    The protocol may leave slower DATA copies in flight after the application
    accepts one copy.  An un-timestamped reachability graph therefore admits
    several providers.  Starting at the actual delivery/crossing edge and
    choosing the unique immediately preceding receive/send edge excludes those
    late branches without assuming that the first same-key provider won.
    """
    current = outgoing
    hops = 0
    visited: set[int] = set()
    while True:
        source = number(current.get("src")) or number(current.get("node"))
        destination = number(current.get("dest"))
        if not source or not destination or node_domain(source) != domain:
            return 0, None, None, hops, "causal_edge_invalid"
        if source in visited:
            return 0, None, None, hops, "causal_cycle"
        visited.add(source)
        if current.get("event") == "data_send_start":
            hit, reason = provider_hit_for_send(events, current)
            if hit is None:
                return 0, None, None, hops, reason
            return source, hit, current, hops + 1, "ok"
        if current.get("event") != "data_forward_start":
            return 0, None, None, hops, "causal_edge_type_invalid"
        handled, reason = latest_unique(
            events, number(current.get("ts_mono_ns")),
            lambda event: event.get("event") == "data_handle_start"
            and number(event.get("node")) == source
            and number(event.get("dest")) == source,
        )
        if handled is None:
            return 0, None, None, hops, reason
        previous = number(handled.get("src"))
        if previous and node_domain(previous) != domain:
            return 0, None, None, hops + 1, "remote_boundary"
        predecessor, reason = latest_unique(
            events, number(handled.get("ts_mono_ns")),
            lambda event: event.get("event") in ("data_send_start", "data_forward_start")
            and number(event.get("src")) == previous
            and number(event.get("dest")) == source,
        )
        if predecessor is None:
            return 0, None, None, hops, reason
        current = predecessor
        hops += 1


def accepted_delivery(events: list[dict[str, Any]], requester: int,
                      app_receive_ns: int, domain: str) \
        -> tuple[int, dict[str, Any] | None, dict[str, Any] | None, int, str]:
    app, reason = latest_unique(
        events, app_receive_ns,
        lambda event: event.get("event") == "app_receive"
        and number(event.get("node")) == requester,
    )
    if app is None:
        return 0, None, None, 0, reason
    previous = number(app.get("src"))
    if not previous:
        return 0, None, None, 0, "delivery_source_missing"
    if node_domain(previous) != domain:
        return 0, None, None, 0, "remote_boundary"
    outgoing, reason = latest_unique(
        events, number(app.get("ts_mono_ns")),
        lambda event: event.get("event") in ("data_send_start", "data_forward_start")
        and number(event.get("src")) == previous
        and number(event.get("dest")) == requester,
    )
    if outgoing is None:
        return 0, None, None, 0, reason
    return reverse_data_chain(events, outgoing, domain)


def border_packets(events: list[dict[str, Any]], request_index: str,
                   role: str, direction: str, logical_type: str,
                   minimum_stream_bytes: int = 1) -> list[dict[str, Any]]:
    candidates = sorted(
        [event for event in events
         if str(event.get("request_index", "")) == request_index
         and event.get("role") == role and event.get("direction") == direction
         and event.get("logical_packet_type") == logical_type
         and event.get("wire_stream")
         and (number(event.get("transport_payload_bytes")) > 0
              or ("tcp_sequence" in event and number(event.get("tcp_flags")) & 1))],
        key=lambda event: number(event.get("ts_mono_ns")),
    )
    streams: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in candidates:
        streams[str(event["wire_stream"])].append(event)
    # The observer deliberately does not decode the application frame.  During
    # an NC request, a short reverse-direction control copy can therefore be
    # labelled DATA by direction alone.  A real DATA stream must carry at least
    # the frozen value bytes; reject header-only streams before choosing the
    # first accepted boundary crossing.  This also prevents a stale 166-byte
    # control stream from being paired with a later DATA trace edge.
    if minimum_stream_bytes > 1:
        streams = {key: first_complete_tcp_prefix(stream, minimum_stream_bytes)
                   for key, stream in streams.items()}
    complete = [
        stream for stream in streams.values()
        if sum(number(event.get("transport_payload_bytes")) for event in stream)
        >= minimum_stream_bytes
    ]
    if not complete:
        return []
    return min(complete, key=lambda stream: number(stream[0].get("ts_mono_ns")))


def first_complete_tcp_prefix(stream: list[dict[str, Any]], minimum_bytes: int = 1) -> list[dict[str, Any]]:
    """Use the first gap-free FIN boundary; never count retransmitted bytes twice.

    Legacy observers have no sequence evidence: preserve them for diagnostic
    validation, where an impossible boundary remains explicitly incomplete.
    """
    if not all("tcp_sequence" in event for event in stream):
        return stream
    if not stream:
        return []
    base = number(stream[0]["tcp_sequence"])
    intervals = []
    ends = {((number(event["tcp_sequence"]) - base) % (1 << 32))
            + number(event["transport_payload_bytes"])
            for event in stream if number(event.get("tcp_flags")) & 1}
    if len(ends) != 1:
        return []
    end = ends.pop()
    for index, event in enumerate(stream):
        start = (number(event["tcp_sequence"]) - base) % (1 << 32)
        if start >= (1 << 31):
            return []  # Earlier missing prefix cannot be established safely.
        stop = start + number(event["transport_payload_bytes"])
        intervals.append((start, stop))
        covered = 0
        for left, right in sorted(intervals):
            if left > covered:
                break
            covered = max(covered, right)
        if covered == end:
            return stream[:index + 1] if end >= minimum_bytes else []
    return []


def decompose(request: dict[str, str], traces: list[dict[str, Any]],
              borders: list[dict[str, Any]], tolerance_ns: int) -> dict[str, Any]:
    index = request["request_index"]
    method = request.get("method", "nc")
    requester, owner = int(request["requester"]), int(request["owner"])
    requester_domain = node_domain(requester)
    serving_domain = node_domain(owner)
    row: dict[str, Any] = {column: request.get(column, "") for column in COLUMNS}
    end_to_end = number(request.get("end_to_end_latency_ns")) or (
        number(request.get("app_receive_mono_ns")) - number(request.get("request_start_mono_ns"))
    )
    row.update({"method": method, "end_to_end_ns": end_to_end,
                "trace_complete": 0, "trace_reason": "unknown"})
    if requester_domain == serving_domain:
        row["trace_reason"] = "local_control_not_decomposed"
        return row
    request_id = str(request.get("packet_request_id", ""))
    if method == "nc" and request_id == "0":
        local_hits = [
            event for event in traces
            if event.get("_domain") == requester_domain
            and event.get("event") == "provider_hit"
            and number(event.get("node")) == requester
            and event.get("key") == request["key"]
            and number(request["request_start_mono_ns"]) <= number(event.get("ts_mono_ns"))
            <= number(request["app_receive_mono_ns"])
        ]
        if len(local_hits) != 1:
            row["trace_reason"] = f"local_cache_provider_count_{len(local_hits)}"
            return row
        row.update({
            "provider": requester, "service_class": "requester_cache",
            "requester_domain_egress_ns": 0, "dci_roundtrip_residual_ns": 0,
            "serving_domain_discovery_ns": 0, "provider_processing_ns": 0,
            "serving_domain_return_ns": 0, "requester_domain_delivery_ns": end_to_end,
            "dci_request_frames": 0, "dci_request_bytes": 0,
            "dci_data_frames": 0, "dci_data_bytes": 0,
            "component_sum_ns": end_to_end, "closure_error_ns": 0,
            "preferred_logical_request_events": 0, "fallback_logical_request_events": 0,
            "cache_service_fraction_proxy": 1, "serving_overlay_hops_proxy": 0,
            "trace_complete": 1, "trace_reason": "ok_requester_cache",
        })
        return row
    requester_events = matching_events(
        traces, domain=requester_domain, key=request["key"], request_id=request_id,
        request_index=index, method=method,
    )
    if method == "tcp-direct":
        provider, provider_hit, send_start, serving_hops, reason = 0, None, None, 0, "remote_boundary"
    else:
        provider, provider_hit, send_start, serving_hops, reason = accepted_delivery(
            requester_events, requester, number(request["app_receive_mono_ns"]), requester_domain,
        )
    if reason not in ("ok", "remote_boundary"):
        row["trace_reason"] = reason
        return row
    if reason == "ok" and provider and node_domain(provider) == requester_domain:
        row.update({
            "provider": provider, "service_class": "requester_domain_cache",
            "requester_domain_egress_ns": 0, "dci_roundtrip_residual_ns": 0,
            "serving_domain_discovery_ns": 0, "provider_processing_ns": 0,
            "serving_domain_return_ns": 0, "requester_domain_delivery_ns": end_to_end,
            "dci_request_frames": 0, "dci_request_bytes": 0,
            "dci_data_frames": 0, "dci_data_bytes": 0,
            "component_sum_ns": end_to_end, "closure_error_ns": 0,
            "preferred_logical_request_events": 0, "fallback_logical_request_events": 0,
            "cache_service_fraction_proxy": 1,
            "serving_overlay_hops_proxy": serving_hops,
            "trace_complete": 1, "trace_reason": "ok_requester_domain_cache",
        })
        return row

    requester_role = "domain-a" if requester_domain == "A" else "domain-b"
    serving_role = "domain-a" if serving_domain == "A" else "domain-b"
    req_out = border_packets(borders, index, requester_role, "egress", "REQUEST")
    req_in = border_packets(borders, index, serving_role, "ingress", "REQUEST")
    value_bytes = number(request.get("expected_value_bytes"))
    data_out = border_packets(
        borders, index, serving_role, "egress", "DATA", value_bytes
    )
    data_in = border_packets(
        borders, index, requester_role, "ingress", "DATA", value_bytes
    )
    if not all((req_out, req_in, data_out, data_in)):
        row["trace_reason"] = "missing_border_payload_event"
        return row

    serving_events = matching_events(
        traces, domain=serving_domain, key=request["key"], request_id=request_id,
        request_index=index, method=method,
    )
    if method == "tcp-direct":
        hits = [event for event in serving_events if event.get("event") == "provider_hit"]
        sends = [event for event in serving_events if event.get("event") == "data_send_start"]
        if len(hits) != 1 or len(sends) != 1:
            row["trace_reason"] = "tcp_provider_not_unique"
            return row
        provider_hit, send_start = hits[0], sends[0]
        provider = number(provider_hit.get("node"))
        serving_hops = 1
    else:
        border = 33 if serving_domain == "B" else 1
        peer = 1 if serving_domain == "B" else 33
        crossing, reason = latest_unique(
            serving_events, number(data_out[0]["ts_mono_ns"]),
            lambda event: event.get("event") in ("data_send_start", "data_forward_start")
            and number(event.get("src")) == border
            and number(event.get("dest")) == peer,
        )
        if crossing is None:
            row["trace_reason"] = reason
            return row
        provider, provider_hit, send_start, serving_hops, reason = reverse_data_chain(
            serving_events, crossing, serving_domain,
        )
        if not provider or provider_hit is None or send_start is None:
            row["trace_reason"] = reason
            return row

    start = number(request["request_start_mono_ns"])
    end = number(request["app_receive_mono_ns"])
    provider_hit_ns = number(provider_hit.get("ts_mono_ns"))
    send_start_ns = number(send_start.get("ts_mono_ns"))
    requester_egress = number(req_out[0]["ts_mono_ns"]) - start
    serving_discovery = provider_hit_ns - number(req_in[0]["ts_mono_ns"])
    provider_processing = send_start_ns - provider_hit_ns
    serving_return = number(data_out[0]["ts_mono_ns"]) - send_start_ns
    requester_delivery = end - number(data_in[-1]["ts_mono_ns"])
    internal = sum((requester_egress, serving_discovery, provider_processing,
                    serving_return, requester_delivery))
    end_to_end = end - start
    residual = end_to_end - internal
    components = [requester_egress, residual, serving_discovery,
                  provider_processing, serving_return, requester_delivery]
    if any(value < -tolerance_ns for value in components):
        row["trace_reason"] = "negative_component"
        return row
    components = [max(0, value) for value in components]
    component_sum = sum(components)
    closure_error = end_to_end - component_sum
    if abs(closure_error) > tolerance_ns:
        row["trace_reason"] = "closure_error"
        return row
    request_direction = "egress" if requester_role != serving_role else ""
    # Payload direction is sufficient because the global closed loop marker
    # binds every observed packet to this single request.
    request_frames = req_out
    data_frames = data_out
    row.update({
        "provider": provider,
        "service_class": "original_owner" if provider == owner else "cache",
        "end_to_end_ns": end_to_end,
        "requester_domain_egress_ns": components[0],
        "dci_roundtrip_residual_ns": components[1],
        "serving_domain_discovery_ns": components[2],
        "provider_processing_ns": components[3],
        "serving_domain_return_ns": components[4],
        "requester_domain_delivery_ns": components[5],
        "dci_request_frames": len(request_frames),
        "dci_request_bytes": sum(number(event.get("ip_total_bytes")) for event in request_frames),
        "dci_data_frames": len(data_frames),
        "dci_data_bytes": sum(number(event.get("ip_total_bytes")) for event in data_frames),
        "component_sum_ns": component_sum,
        "closure_error_ns": closure_error,
        "preferred_logical_request_events": sum(
            event.get("event") in ("request_send_done", "request_forward_done")
            and str(event.get("fallback", "0")).lower() not in ("1", "true")
            and str(event.get("key", "")) in ("", request["key"])
            and ((method == "nc" and str(event.get("request_id", "")) == request_id)
                 or (method == "tcp-direct" and str(event.get("request_index", "")) == index))
            for event in traces
        ),
        "fallback_logical_request_events": sum(
            event.get("event") in ("fallback_forward", "origin_fallback_triggered")
            and str(event.get("key", "")) in ("", request["key"])
            and method == "nc" and str(event.get("request_id", "")) == request_id
            for event in traces
        ),
        "cache_service_fraction_proxy": int(provider != owner),
        "serving_overlay_hops_proxy": serving_hops,
        "trace_complete": 1,
        "trace_reason": "ok",
    })
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", required=True, type=Path)
    parser.add_argument("--domain-a-traces", required=True, type=Path)
    parser.add_argument("--domain-b-traces", required=True, type=Path)
    parser.add_argument("--domain-a-border", required=True, type=Path)
    parser.add_argument("--domain-b-border", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tolerance-ms", type=float, default=1.0)
    args = parser.parse_args()
    requests = read_csv(args.requests)
    traces = read_jsonl(sorted(args.domain_a_traces.glob("*.jsonl")), "A")
    traces += read_jsonl(sorted(args.domain_b_traces.glob("*.jsonl")), "B")
    borders = read_jsonl([args.domain_a_border], "A")
    borders += read_jsonl([args.domain_b_border], "B")
    method = str(requests[0].get("method", "nc")) if requests else "nc"
    trace_lookup = index_traces(traces, method)
    border_lookup = index_borders(borders)
    rows = [
        decompose(
            row,
            trace_lookup.get((
                row["key"],
                row.get("packet_request_id", "") if method == "nc"
                else row["request_index"],
            ), []),
            border_lookup.get(row["request_index"], []),
            int(args.tolerance_ms * 1_000_000),
        )
        for row in requests
    ]
    remote = [row for row in rows if row.get("request_class") == "remote"]
    if any(not row["trace_complete"] for row in remote):
        reasons: dict[str, int] = defaultdict(int)
        for row in remote:
            if not row["trace_complete"]:
                reasons[str(row["trace_reason"])] += 1
        raise RuntimeError(f"remote decomposition incomplete: {dict(reasons)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in COLUMNS} for row in rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
