#!/usr/bin/env python3
"""Aggregate latency-v3 using run -> repetition -> seed -> paired-seed inference."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


EXP_DIR = Path(__file__).resolve().parent.parent
RESULTS = EXP_DIR / "results" / "latency-v3"
ANALYSIS = RESULTS / "analysis"
ASSETS = EXP_DIR / "paper_assets" / "latency-v3"
METRICS = ("end_to_end_latency_ns", "discovery_latency_ns", "return_path_latency_ns")
T95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
       7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}


def topology_host_count(value: object) -> int:
    """Accept both the canonical topology name and an already numeric count."""
    text = str(value)
    try:
        count = int(text)
    except ValueError:
        try:
            count = int(text.rsplit("-", 1)[-1])
        except ValueError as exc:
            raise ValueError(f"invalid latency-v3 topology value: {value!r}") from exc
    if count not in (16, 32):
        raise ValueError(f"unsupported latency-v3 host count: {count}")
    return count


def read_csv(path: Path, delimiter: str = ",") -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def write_csv(path: Path, rows: Sequence[Mapping[str, object]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)
    temporary.replace(path)


def average(values: Iterable[float]) -> float:
    values = [value for value in values if not math.isnan(value)]
    return statistics.fmean(values) if values else math.nan


def ci95(values: Sequence[float]) -> Tuple[float, float, float]:
    values = [value for value in values if not math.isnan(value)]
    center = average(values)
    if len(values) < 2:
        return center, math.nan, math.nan
    critical = T95.get(len(values), 1.96)
    half = critical * statistics.stdev(values) / math.sqrt(len(values))
    return center, center - half, center + half


def period_name(workload: str, row: Mapping[str, str]) -> str:
    if workload == "controlled":
        round_index = int(row["round"])
        if round_index <= 2:
            return "early"
        if round_index >= 7:
            return "late"
    else:
        position = int(row["request_index"]) - 16
        if position <= 60:
            return "early"
        if position > 180:
            return "late"
    return "middle"


def load_run(selected: Mapping[str, str]) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    metadata = json.loads(Path(selected["metadata_path"]).read_text(encoding="utf-8"))
    metadata["topology_name"] = metadata["topology"]
    metadata["topology"] = topology_host_count(metadata["topology"])
    trace_path = Path(selected["trace_path"])
    trace = {row.get("request_index", ""): row for row in read_csv(trace_path)}
    result = []
    for row in read_csv(Path(selected["request_results_path"])):
        if row.get("phase") != "measurement" or row.get("status") != "ok":
            continue
        traced = trace.get(row["request_index"], {})
        discovery = row.get("discovery_latency_ns") or traced.get("discovery_delay_ns", "")
        returned = row.get("return_path_latency_ns") or traced.get("return_delay_ns", "")
        result.append({
            **row,
            "end_to_end_latency_ns": float(row["end_to_end_latency_ns"]),
            "discovery_latency_ns": float(discovery) if discovery else math.nan,
            "return_path_latency_ns": float(returned) if returned else math.nan,
            "actual_provider": traced.get("provider", row.get("owner", "")),
            "actual_serving_hops": traced.get("provider_hit_hop", ""),
            "service_class": traced.get("service_class", "tcp_direct" if metadata["method"] == "tcp-direct" else ""),
        })
    return metadata, result


def run_summaries(records: Sequence[Mapping[str, str]]) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], List[Dict[str, object]], Dict[str, Dict[str, object]]]:
    group_values: Dict[Tuple[str, int, int, int], List[Dict[str, object]]] = defaultdict(list)
    period_values: Dict[Tuple[str, int, int, str], List[Dict[str, object]]] = defaultdict(list)
    class_values: Dict[Tuple[str, int, int, str], List[Dict[str, object]]] = defaultdict(list)
    metadata_by_config: Dict[str, Dict[str, object]] = {}
    for record in records:
        metadata, rows = load_run(record)
        config, seed = record["config"], int(record["seed"])
        repetition = int(record.get("selection_index") or record.get("attempt_index") or 0)
        metadata_by_config[config] = metadata
        for row in rows:
            group = int(row["round"] if metadata["workload"] == "controlled" else row["bin"])
            group_values[(config, seed, repetition, group)].append(row)
            period_values[(config, seed, repetition, "all")].append(row)
            period_values[(config, seed, repetition, period_name(str(metadata["workload"]), row))].append(row)
            class_values[(config, seed, repetition, str(row["service_class"]))].append(row)

    def summarize(key: Tuple, rows: Sequence[Mapping[str, object]], label: str, value: object) -> Dict[str, object]:
        config, seed, repetition = key[:3]
        meta = metadata_by_config[config]
        output: Dict[str, object] = {
            "config": config, "seed": seed, "repetition": repetition,
            "workload": meta["workload"], "topology": meta["topology"], "method": meta["method"],
            "value_size_bytes": meta["value_size_bytes"], label: value,
            "scheduled_rows": 16 if meta["workload"] == "controlled" and label == "group_index" else
                              (20 if label == "group_index" else len(rows)),
            "usable_rows": len(rows),
        }
        for metric in METRICS:
            output[f"{metric}_mean"] = average(float(row[metric]) for row in rows)
        return output

    groups = [summarize(key, rows, "group_index", key[3]) for key, rows in sorted(group_values.items())]
    periods = [summarize(key, rows, "period", key[3]) for key, rows in sorted(period_values.items()) if key[3] != "middle"]
    classes = [summarize(key, rows, "service_class", key[3]) for key, rows in sorted(class_values.items())]
    return groups, periods, classes, metadata_by_config


def seed_aggregate(run_rows: Sequence[Mapping[str, object]], grouping: str) -> List[Dict[str, object]]:
    buckets: Dict[Tuple[str, int, str], List[Mapping[str, object]]] = defaultdict(list)
    for row in run_rows:
        buckets[(str(row["config"]), int(row["seed"]), str(row[grouping]))].append(row)
    output = []
    for (config, seed, group), rows in sorted(buckets.items()):
        item: Dict[str, object] = {"config": config, "seed": seed, grouping: group, "repetitions": len(rows)}
        for metric in METRICS:
            item[f"{metric}_mean"] = average(float(row[f"{metric}_mean"]) for row in rows)
        output.append(item)
    return output


def overall_aggregate(seed_rows: Sequence[Mapping[str, object]], grouping: str) -> List[Dict[str, object]]:
    buckets: Dict[Tuple[str, str], List[Mapping[str, object]]] = defaultdict(list)
    for row in seed_rows:
        buckets[(str(row["config"]), str(row[grouping]))].append(row)
    output = []
    for (config, group), rows in sorted(buckets.items()):
        item: Dict[str, object] = {"config": config, grouping: group, "seed_count": len(rows)}
        for metric in METRICS:
            center, low, high = ci95([float(row[f"{metric}_mean"]) for row in rows])
            item[f"{metric}_mean"] = center
            item[f"{metric}_ci95_low"] = low
            item[f"{metric}_ci95_high"] = high
        output.append(item)
    return output


def convergence(run_groups: Sequence[Mapping[str, object]], metadata: Mapping[str, Mapping[str, object]]) -> List[Dict[str, object]]:
    buckets: Dict[Tuple[str, int, int], Dict[int, float]] = defaultdict(dict)
    for row in run_groups:
        if metadata[str(row["config"])]["workload"] == "controlled":
            buckets[(str(row["config"]), int(row["seed"]), int(row["repetition"]))][int(row["group_index"])] = float(row["end_to_end_latency_ns_mean"])
    run_rows = []
    for (config, seed, repetition), rounds in sorted(buckets.items()):
        steady = average(rounds[index] for index in (6, 7, 8))
        threshold = steady * 1.10
        qualifying = math.nan
        for start in range(1, 7):
            if all(rounds.get(index, math.inf) <= threshold for index in range(start, start + 3)):
                later = [rounds.get(index, math.inf) > threshold for index in range(start + 3, 9)]
                if not any(a and b for a, b in zip(later, later[1:])):
                    qualifying = float(start)
                    break
        run_rows.append({"config": config, "seed": seed, "repetition": repetition,
                         "steady_reference_ns": steady, "threshold_ns": threshold,
                         "convergence_round": qualifying,
                         "requests_to_converge": math.nan if math.isnan(qualifying) else 1 + (int(qualifying) - 1) * 16})
    seed_buckets: Dict[Tuple[str, int], List[Mapping[str, object]]] = defaultdict(list)
    for row in run_rows:
        seed_buckets[(str(row["config"]), int(row["seed"]))].append(row)
    output = []
    for (config, seed), rows in sorted(seed_buckets.items()):
        output.append({"config": config, "seed": seed,
                       "convergence_round_mean": average(float(row["convergence_round"]) for row in rows),
                       "requests_to_converge_mean": average(float(row["requests_to_converge"]) for row in rows)})
    return output


def paired_comparisons(period_seeds: Sequence[Mapping[str, object]], metadata: Mapping[str, Mapping[str, object]]) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    lookup = {(str(row["config"]), int(row["seed"]), str(row["period"])): row for row in period_seeds}
    by_spec = {(str(m["workload"]), topology_host_count(m["topology"]), str(m["method"]), int(m["value_size_bytes"])): config
               for config, m in metadata.items()}
    definitions: List[Tuple[str, str, str]] = []
    for workload in ("controlled", "iid-zipf", "iid-uniform"):
        for method in ("nc", "known-owner"):
            definitions.append(("scaling_32_minus_16", by_spec[(workload, 16, method, 65536)], by_spec[(workload, 32, method, 65536)]))
        for topology in (16, 32):
            definitions.append(("known_owner_minus_nc", by_spec[(workload, topology, "nc", 65536)], by_spec[(workload, topology, "known-owner", 65536)]))
    for topology in (16, 32):
        for method in ("nc", "known-owner"):
            definitions.append(("uniform_minus_zipf", by_spec[("iid-zipf", topology, method, 65536)], by_spec[("iid-uniform", topology, method, 65536)]))
    size_rows = []
    for method in ("nc", "known-owner", "tcp-direct"):
        configs = {size: by_spec[("controlled", 32, method, size)] for size in (1024, 65536, 1048576)}
        for low_size, high_size in ((1024, 65536), (65536, 1048576), (1024, 1048576)):
            definitions.append((f"size_{high_size}_minus_{low_size}", configs[low_size], configs[high_size]))
    output = []
    for comparison, left, right in definitions:
        periods = ("all",) if comparison.startswith("size_") else ("early", "late", "all")
        for period in periods:
            for metric in METRICS:
                effects, ratios = [], []
                for seed in range(1, 11):
                    a = float(lookup[(left, seed, period)][f"{metric}_mean"])
                    b = float(lookup[(right, seed, period)][f"{metric}_mean"])
                    if not math.isnan(a) and not math.isnan(b):
                        effects.append(b - a)
                        ratios.append(b / a if a else math.nan)
                center, low, high = ci95(effects)
                ratio, ratio_low, ratio_high = ci95(ratios)
                row = {"comparison": comparison, "left_config": left, "right_config": right,
                       "period": period, "metric": metric, "paired_seed_count": len(effects),
                       "difference_mean": center, "difference_ci95_low": low, "difference_ci95_high": high,
                       "ratio_mean": ratio, "ratio_ci95_low": ratio_low, "ratio_ci95_high": ratio_high}
                output.append(row)
                if comparison.startswith("size_"):
                    left_size = int(metadata[left]["value_size_bytes"]); right_size = int(metadata[right]["value_size_bytes"])
                    size_rows.append({**row, "left_size_bytes": left_size, "right_size_bytes": right_size,
                                      "slope_ns_per_byte": center / (right_size - left_size),
                                      "slope_ns_per_log2_size": center / math.log2(right_size / left_size)})
    return output, size_rows


def sensitivity(primary: Sequence[Mapping[str, object]], qualified: Sequence[Mapping[str, str]]) -> List[Dict[str, object]]:
    _, all_periods, _, _ = run_summaries(qualified)
    primary_seeds = seed_aggregate(primary, "period")
    all_seeds = seed_aggregate(all_periods, "period")
    p = {(str(row["config"]), int(row["seed"]), str(row["period"])): row for row in primary_seeds}
    a = {(str(row["config"]), int(row["seed"]), str(row["period"])): row for row in all_seeds}
    output = []
    configs = sorted({str(row["config"]) for row in primary})
    for config in configs:
        for period in ("early", "late"):
            for metric in METRICS:
                primary_mean = average(float(p[(config, seed, period)][f"{metric}_mean"]) for seed in range(1, 11))
                all_mean = average(float(a[(config, seed, period)][f"{metric}_mean"]) for seed in range(1, 11))
                relative = abs(all_mean - primary_mean) / abs(primary_mean) if primary_mean else math.nan
                output.append({"config": config, "period": period, "metric": metric,
                               "primary_mean": primary_mean, "all_qualified_mean": all_mean,
                               "relative_difference": relative,
                               "within_5_percent": "1" if relative <= 0.05 else "0"})
        early_p = average(float(p[(config, seed, "early")]["end_to_end_latency_ns_mean"]) for seed in range(1, 11))
        late_p = average(float(p[(config, seed, "late")]["end_to_end_latency_ns_mean"]) for seed in range(1, 11))
        early_a = average(float(a[(config, seed, "early")]["end_to_end_latency_ns_mean"]) for seed in range(1, 11))
        late_a = average(float(a[(config, seed, "late")]["end_to_end_latency_ns_mean"]) for seed in range(1, 11))
        output.append({"config": config, "period": "early_to_late", "metric": "direction",
                       "primary_mean": late_p - early_p, "all_qualified_mean": late_a - early_a,
                       "relative_difference": "", "within_5_percent": "1" if (late_p - early_p) * (late_a - early_a) >= 0 else "0"})
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    selection = read_csv(RESULTS / "latency_selection.csv")
    if len(selection) != 380 and not args.allow_incomplete:
        raise SystemExit(f"latency-v3 requires 380 selected runs, found {len(selection)}")
    groups, periods, classes, metadata = run_summaries(selection)
    group_seeds = seed_aggregate(groups, "group_index")
    period_seeds = seed_aggregate(periods, "period")
    overall_groups = overall_aggregate(group_seeds, "group_index")
    overall_periods = overall_aggregate(period_seeds, "period")
    convergence_rows = convergence(groups, metadata)
    paired, sizes = paired_comparisons(period_seeds, metadata) if len(metadata) == 19 else ([], [])
    sensitivity_rows = sensitivity(periods, read_csv(RESULTS / "qualified_attempts.csv")) if selection else []
    outputs = (
        ("run_group_means.csv", groups), ("seed_group_means.csv", group_seeds),
        ("overall_group_mean_ci95.csv", overall_groups), ("run_period_means.csv", periods),
        ("seed_early_late.csv", period_seeds), ("overall_early_late_ci95.csv", overall_periods),
        ("run_service_class_means.csv", classes), ("controlled_convergence.csv", convergence_rows),
        ("paired_effects.csv", paired), ("size_effects.csv", sizes), ("sensitivity.csv", sensitivity_rows),
    )
    for filename, rows in outputs:
        write_csv(ANALYSIS / filename, rows, list(rows[0]) if rows else [])
        if rows and filename in {"overall_group_mean_ci95.csv", "overall_early_late_ci95.csv", "paired_effects.csv", "size_effects.csv"}:
            write_csv(ASSETS / filename, rows, list(rows[0]))
    if sensitivity_rows and any(row["within_5_percent"] != "1" for row in sensitivity_rows):
        raise SystemExit("latency-v3 sensitivity gate failed")
    print(f"wrote latency-v3 analysis to {ANALYSIS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
