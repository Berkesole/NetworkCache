#!/usr/bin/env python3
"""Aggregate request-complete latency-v2 data through run and seed units."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import curate_latency_v2


METRICS = ("miss_latency_ns", "discovery_delay_ns", "return_delay_ns", "provider_hit_hop")
T_CRITICAL_95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776}
SENSITIVITY_LIMIT = 0.05


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not columns:
            handle.write("\n")
            return
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def mean(values: Iterable[float]) -> float:
    data = list(values)
    return statistics.fmean(data) if data else math.nan


def fmt(value: float) -> str:
    return "" if math.isnan(value) else f"{value:.6f}".rstrip("0").rstrip(".")


def request_rows(selection_rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    output: List[Dict[str, str]] = []
    for selected in selection_rows:
        trace_path = Path(selected["trace_path"])
        schedule = json.loads(trace_path.with_name("schedule_metadata.json").read_text(encoding="utf-8"))
        positions = {
            str(row["request_index"]): int(row["measurement_position"])
            for row in schedule["requests"]
            if row.get("phase") == "measurement"
        }
        width = int(selected["bin_width"])
        analysis_run_id = f"{selected['attempt_batch']}/{selected['run_id']}"
        seen: set[str] = set()
        for row in read_csv(trace_path):
            request_index = row.get("request_index", "")
            if (
                request_index in seen
                or request_index not in positions
                or not curate_latency_v2.usable_trace(row)
            ):
                continue
            seen.add(request_index)
            output.append(
                {
                    **row,
                    "config": selected["config"],
                    "analysis_run_id": analysis_run_id,
                    "seed": selected["seed"],
                    "request_bin": str((positions[request_index] - 1) // width + 1),
                    "bin_width": str(width),
                }
            )
    return output


def aggregate_run_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, str]]] = defaultdict(list)
    fields = ("config", "analysis_run_id", "seed", "request_bin", "bin_width")
    for row in rows:
        groups[tuple(row[field] for field in fields)].append(row)
    output: List[Dict[str, object]] = []
    for key, group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1], int(item[0][3]))):
        result: Dict[str, object] = {field: key[index] for index, field in enumerate(fields)}
        result["run_id"] = result.pop("analysis_run_id")
        result["usable_rows"] = len(group)
        result["scheduled_rows"] = int(result["bin_width"])
        for metric in METRICS:
            result[f"{metric}_mean"] = fmt(mean(float(row[metric]) for row in group if row.get(metric)))
        output.append(result)
    return output


def aggregate_seed_rows(run_rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, object]]] = defaultdict(list)
    fields = ("config", "seed", "request_bin", "bin_width")
    for row in run_rows:
        groups[tuple(str(row[field]) for field in fields)].append(row)
    output: List[Dict[str, object]] = []
    for key, group in sorted(groups.items(), key=lambda item: (item[0][0], int(item[0][1]), int(item[0][2]))):
        result: Dict[str, object] = {field: key[index] for index, field in enumerate(fields)}
        result["run_count"] = len(group)
        result["usable_rows"] = sum(int(row["usable_rows"]) for row in group)
        result["scheduled_rows"] = sum(int(row["scheduled_rows"]) for row in group)
        for metric in METRICS:
            result[f"{metric}_mean"] = fmt(
                mean(float(row[f"{metric}_mean"]) for row in group if row.get(f"{metric}_mean"))
            )
        output.append(result)
    return output


def aggregate_overall_rows(seed_rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, str, str], List[Dict[str, object]]] = defaultdict(list)
    for row in seed_rows:
        groups[(str(row["config"]), str(row["request_bin"]), str(row["bin_width"]))].append(row)
    output: List[Dict[str, object]] = []
    for (config, request_bin, width), group in sorted(
        groups.items(), key=lambda item: (item[0][0], int(item[0][1]))
    ):
        result: Dict[str, object] = {
            "config": config,
            "request_bin": request_bin,
            "bin_width": width,
            "seed_count": len(group),
            "usable_rows": sum(int(row["usable_rows"]) for row in group),
            "scheduled_rows": sum(int(row["scheduled_rows"]) for row in group),
        }
        for metric in METRICS:
            values = [float(row[f"{metric}_mean"]) for row in group if row.get(f"{metric}_mean")]
            center = mean(values)
            half = math.nan
            if len(values) >= 2:
                half = T_CRITICAL_95[len(values)] * statistics.stdev(values) / math.sqrt(len(values))
            result[f"{metric}_mean"] = fmt(center)
            result[f"{metric}_ci95_low"] = fmt(center - half) if not math.isnan(half) else ""
            result[f"{metric}_ci95_high"] = fmt(center + half) if not math.isnan(half) else ""
        output.append(result)
    return output


def summary_rows(overall_rows: Sequence[Dict[str, object]], spacing_by_config: Dict[str, int]) -> List[Dict[str, object]]:
    groups: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in overall_rows:
        groups[str(row["config"])].append(row)
    output: List[Dict[str, object]] = []
    for config, rows in sorted(groups.items()):
        rows.sort(key=lambda row: int(row["request_bin"]))
        width = int(rows[0]["bin_width"])
        latencies = [float(row["miss_latency_ns_mean"]) for row in rows]
        early = mean(latencies[:3])
        late = mean(latencies[-3:])
        threshold = late * 1.10
        convergence_bin = 0
        if config.startswith("controlled-"):
            for index in range(0, max(0, len(latencies) - 2)):
                if not all(value <= threshold for value in latencies[index:index + 3]):
                    continue
                later = latencies[index + 3:]
                if any(
                    later[offset] > threshold and later[offset + 1] > threshold
                    for offset in range(len(later) - 1)
                ):
                    continue
                convergence_bin = int(rows[index]["request_bin"])
                break
        request_index = (convergence_bin - 1) * width + 1 if convergence_bin else 0
        output.append(
            {
                "config": config,
                "bin_count": len(rows),
                "bin_width": width,
                "early_window_requests": width * 3,
                "late_window_requests": width * 3,
                "early_three_bin_mean_ns": fmt(early),
                "late_three_bin_mean_ns": fmt(late),
                "relative_reduction": fmt((early - late) / early if early else math.nan),
                "steady_threshold_ns": fmt(threshold),
                "converged": "1" if convergence_bin else "0",
                "convergence_bin": convergence_bin or "",
                "requests_to_converge": request_index or "",
                "time_to_converge_ms": (request_index - 1) * spacing_by_config[config] if request_index else "",
            }
        )
    return output


def aggregate(selection_rows: Sequence[Dict[str, str]]) -> Tuple[List[Dict[str, object]], ...]:
    raw = request_rows(selection_rows)
    run = aggregate_run_rows(raw)
    seed = aggregate_seed_rows(run)
    overall = aggregate_overall_rows(seed)
    spacing = {
        row["config"]: int(row["config"].split("-s", 1)[1].split("-", 1)[0])
        for row in selection_rows
    }
    summary = summary_rows(overall, spacing)
    return raw, run, seed, overall, summary


def direction(value: float) -> str:
    return "higher" if value > 0 else "lower" if value < 0 else "equal"


def sensitivity_rows(
    primary_summary: Sequence[Dict[str, object]],
    all_summary: Sequence[Dict[str, object]],
) -> List[Dict[str, object]]:
    primary = {str(row["config"]): row for row in primary_summary}
    alternate = {str(row["config"]): row for row in all_summary}
    output: List[Dict[str, object]] = []
    segments = (("early", "early_three_bin_mean_ns"), ("late", "late_three_bin_mean_ns"))
    for config in sorted(primary):
        for segment, column in segments:
            first = float(primary[config][column])
            second = float(alternate[config][column])
            relative = abs(first - second) / abs(second) if second else math.inf
            output.append(
                {
                    "check_type": "estimate",
                    "name": config,
                    "segment": segment,
                    "primary_value_ns": fmt(first),
                    "all_eligible_value_ns": fmt(second),
                    "relative_difference": fmt(relative),
                    "direction_primary": "",
                    "direction_all_eligible": "",
                    "pass": "1" if relative <= SENSITIVITY_LIMIT else "0",
                }
            )
    comparisons = []
    for topology in (16, 32):
        comparisons.extend(
            [
                (f"zipf_minus_uniform_{topology}", f"iid-zipf-s2000-{topology}-nc", f"iid-uniform-s2000-{topology}-nc"),
                (f"nc_minus_known_owner_{topology}", f"iid-zipf-s2000-{topology}-nc", f"iid-zipf-s2000-{topology}-known-owner"),
            ]
        )
    for method in ("nc", "known-owner"):
        comparisons.append(
            (f"s250_minus_s2000_{method}", f"iid-zipf-s250-32-{method}", f"iid-zipf-s2000-32-{method}")
        )
    for name, left, right in comparisons:
        for segment, column in segments:
            first = float(primary[left][column]) - float(primary[right][column])
            second = float(alternate[left][column]) - float(alternate[right][column])
            first_direction = direction(first)
            second_direction = direction(second)
            output.append(
                {
                    "check_type": "direction",
                    "name": name,
                    "segment": segment,
                    "primary_value_ns": fmt(first),
                    "all_eligible_value_ns": fmt(second),
                    "relative_difference": "",
                    "direction_primary": first_direction,
                    "direction_all_eligible": second_direction,
                    "pass": "1" if first_direction == second_direction else "0",
                }
            )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_root", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    selection_path = args.selection or args.results_root / "latency_selection.csv"
    candidates_path = args.candidates or args.results_root / "latency_candidates.csv"
    selection = read_csv(selection_path)
    candidates = [row for row in read_csv(candidates_path) if row.get("eligible") == "1"]
    raw, run, seed, overall, summaries = aggregate(selection)
    _all_raw, _all_run, _all_seed, _all_overall, all_summaries = aggregate(candidates)
    sensitivity = sensitivity_rows(summaries, all_summaries)
    if any(row["pass"] != "1" for row in sensitivity):
        failed = [f"{row['name']}:{row['segment']}" for row in sensitivity if row["pass"] != "1"]
        raise SystemExit("latency sensitivity gate failed: " + ", ".join(failed))
    write_csv(args.output / "run_bin_means.csv", run)
    write_csv(args.output / "seed_bin_means.csv", seed)
    write_csv(args.output / "overall_bin_mean_ci95.csv", overall)
    write_csv(args.output / "latency_v2_summary.csv", summaries)
    write_csv(args.output / "sensitivity.csv", sensitivity)
    print(
        f"latency-v2 analysis: {len(raw)} usable requests, {len(run)} run bins, "
        f"{len(seed)} seed bins, {len(overall)} overall bins"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
