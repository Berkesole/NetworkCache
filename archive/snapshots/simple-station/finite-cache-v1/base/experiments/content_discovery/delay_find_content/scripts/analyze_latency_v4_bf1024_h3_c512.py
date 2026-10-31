#!/usr/bin/env python3
"""Aggregate latency-v4 as repetition means, seed means and paired 10-seed effects."""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


EXP = Path(__file__).resolve().parent.parent
SUITE = "latency-v4-bf1024-h3-c512"
RESULTS = EXP / "results" / f"{SUITE}-formal-r2"
ANALYSIS = RESULTS / "analysis"
ASSETS = EXP / "paper_assets" / "networkcache-bf1024-h3-formal-r2" / "latency"
METRICS = ("end_to_end_latency_ns", "discovery_latency_proxy_ns",
           "return_path_latency_proxy_ns", "source_fallback_triggered",
           "preferred_logical_request_events", "fallback_logical_request_events")
T95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
       7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}


def read_csv(path: Path, delimiter: str = ",") -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else []
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def average(values: Iterable[float]) -> float:
    finite = [value for value in values if not math.isnan(value)]
    return statistics.fmean(finite) if finite else math.nan


def ci95(values: Sequence[float]) -> Tuple[float, float, float]:
    finite = [value for value in values if not math.isnan(value)]
    center = average(finite)
    if len(finite) < 2:
        return center, math.nan, math.nan
    half = T95.get(len(finite), 1.96) * statistics.stdev(finite) / math.sqrt(len(finite))
    return center, center - half, center + half


def numeric(row: Mapping[str, str], field: str) -> float:
    try:
        return float(row[field])
    except (KeyError, TypeError, ValueError):
        return math.nan


def period(workload: str, row: Mapping[str, str], final_bin: int) -> str:
    if workload == "controlled":
        group = int(row["round"])
        return "early" if group <= 2 else "late" if group >= 7 else "middle"
    group = int(row["bin"])
    return "early" if group <= 4 else "late" if group > final_bin - 4 else "middle"


def summarize_selection(selection: Sequence[Mapping[str, str]]) -> Tuple[List[dict], List[dict], Dict[str, dict], List[dict], List[dict]]:
    groups: Dict[Tuple[str, int, int, int], List[dict]] = defaultdict(list)
    periods: Dict[Tuple[str, int, int, str], List[dict]] = defaultdict(list)
    metadata: Dict[str, dict] = {}
    bloom_rows: List[dict] = []
    unique_rows: List[dict] = []
    for selected in selection:
        config = selected["config"]
        seed = int(selected["seed"])
        repetition = int(selected["selection_index"])
        run_dir = Path(selected["run_dir"])
        meta = json.loads((run_dir / "metadata.json").read_text())
        metadata[config] = meta
        trace = [row for row in read_csv(run_dir / "trace_breakdown.csv") if row["phase"] == "measurement"]
        final_bin = max((int(row["bin"]) for row in trace), default=0)
        seen: set[str] = set()
        for row in trace:
            group = int(row["round"] if meta["workload"] == "controlled" else row["bin"])
            groups[(config, seed, repetition, group)].append(row)
            periods[(config, seed, repetition, "all")].append(row)
            periods[(config, seed, repetition, period(meta["workload"], row, final_bin))].append(row)
            seen.add(row["key"])
            unique_rows.append({"config": config, "seed": seed, "repetition": repetition,
                                "request_index": row["request_index"], "group_index": group,
                                "cumulative_unique_keys": len(seen)})
        bloom = read_csv(run_dir / "sync_bloom_state.tsv", "\t") if (run_dir / "sync_bloom_state.tsv").exists() else []
        for snapshot, rows in _group(bloom, lambda item: item["snapshot_id"]):
            values = [numeric(row, "occupancy") for row in rows]
            bloom_rows.append({"config": config, "seed": seed, "repetition": repetition,
                               "snapshot_id": snapshot, "phase": rows[0]["phase"], "bin": rows[0]["bin"],
                               "bloom_occupancy_mean": average(values), "bloom_occupancy_max": max(values)})

    def summarize(key: Tuple, rows: Sequence[dict], label: str, value: object) -> dict:
        config, seed, repetition = key[:3]
        result = {"config": config, "seed": seed, "repetition": repetition,
                  "workload": metadata[config]["workload"], "topology": metadata[config]["topology"],
                  "method": metadata[config]["method"], "value_size_bytes": metadata[config]["value_size_bytes"],
                  label: value, "usable_rows": len(rows)}
        for metric in METRICS:
            result[f"{metric}_mean"] = average(numeric(row, metric) for row in rows)
        triggered = [numeric(row, "fallback_trigger_to_delivery_ns") for row in rows
                     if row.get("source_fallback_triggered") == "1"]
        result["fallback_trigger_to_delivery_ns_mean"] = average(triggered)
        result["first_observed_cache_fraction_proxy"] = average(
            row.get("first_observed_service_class_proxy") == "cache" for row in rows)
        result["multiple_matching_provider_fraction"] = average(
            row.get("multiple_matching_provider_hits") == "1" for row in rows)
        return result
    run_groups = [summarize(key, rows, "group_index", key[3])
                  for key, rows in sorted(groups.items())]
    run_periods = [summarize(key, rows, "period", key[3])
                   for key, rows in sorted(periods.items()) if key[3] != "middle"]
    return run_groups, run_periods, metadata, bloom_rows, unique_rows


