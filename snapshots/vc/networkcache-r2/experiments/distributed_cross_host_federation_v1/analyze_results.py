#!/usr/bin/env python3
"""Convergence and formal aggregation for Cross-host Federation-v1."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import spec


WINDOW = 4
T_CRITICAL_95_DF9 = 2.262157


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else math.nan


def slope(points: list[tuple[int, float]]) -> float:
    xbar, ybar = mean([float(x) for x, _ in points]), mean([y for _, y in points])
    denominator = sum((x - xbar) ** 2 for x, _ in points)
    return sum((x - xbar) * (y - ybar) for x, y in points) / denominator


def batch_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row["phase"] == "post":
            groups[(int(row["bin"]), row["request_class"])].append(row)
    metrics = (
        "end_to_end_ns", "requester_domain_egress_ns", "dci_roundtrip_residual_ns",
        "serving_domain_discovery_ns", "provider_processing_ns",
        "serving_domain_return_ns", "requester_domain_delivery_ns",
        "preferred_logical_request_events", "fallback_logical_request_events",
        "cache_service_fraction_proxy", "serving_overlay_hops_proxy",
        "dci_request_frames", "dci_request_bytes", "dci_data_frames", "dci_data_bytes",
    )
    output = []
    for (batch, request_class), members in sorted(groups.items()):
        item: dict[str, Any] = {
            "batch": batch, "request_class": request_class, "requests": len(members),
            "decomposition_complete": sum(str(row.get("trace_complete")) == "1" for row in members),
        }
        for metric in metrics:
            values = [float(row[metric]) for row in members if row.get(metric, "") != ""]
            scale = 1e6 if metric.endswith("_ns") else 1.0
            item[metric.removesuffix("_ns") + ("_ms" if metric.endswith("_ns") else "")] = (
                mean(values) / scale
            )
        output.append(item)
    return output


def convergence(seed: int, batches: list[dict[str, Any]], observed: int) -> dict[str, Any]:
    remote = {row["batch"]: row for row in batches if row["request_class"] == "remote"}
    local = {row["batch"]: row for row in batches if row["request_class"] == "local_control"}
    if set(remote) != set(range(1, observed + 1)) or set(local) != set(range(1, observed + 1)):
        raise ValueError("calibration batch matrix is incomplete")
    windows = []
    for end in range(WINDOW, observed + 1, WINDOW):
        indexes = range(end - WINDOW + 1, end + 1)
        windows.append({
            "window_start": end - WINDOW + 1, "window_end": end,
            "remote_latency_ms": mean([remote[index]["end_to_end_ms"] for index in indexes]),
            "local_latency_ms": mean([local[index]["end_to_end_ms"] for index in indexes]),
            "preferred": mean([remote[index]["preferred_logical_request_events"] for index in indexes]),
            "fallback": mean([remote[index]["fallback_logical_request_events"] for index in indexes]),
            "cache_fraction": mean([remote[index]["cache_service_fraction_proxy"] for index in indexes]),
            "serving_hops": mean([remote[index]["serving_overlay_hops_proxy"] for index in indexes]),
        })
    checks = []
    for previous, current in zip(windows, windows[1:]):
        absolute = abs(current["remote_latency_ms"] - previous["remote_latency_ms"])
        relative = absolute / previous["remote_latency_ms"]
        last = [remote[index]["end_to_end_ms"] for index in range(current["window_end"] - 7,
                                                                    current["window_end"] + 1)]
        drift = {
            name: abs(current[name] - previous[name])
            for name in ("preferred", "fallback", "cache_fraction", "serving_hops")
        }
        passed = (
            absolute <= 10 and relative <= 0.05
            and abs(slope(list(zip(range(current["window_end"] - 7,
                                         current["window_end"] + 1), last)))) <= 2
            and drift["preferred"] <= .10 and drift["fallback"] <= .10
            and drift["cache_fraction"] <= .03 and drift["serving_hops"] <= .10
        )
        checks.append({"at_post_batch": current["window_end"], "passed": passed,
                       "latency_absolute_change_ms": absolute,
                       "latency_relative_change": relative, "mechanism_changes": drift})
    candidate = next((right["at_post_batch"] for left, right in zip(checks, checks[1:])
                      if left["passed"] and right["passed"]), None)
    return {
        "seed": seed, "post_batches_observed": observed,
        "earliest_recommended_post_batches": candidate,
        "decision": "HORIZON_CANDIDATE_FOUND" if candidate else f"NOT_CONVERGED_BY_BATCH_{observed}",
        "formal_inference_allowed": False, "checks": checks,
    }


def ci(values: list[float]) -> dict[str, float | int]:
    if len(values) != 10:
        raise ValueError("formal CI requires exactly 10 seed means")
    center = mean(values)
    half = T_CRITICAL_95_DF9 * statistics.stdev(values) / math.sqrt(10)
    return {"n_seed_means": 10, "mean": center, "ci95_low": center - half,
            "ci95_high": center + half}


def formal(selection: list[dict[str, str]], output: Path) -> dict[str, Any]:
    if len(selection) != 40:
        raise ValueError("formal selection must contain exactly 40 runs")
    slots = {(row["method"], int(row["seed"]), int(row["repetition"])) for row in selection}
    expected = {(method, seed, repetition) for method in spec.METHODS
                for seed in spec.SEEDS for repetition in spec.REPETITIONS}
    if slots != expected:
        raise ValueError("formal selection matrix is incomplete or duplicated")
    per_run: dict[tuple[str, int, int], dict[str, float]] = {}
    per_run_period: dict[tuple[str, int, int, str], dict[str, float]] = {}
    run_timeline: dict[tuple[str, int, int, int], float] = {}
    for selected in selection:
        rows = read_csv(Path(selected["decomposition_csv"]))
        remote = [row for row in rows if row["phase"] == "post" and row["request_class"] == "remote"]
        local = [row for row in rows if row["phase"] == "post" and row["request_class"] == "local_control"]
        late_start = max(int(row["bin"]) for row in remote) - 7
        late = [row for row in remote if int(row["bin"]) >= late_start]
        key = (selected["method"], int(selected["seed"]), int(selected["repetition"]))
        remote_ms = mean([float(row["end_to_end_ns"]) / 1e6 for row in late])
        local_ms = mean([float(row["end_to_end_ns"]) / 1e6 for row in local if int(row["bin"]) >= late_start])
        per_run[key] = {"remote_ms": remote_ms, "local_ms": local_ms,
                        "difference_ms": remote_ms - local_ms,
                        "ratio": remote_ms / local_ms}
        for period, members in (("early", [row for row in remote if int(row["bin"]) <= 8]),
                                ("late", late)):
            period_metrics = (
                "end_to_end_ns", "requester_domain_egress_ns",
                "dci_roundtrip_residual_ns", "serving_domain_discovery_ns",
                "provider_processing_ns", "serving_domain_return_ns",
                "requester_domain_delivery_ns", "dci_request_frames",
                "dci_request_bytes", "dci_data_frames", "dci_data_bytes",
                "fallback_logical_request_events", "cache_service_fraction_proxy",
                "serving_overlay_hops_proxy",
            )
            values = {"decomposition_coverage": mean([
                float(str(row.get("trace_complete")) == "1") for row in members
            ])}
            for metric in period_metrics:
                scale = 1e6 if metric.endswith("_ns") else 1.0
                values[metric.removesuffix("_ns") + ("_ms" if metric.endswith("_ns") else "")] = (
                    mean([float(row[metric]) for row in members if row.get(metric, "") != ""]) / scale
                )
            per_run_period[(*key, period)] = values
        for batch in sorted({int(row["bin"]) for row in remote}):
            run_timeline[(selected["method"], int(selected["seed"]),
                          int(selected["repetition"]), batch)] = mean([
                float(row["end_to_end_ns"]) / 1e6 for row in remote if int(row["bin"]) == batch
            ])
    seed_values: dict[tuple[str, int], dict[str, float]] = {}
    for method in spec.METHODS:
        for seed in spec.SEEDS:
            seed_values[(method, seed)] = {
                metric: mean([per_run[(method, seed, repetition)][metric]
                              for repetition in spec.REPETITIONS])
                for metric in ("remote_ms", "local_ms", "difference_ms", "ratio")
            }
    effects = {}
    for method in spec.METHODS:
        effects[method] = {
            metric: ci([seed_values[(method, seed)][metric] for seed in spec.SEEDS])
            for metric in ("remote_ms", "local_ms", "difference_ms", "ratio")
        }
    effects["nc_minus_tcp_direct_ms"] = ci([
        seed_values[("nc", seed)]["remote_ms"] - seed_values[("tcp-direct", seed)]["remote_ms"]
        for seed in spec.SEEDS
    ])
    period_summaries: dict[str, dict[str, dict[str, dict[str, float | int]]]] = {}
    for method in spec.METHODS:
        period_summaries[method] = {}
        for period in ("early", "late"):
            metric_names = per_run_period[(method, 1, 1, period)].keys()
            period_summaries[method][period] = {}
            for metric in metric_names:
                seed_means = [mean([
                    per_run_period[(method, seed, repetition, period)][metric]
                    for repetition in spec.REPETITIONS
                ]) for seed in spec.SEEDS]
                period_summaries[method][period][metric] = (
                    ci(seed_means) if all(math.isfinite(value) for value in seed_means)
                    else {"status": "INSUFFICIENT_DECOMPOSITION_COVERAGE",
                          "n_seed_means": sum(math.isfinite(value) for value in seed_means)}
                )
    batches = sorted({key[3] for key in run_timeline})
    timeline = []
    for method in spec.METHODS:
        for batch in batches:
            seed_means = [mean([
                run_timeline[(method, seed, repetition, batch)]
                for repetition in spec.REPETITIONS
            ]) for seed in spec.SEEDS]
            timeline.append({"method": method, "batch": batch, **ci(seed_means)})
    result = {"status": "READY", "selected_runs": 40, "effects": effects,
              "period_summaries": period_summaries,
              "batch_timeline": timeline,
              "claim_boundary": "paired differences and ratios; no equivalence claim",
              "decomposition_boundary": "conditional on available decomposition; end-to-end includes every request; coverage reported separately"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    calibration = sub.add_parser("calibration")
    calibration.add_argument("--decomposition", required=True, type=Path)
    calibration.add_argument("--seed", required=True, type=int)
    calibration.add_argument("--post-batches", required=True, type=int)
    calibration.add_argument("--output-dir", required=True, type=Path)
    formal_parser = sub.add_parser("formal")
    formal_parser.add_argument("--selection", required=True, type=Path)
    formal_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "calibration":
        batches = batch_rows(read_csv(args.decomposition))
        report = convergence(args.seed, batches, args.post_batches)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_csv(args.output_dir / "batch_means.csv", batches)
        (args.output_dir / "convergence_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        selection = read_csv(args.selection)
        print(json.dumps(formal(selection, args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
