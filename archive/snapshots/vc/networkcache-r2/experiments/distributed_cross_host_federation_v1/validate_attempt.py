#!/usr/bin/env python3
"""Strictly validate a collected pair without changing either raw attempt."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import decompose_latency as decomposition
import spec


POLICY = "cross-host-federation-v1-validator-v5-decomposition-coverage"
OBSERVER_POLICY = "cross-host-border-observer-v3-tcp-sequence"


def dci_shaping_ok(snapshot: dict[str, Any]) -> bool:
    """Validate the structured `tc -j qdisc` snapshot in SI units.

    iproute2 serializes delay as seconds and rate as bytes/second on the
    deployed workers.  Checking human-readable unit tokens rejects a correct
    snapshot such as delay=0.002 and rate=125000000.
    """
    try:
        qdiscs = json.loads(snapshot["tc"]["stdout"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(qdiscs, list):
        return False
    for qdisc in qdiscs:
        if qdisc.get("kind") != "netem" or qdisc.get("root") is not True:
            continue
        options = qdisc.get("options") or {}
        delay = (options.get("delay") or {}).get("delay")
        rate = (options.get("rate") or {}).get("rate")
        limit = options.get("limit")
        try:
            return (
                math.isclose(float(delay), 0.002, rel_tol=0.0, abs_tol=1e-9)
                and math.isclose(float(rate), 125_000_000.0, rel_tol=0.0, abs_tol=0.5)
                and int(limit) == 1000
            )
        except (TypeError, ValueError):
            return False
    return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_events(directory: Path, domain: str) -> list[dict[str, Any]]:
    return decomposition.read_jsonl(sorted(directory.glob("*.jsonl")), domain)


def validate(domain_a: Path, domain_b: Path, output: Path) -> dict[str, Any]:
    inputs: list[Path] = []
    for domain, role in ((domain_a, "domain-a"), (domain_b, "domain-b")):
        result = load_json(domain / "domain_result.json")
        if result.get("status") != "CAPTURED" or result.get("role") != role:
            raise ValueError(f"{role} was not captured successfully")
        inputs.append(domain / "domain_result.json")
    result_a, result_b = load_json(domain_a / "domain_result.json"), load_json(domain_b / "domain_result.json")
    for field in ("scenario", "method", "schedule_sha256"):
        if result_a.get(field) != result_b.get(field):
            raise ValueError(f"paired {field} mismatch")
    campaign_a = load_json(domain_a / "campaign_snapshot.json")
    campaign_b = load_json(domain_b / "campaign_snapshot.json")
    if campaign_a != campaign_b:
        raise ValueError("paired campaign snapshot mismatch")
    formal = campaign_a.get("evidence_status") != "HORIZON_PENDING"
    for domain, role in ((domain_a, "domain-a"), (domain_b, "domain-b")):
        snapshot = load_json(domain / "host_snapshot.json")
        link = json.loads(snapshot["link"]["stdout"])
        if len(link) != 1 or int(link[0].get("mtu", 0)) != 1420:
            raise ValueError(f"{role} WireGuard MTU mismatch")
        if not snapshot["wireguard"]["stdout"].strip():
            raise ValueError(f"{role} WireGuard snapshot missing")
        if not dci_shaping_ok(snapshot):
            raise ValueError(f"{role} DCI shaping snapshot mismatch")
        images = json.loads(snapshot["image"]["stdout"])
        image_id = images[0]["Id"] if images else ""
        expected_image = campaign_a.get("runtime_image_id")
        if formal and image_id != expected_image:
            raise ValueError(f"{role} runtime image mismatch")
        provenance = load_json(domain / "provenance.json")
        if provenance["campaign_sha256"] != sha256(domain / "campaign_snapshot.json"):
            raise ValueError(f"{role} campaign provenance mismatch")
        if formal:
            assignment = load_json(domain / "assignment_snapshot.json")
            if assignment.get("role") != role:
                raise ValueError(f"{role} assignment role mismatch")
            if assignment.get("campaign_fingerprint") != campaign_a.get("campaign_fingerprint"):
                raise ValueError(f"{role} assignment campaign mismatch")
        lab = (domain / "lab.conf").read_text(encoding="utf-8")
        if "link1_33" in lab or lab.count("[entrypoint]") != 45:
            raise ValueError(f"{role} physical topology is not the frozen 45-container shard")
    metadata = load_json(domain_a / "schedule_metadata.json")
    expected = spec.requests_per_run(int(metadata["post_batches"]))
    requests = decomposition.read_csv(domain_a / "request_results.csv")
    if len(requests) != expected:
        raise ValueError(f"request count {len(requests)} != {expected}")
    if any(row.get("status") != "ok" for row in requests):
        raise ValueError("not every request completed successfully")
    if any(
        row.get("actual_value_bytes") != row.get("expected_value_bytes")
        or row.get("actual_fnv1a64") != row.get("expected_fnv1a64")
        or row.get("quiescence_ack_count") != "64"
        for row in requests
    ):
        raise ValueError("DATA integrity or global quiescence gate failed")
    if len({row["request_index"] for row in requests}) != expected:
        raise ValueError("request indices are not unique")
    for domain in ("A", "B"):
        intervals = sorted(
            (int(row["request_start_mono_ns"]), int(row["app_receive_mono_ns"]))
            for row in requests
            if decomposition.node_domain(int(row["requester"])) == domain
        )
        if any(right[0] < left[1] for left, right in zip(intervals, intervals[1:])):
            raise ValueError(f"{domain} request intervals overlap")
    if {row.get("schedule_sha256") for row in requests} != {result_a["schedule_sha256"]}:
        raise ValueError("request schedule fingerprint mismatch")
    isolation = decomposition.read_csv(domain_a / "isolation_results.csv")
    if len(isolation) != 2 or any(
        row["status"] != "ok" or row["quiescence_ack_count"] != "64"
        or (result_a["method"] == "nc" and row["completion_present"] != "0")
        or (result_a["method"] == "tcp-direct" and row["completion_present"] != "1")
        for row in isolation
    ):
        raise ValueError("pre-activation isolation gate failed")
    activation = load_json(domain_a / "activation.json")
    if activation.get("stable_window_ms") != 500 or len(activation.get("route_proofs", [])) != 2:
        raise ValueError("activation barrier/route proof missing")

    traces = load_events(domain_a / "traces", "A") + load_events(domain_b / "traces", "B")
    method = str(result_a["method"])
    deliveries = Counter()
    local_deliveries = defaultdict(list)
    for event in traces:
        if event.get("event") != "app_receive":
            continue
        identifier = (
            str(event.get("request_id", "")) if method == "nc"
            else str(event.get("request_index", ""))
        )
        deliveries[(identifier, str(event.get("key", "")), int(event.get("node", 0)))] += 1
        local_deliveries[(str(event.get("key", "")), int(event.get("node", 0)))].append(event)
    for request in requests:
        identifier = request["packet_request_id"] if method == "nc" else request["request_index"]
        key = (identifier, request["key"], int(request["requester"]))
        count = deliveries[key]
        if method == "nc" and identifier == "0":
            count = sum(
                event.get("event") == "app_receive"
                and event.get("key") == request["key"]
                and int(event.get("node", 0)) == int(request["requester"])
                and int(request["request_start_mono_ns"]) <= int(event.get("ts_mono_ns", 0))
                <= int(request["app_receive_mono_ns"])
                for event in local_deliveries[(request["key"], int(request["requester"]))]
            )
        if count != 1:
            raise ValueError(f"application delivery count for request {request['request_index']} is {count}")

    borders = decomposition.read_jsonl([domain_a / "border_packets.jsonl"], "A")
    borders += decomposition.read_jsonl([domain_b / "border_packets.jsonl"], "B")
    expected_observer = campaign_a.get("observer_policy", "cross-host-border-observer-v2-dual-boundary")
    if not borders or {event.get("observer_policy") for event in borders} != {expected_observer}:
        raise ValueError("border observer policy mismatch")
    expected_interfaces = {
        ("domain-a", "egress"): "br-xfed-v1-a",
        ("domain-a", "ingress"): "wg-xfed-v1",
        ("domain-b", "egress"): "br-xfed-v1-b",
        ("domain-b", "ingress"): "wg-xfed-v1",
    }
    if any(
        event.get("capture_interface") != expected_interfaces.get(
            (str(event.get("role")), str(event.get("direction")))
        )
        or event.get("logical_packet_type") not in ("REQUEST", "DATA")
        for event in borders
        if event.get("request_class") == "remote"
        and int(event.get("transport_payload_bytes", 0) or 0) > 0
    ):
        raise ValueError("border capture boundary or logical type mismatch")
    trace_lookup = decomposition.index_traces(traces, method)
    border_lookup = decomposition.index_borders(borders)
    rows = []
    for request in requests:
        identifier = (
            request["packet_request_id"] if method == "nc"
            else request["request_index"]
        )
        rows.append(decomposition.decompose(
            {**request, "method": method},
            trace_lookup.get((request["key"], identifier), []),
            border_lookup.get(request["request_index"], []),
            1_000_000,
        ))
    remote = [row for row in rows if row.get("request_class") == "remote"]
    incomplete = Counter(str(row["trace_reason"]) for row in remote if not row["trace_complete"])
    # End-to-end samples remain valid even when packet observation cannot
    # establish a decomposition. Keep those rows with blank components and
    # explicit reasons; never zero-fill or drop their end-to-end latency.
    output.mkdir(parents=True, exist_ok=False)
    with (output / "request_decomposition.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=decomposition.COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in decomposition.COLUMNS} for row in rows)
    inputs.extend([
        domain_a / "request_results.csv", domain_a / "isolation_results.csv",
        domain_a / "activation.json", domain_a / "border_packets.jsonl",
        domain_b / "border_packets.jsonl",
        domain_a / "host_snapshot.json", domain_b / "host_snapshot.json",
        domain_a / "provenance.json", domain_b / "provenance.json",
        domain_a / "schedule_metadata.json", domain_b / "schedule_metadata.json",
        *sorted((domain_a / "traces").glob("*.jsonl")),
        *sorted((domain_b / "traces").glob("*.jsonl")),
    ])
    record = {
        "status": "QUALIFIED", "validator_policy": POLICY,
        "validator_policy_sha256": hashlib.sha256(POLICY.encode()).hexdigest(),
        "scenario": result_a["scenario"], "method": method,
        "requests": len(requests),
        "remote_requests": len(remote),
        "remote_decomposed": len(remote) - sum(incomplete.values()),
        "remote_decomposition_missing": dict(incomplete),
        "remote_decomposition_coverage": (len(remote) - sum(incomplete.values())) / len(remote),
        "inputs": {str(path): sha256(path) for path in inputs},
    }
    (output / "validation.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain-a", required=True, type=Path)
    parser.add_argument("--domain-b", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.domain_a, args.domain_b, args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