def _group(rows: Sequence[dict], key):
    buckets = defaultdict(list)
    for row in rows:
        buckets[key(row)].append(row)
    return sorted(buckets.items())


def aggregate(rows: Sequence[dict], grouping: str, seed_level: bool) -> List[dict]:
    key_fields = ("config", "seed", grouping) if seed_level else ("config", grouping)
    buckets = defaultdict(list)
    for row in rows:
        buckets[tuple(str(row[field]) for field in key_fields)].append(row)
    output = []
    metric_columns = [column for column in rows[0] if column.endswith("_mean") or column.endswith("_fraction_proxy")]
    for key, members in sorted(buckets.items()):
        item = {field: value for field, value in zip(key_fields, key)}
        item["repetitions" if seed_level else "seed_count"] = len(members)
        for metric in metric_columns:
            values = [float(row[metric]) for row in members]
            if seed_level:
                item[metric] = average(values)
            else:
                center, low, high = ci95(values)
                item[metric] = center
                item[f"{metric}_ci95_low"] = low
                item[f"{metric}_ci95_high"] = high
        output.append(item)
    return output


def paired(period_seeds: Sequence[dict], metadata: Mapping[str, dict]) -> List[dict]:
    lookup = {(row["config"], int(row["seed"]), row["period"]): row for row in period_seeds}
    specs = {(m["workload"], int(str(m["topology"]).rsplit("-", 1)[-1]), m["method"], int(m["value_size_bytes"])): config
             for config, m in metadata.items()}
    definitions = []
    for workload in ("controlled", "iid-zipf", "iid-uniform"):
        for method in ("nc", "known-owner"):
            definitions.append(("32_minus_16", specs[(workload, 16, method, 65536)], specs[(workload, 32, method, 65536)]))
        for topology in (16, 32):
            definitions.append(("known_owner_minus_nc", specs[(workload, topology, "nc", 65536)], specs[(workload, topology, "known-owner", 65536)]))
    for topology in (16, 32):
        for method in ("nc", "known-owner"):
            definitions.append(("uniform_minus_zipf", specs[("iid-zipf", topology, method, 65536)], specs[("iid-uniform", topology, method, 65536)]))
    for method in ("nc", "known-owner", "tcp-direct"):
        for low, high in ((1024, 65536), (65536, 1048576), (1024, 1048576)):
            definitions.append((f"size_{high}_minus_{low}", specs[("controlled", 32, method, low)], specs[("controlled", 32, method, high)]))
    output = []
    for comparison, left, right in definitions:
        for period_name in (("all",) if comparison.startswith("size_") else ("early", "late", "all")):
            for metric in METRICS:
                effects, ratios = [], []
                for seed in range(1, 11):
                    a = float(lookup[(left, seed, period_name)][f"{metric}_mean"])
                    b = float(lookup[(right, seed, period_name)][f"{metric}_mean"])
                    if not (math.isnan(a) or math.isnan(b)):
                        effects.append(b - a); ratios.append(b / a if a else math.nan)
                center, low, high = ci95(effects)
                ratio, ratio_low, ratio_high = ci95(ratios)
                output.append({"comparison": comparison, "left_config": left, "right_config": right,
                               "period": period_name, "metric": metric, "paired_seed_count": len(effects),
                               "difference_mean": center, "difference_ci95_low": low, "difference_ci95_high": high,
                               "ratio_mean": ratio, "ratio_ci95_low": ratio_low, "ratio_ci95_high": ratio_high})
    return output


