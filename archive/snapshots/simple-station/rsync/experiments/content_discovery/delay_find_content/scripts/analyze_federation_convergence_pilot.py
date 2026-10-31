#!/usr/bin/env python3
"""Validate and summarize one long Federation convergence pilot.

This is deliberately descriptive: one seed can select an observation horizon,
but it cannot support a formal confidence interval or an equivalence claim.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import generate_federation_convergence_pilot as gen


EXPECTED_IMAGE = "sha256:bce2e56d82225efd51335a3af959ec0c0fc199782ab68281fd16f1ff16c13a74"
WINDOW = 4
LATENCY_RELATIVE_TOLERANCE = 0.05
LATENCY_ABSOLUTE_TOLERANCE_MS = 10.0
SLOPE_TOLERANCE_MS_PER_BATCH = 2.0
MECHANISM_TOLERANCES = {
    "preferred_logical_request_events": 0.10,
    "fallback_logical_request_events": 0.10,
    "cache_service_fraction_proxy": 0.03,
    "serving_overlay_hops_proxy": 0.10,
}


def read_csv(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def value(row: dict[str, str], field: str) -> float:
    try:
        return float(row[field])
    except (KeyError, TypeError, ValueError):
        return math.nan


def mean(values: list[float]) -> float:
    usable = [item for item in values if math.isfinite(item)]
    return statistics.fmean(usable) if usable else math.nan


def slope(points: list[tuple[int, float]]) -> float:
    center_x = mean([float(x) for x, _ in points])
    center_y = mean([y for _, y in points])
    denominator = sum((x - center_x) ** 2 for x, _ in points)
    return sum((x - center_x) * (y - center_y) for x, y in points) / denominator


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def validate_run(run: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    metadata = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    source = json.loads((run / "schedule_metadata.json").read_text(encoding="utf-8"))
    completion = json.loads((run / "completion_summary.json").read_text(encoding="utf-8"))
    schedule = read_csv(run / "schedule.tsv", "\t")
    requests = read_csv(run / "request_results.csv")
    trace = read_csv(run / "trace_breakdown.csv")
    isolation = read_csv(run / "isolation_results.csv")

    if metadata.get("status") != "ok" or metadata.get("method") != "nc":
        raise RuntimeError("pilot driver or method is invalid")
    required = {
        "wire_version": 3,
        "bloom_bits": 1024,
        "bloom_hash_count": 3,
        "staged_fallback": True,
        "fallback_delay_ms": 1500,
        "query_table_timeout_ms": 7000,
        "controller_timeout_ms": 8000,
        "max_hop_count": 12,
    }
    if any(metadata.get(key) != expected for key, expected in required.items()):
        raise RuntimeError("runtime protocol configuration changed")
    if any(source.get(key) != expected for key, expected in required.items()):
        raise RuntimeError("workload protocol configuration changed")
    if source.get("post_bins") != gen.POST_BINS or source.get("distribution") != "zipf":
        raise RuntimeError("pilot workload configuration changed")
    image_path = run / "runtime_image_id.before_launch"
    if image_path.read_text(encoding="utf-8").strip() != EXPECTED_IMAGE:
        raise RuntimeError("runtime image is not the frozen Federation-v4 image")
    if metadata.get("runtime_image_id") != EXPECTED_IMAGE:
        raise RuntimeError("metadata runtime image mismatch")

    expected = gen.EXPECTED_REQUESTS
    if not (len(schedule) == len(requests) == len(trace) == expected):
        raise RuntimeError(
            f"row count mismatch: schedule={len(schedule)} requests={len(requests)} "
            f"trace={len(trace)} expected={expected}"
        )
    if any(completion.get(field) != expected for field in ("scheduled", "issued", "completed")):
        raise RuntimeError("pilot did not complete every scheduled request")
    if len(isolation) != 2 or any(
        row.get("status") != "ok"
        or row.get("completion_present") != "0"
        or row.get("quiescence_ack_count") != "64"
        for row in isolation
    ):
        raise RuntimeError("pre-activation isolation probe failed")

    schedule_by_index = {row["request_index"]: row for row in schedule}
    intervals = []
    for row in requests:
        expected_row = schedule_by_index.get(row.get("request_index", ""))
        if expected_row is None or row.get("status") != "ok":
            raise RuntimeError("request status or identity mismatch")
        if (
            row.get("actual_value_bytes") != expected_row.get("value_size_bytes")
            or row.get("actual_fnv1a64") != expected_row.get("expected_fnv1a64")
        ):
            raise RuntimeError("DATA value integrity mismatch")
        if row.get("quiescence_ack_count") != "64":
            raise RuntimeError("request did not reach global quiescence")
        intervals.append(
            (int(row["request_start_mono_ns"]), int(row["app_receive_mono_ns"]))
        )
    intervals.sort()
    if any(right[0] < left[1] for left, right in zip(intervals, intervals[1:])):
        raise RuntimeError("closed-loop request intervals overlap")
    if any(row.get("application_delivery_events") != "1" for row in trace):
        raise RuntimeError("application delivery count is not exactly one")
    if sum(int(row.get("query_expired_unsatisfied_events") or 0) for row in trace):
        raise RuntimeError("a formal request expired unsatisfied")
    if sum(row.get("trace_complete") == "1" for row in trace) < math.ceil(expected * 0.99):
        raise RuntimeError("trace coverage is below 99%")
    for batch in range(1, gen.POST_BINS + 1):
        remote = [
            row for row in trace
            if row.get("phase") == "post"
            and row.get("request_class") == "remote"
            and int(row["bin"]) == batch
        ]
        local = [
            row for row in trace
            if row.get("phase") == "post"
            and row.get("request_class") == "local_control"
            and int(row["bin"]) == batch
        ]
        if sum(row.get("trace_complete") == "1" for row in remote) < 127:
            raise RuntimeError(f"remote trace coverage failed in batch {batch}")
        if sum(row.get("trace_complete") == "1" for row in local) < 31:
            raise RuntimeError(f"local-control trace coverage failed in batch {batch}")
    return requests, trace


def batch_rows(
    requests: list[dict[str, str]], trace: list[dict[str, str]]
) -> list[dict]:
    lookup = {row["request_index"]: row for row in trace}
    groups: dict[tuple[int, str], list[tuple[dict[str, str], dict[str, str]]]] = defaultdict(list)
    for request in requests:
        if request["phase"] != "post":
            continue
        role = "remote" if request["request_class"] == "remote" else "local_control"
        groups[(int(request["bin"]), role)].append((request, lookup[request["request_index"]]))

    output = []
    for (batch, role), members in sorted(groups.items()):
        traces = [trace_row for _, trace_row in members]
        output.append(
            {
                "batch": batch,
                "request_class": role,
                "requests": len(members),
                "end_to_end_latency_ms": mean(
                    [value(request, "end_to_end_latency_ns") / 1e6 for request, _ in members]
                ),
                "discovery_latency_ms": mean(
                    [value(trace_row, "discovery_latency_ns") / 1e6 for trace_row in traces]
                ),
                "return_path_latency_ms": mean(
                    [value(trace_row, "return_path_latency_ns") / 1e6 for trace_row in traces]
                ),
                "preferred_logical_request_events": mean(
                    [value(trace_row, "normal_request_frames") for trace_row in traces]
                ),
                "fallback_logical_request_events": mean(
                    [value(trace_row, "fallback_request_frames") for trace_row in traces]
                ),
                "cache_service_fraction_proxy": mean(
                    [1.0 if trace_row.get("service_class") == "cache" else 0.0 for trace_row in traces]
                ),
                "serving_overlay_hops_proxy": mean(
                    [value(trace_row, "serving_overlay_hops") for trace_row in traces]
                ),
            }
        )
    return output


def window_rows(batches: list[dict]) -> list[dict]:
    remote = {row["batch"]: row for row in batches if row["request_class"] == "remote"}
    local = {row["batch"]: row for row in batches if row["request_class"] == "local_control"}
    metrics = [
        "end_to_end_latency_ms",
        "discovery_latency_ms",
        "return_path_latency_ms",
        *MECHANISM_TOLERANCES,
    ]
    output = []
    for end in range(WINDOW, gen.POST_BINS + 1, WINDOW):
        indexes = list(range(end - WINDOW + 1, end + 1))
        item = {
            "window_start": indexes[0],
            "window_end": end,
            "remote_requests": sum(remote[index]["requests"] for index in indexes),
            "local_control_requests": sum(local[index]["requests"] for index in indexes),
        }
        for metric in metrics:
            item[f"remote_{metric}"] = mean([remote[index][metric] for index in indexes])
        item["local_end_to_end_latency_ms"] = mean(
            [local[index]["end_to_end_latency_ms"] for index in indexes]
        )
        item["remote_minus_local_ms"] = (
            item["remote_end_to_end_latency_ms"] - item["local_end_to_end_latency_ms"]
        )
        output.append(item)
    return output


def convergence(windows: list[dict], batches: list[dict]) -> dict:
    checks = []
    for previous, current in zip(windows, windows[1:]):
        old = previous["remote_end_to_end_latency_ms"]
        new = current["remote_end_to_end_latency_ms"]
        latency_abs = abs(new - old)
        latency_rel = latency_abs / old if old else math.inf
        last_eight = [
            row for row in batches
            if row["request_class"] == "remote"
            and current["window_end"] - 7 <= row["batch"] <= current["window_end"]
        ]
        latency_slope = slope(
            [(int(row["batch"]), float(row["end_to_end_latency_ms"])) for row in last_eight]
        )
        mechanism_deltas = {
            metric: abs(current[f"remote_{metric}"] - previous[f"remote_{metric}"])
            for metric in MECHANISM_TOLERANCES
        }
        passed = (
            latency_abs <= LATENCY_ABSOLUTE_TOLERANCE_MS
            and latency_rel <= LATENCY_RELATIVE_TOLERANCE
            and abs(latency_slope) <= SLOPE_TOLERANCE_MS_PER_BATCH
            and all(
                mechanism_deltas[metric] <= tolerance
                for metric, tolerance in MECHANISM_TOLERANCES.items()
            )
        )
        checks.append(
            {
                "at_post_batch": current["window_end"],
                "previous_window": [previous["window_start"], previous["window_end"]],
                "current_window": [current["window_start"], current["window_end"]],
                "remote_latency_absolute_change_ms": latency_abs,
                "remote_latency_relative_change": latency_rel,
                "remote_latency_slope_ms_per_batch_last8": latency_slope,
                "mechanism_absolute_changes": mechanism_deltas,
                "passed": passed,
            }
        )
    recommended = None
    for left, right in zip(checks, checks[1:]):
        if left["passed"] and right["passed"]:
            recommended = right["at_post_batch"]
            break
    return {
        "evidence_level": "ONE_SEED_DESCRIPTIVE_PILOT",
        "formal_inference_allowed": False,
        "post_batches_observed": gen.POST_BINS,
        "window_width_batches": WINDOW,
        "requires_two_consecutive_passing_boundaries": True,
        "thresholds": {
            "latency_relative_change_max": LATENCY_RELATIVE_TOLERANCE,
            "latency_absolute_change_ms_max": LATENCY_ABSOLUTE_TOLERANCE_MS,
            "latency_slope_ms_per_batch_max": SLOPE_TOLERANCE_MS_PER_BATCH,
            "mechanism_absolute_change_max": MECHANISM_TOLERANCES,
        },
        "checks": checks,
        "earliest_recommended_post_batches": recommended,
        "decision": (
            "HORIZON_CANDIDATE_FOUND" if recommended is not None else "NOT_CONVERGED_BY_BATCH_64"
        ),
        "next_step": (
            "confirm this horizon with additional seeds before freezing a formal matrix"
            if recommended is not None
            else "inspect mechanism drift before changing protocol parameters or extending further"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir or args.run_dir / "convergence_analysis"
    requests, trace = validate_run(args.run_dir)
    batches = batch_rows(requests, trace)
    windows = window_rows(batches)
    report = convergence(windows, batches)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "batch_means.csv", batches)
    write_csv(output / "window_means.csv", windows)
    (output / "convergence_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"{report['decision']} "
        f"recommended_post_batches={report['earliest_recommended_post_batches']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