def aggregate_bloom(rows: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    seed_buckets = defaultdict(list)
    for row in rows:
        seed_buckets[(row["config"], int(row["seed"]), row["snapshot_id"], row["phase"], row["bin"])].append(row)
    seeds = []
    for (config, seed, snapshot, phase, bin_index), members in sorted(seed_buckets.items()):
        seeds.append({"config": config, "seed": seed, "snapshot_id": snapshot,
                      "phase": phase, "bin": bin_index, "repetitions": len(members),
                      "bloom_occupancy_mean": average(float(row["bloom_occupancy_mean"]) for row in members),
                      "bloom_occupancy_max": average(float(row["bloom_occupancy_max"]) for row in members)})
    overall_buckets = defaultdict(list)
    for row in seeds:
        overall_buckets[(row["config"], row["snapshot_id"], row["phase"], row["bin"])].append(row)
    overall = []
    for (config, snapshot, phase, bin_index), members in sorted(overall_buckets.items()):
        item = {"config": config, "snapshot_id": snapshot, "phase": phase, "bin": bin_index,
                "seed_count": len(members)}
        for metric in ("bloom_occupancy_mean", "bloom_occupancy_max"):
            center, low, high = ci95([float(row[metric]) for row in members])
            item[metric] = center; item[f"{metric}_ci95_low"] = low; item[f"{metric}_ci95_high"] = high
        overall.append(item)
    return seeds, overall


def main() -> int:
    selection = read_csv(RESULTS / "latency_selection.csv")
    if len(selection) != 380:
        raise SystemExit(f"latency-v4 requires 380 selected runs, found {len(selection)}")
    groups, periods, metadata, bloom, unique = summarize_selection(selection)
    seed_groups = aggregate(groups, "group_index", True)
    seed_periods = aggregate(periods, "period", True)
    overall_groups = aggregate(seed_groups, "group_index", False)
    overall_periods = aggregate(seed_periods, "period", False)
    pairs = paired(seed_periods, metadata)
    seed_bloom, overall_bloom = aggregate_bloom(bloom)
    outputs = {
        "run_group_means.csv": groups, "seed_group_means.csv": seed_groups,
        "overall_group_mean_ci95.csv": overall_groups, "run_period_means.csv": periods,
        "seed_early_late.csv": seed_periods, "overall_early_late_ci95.csv": overall_periods,
        "paired_effects.csv": pairs, "run_bloom_occupancy.csv": bloom,
        "seed_bloom_occupancy.csv": seed_bloom, "overall_bloom_occupancy_ci95.csv": overall_bloom,
        "run_unique_key_trajectory.csv": unique,
    }
    for filename, rows in outputs.items():
        write_csv(ANALYSIS / filename, rows)
        if filename.startswith("overall_") or filename == "paired_effects.csv":
            write_csv(ASSETS / filename, rows)
    print(f"wrote latency-v4 analysis to {ANALYSIS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
