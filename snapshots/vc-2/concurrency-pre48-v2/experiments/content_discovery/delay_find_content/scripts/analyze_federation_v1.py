#!/usr/bin/env python3
"""Aggregate selected federation-v1 runs as run -> seed -> paired-seed effects."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXP = SCRIPT_DIR.parent
RESULTS = EXP / "results/federation-v1"
ANALYSIS = RESULTS / "analysis"
ASSETS = EXP / "paper_assets/federation-v1"
T975_DF9 = 2.262157


def read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle: return list(csv.DictReader(handle))


def summary(values: list[float]) -> tuple[float, float, float]:
    mean = statistics.mean(values)
    half = T975_DF9 * statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0
    return mean, mean - half, mean + half


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys())); writer.writeheader(); writer.writerows(rows)


def optional_mean(rows: list[dict], field: str) -> float | str:
    values = [float(row[field]) for row in rows if row.get(field) not in (None, "")]
    return statistics.mean(values) if values else ""


def convergence_round(round_means: list[float]) -> int | str:
    steady = statistics.mean(round_means[-3:])
    threshold = steady * 1.10
    for start in range(0, len(round_means) - 2):
        if not all(value <= threshold for value in round_means[start:start + 3]): continue
        if any(round_means[index] > threshold and round_means[index + 1] > threshold
               for index in range(start + 3, len(round_means) - 1)):
            continue
        return start + 1
    return ""


def main() -> int:
    selection = read(RESULTS / "federation_selection.csv")
    if len(selection) != 80: raise SystemExit(f"federation-v1 requires 80 selected runs, found {len(selection)}")
    run_rounds, run_metrics = [], []
    for selected in selection:
        rows = read(Path(selected["run_dir"]) / "request_results.csv")
        measured = [r for r in rows if r["phase"] == "measurement"]
        trace_path = Path(selected["run_dir"]) / "trace_breakdown.csv"
        trace = read(trace_path) if trace_path.exists() else []
        trace_by_index = {row["request_index"]: row for row in trace}
        for round_index in range(1, 9):
            for direction in ("A_to_B", "B_to_A", "both"):
                subset = [r for r in measured if int(r["round"]) == round_index and
                          (direction == "both" or r["direction"] == direction)]
                trace_subset = [trace_by_index[r["request_index"]] for r in subset
                                if r["request_index"] in trace_by_index]
                run_rounds.append({**{k: selected[k] for k in ("config", "workload", "method", "seed", "selection_index")},
                                   "round": round_index, "direction": direction, "request_rows": len(subset),
                                   "discovery_latency_ns": optional_mean(trace_subset, "discovery_latency_ns"),
                                   "return_path_latency_ns": optional_mean(trace_subset, "return_path_latency_ns"),
                                   "end_to_end_latency_ns": statistics.mean(int(r["end_to_end_latency_ns"]) for r in subset),
                                   "cross_link_request_frames": sum(int(r.get("cross_link_request_frames") or 0) for r in trace_subset),
                                   "cross_link_data_frames": sum(int(r.get("cross_link_data_frames") or 0) for r in trace_subset),
                                   "cross_link_request_bytes": sum(int(r.get("cross_link_request_bytes") or 0) for r in trace_subset),
                                   "cross_link_data_bytes": sum(int(r.get("cross_link_data_bytes") or 0) for r in trace_subset)})
        first = measured[0]
        cache_hits = [r for r in trace if r.get("phase") == "measurement" and r.get("service_class") == "cache"]
        first_cache = min(cache_hits, key=lambda r: int(r["request_index"])) if cache_hits else None
        activation = json.loads((Path(selected["run_dir"]) / "activation.json").read_text())
        run_metrics.append({**{k: selected[k] for k in ("config", "workload", "method", "seed", "selection_index")},
                            "activation_apply_latency_ns": int(activation["activation_apply_latency_ns"]),
                            "activation_to_request_start_ns": int(first["activation_to_request_start_ns"]),
                            "activation_to_first_remote_data_ns": int(first["activation_to_remote_data_ns"]),
                            "first_remote_request_latency_ns": int(first["end_to_end_latency_ns"]),
                            "early_latency_ns": statistics.mean(int(r["end_to_end_latency_ns"]) for r in measured if r["round"] == "1"),
                            "late_latency_ns": statistics.mean(int(r["end_to_end_latency_ns"]) for r in measured if r["round"] in ("7", "8")),
                            "requests_to_first_cache_assisted_service": int(first_cache["request_index"]) - 64 if first_cache else "",
                            "time_to_first_cache_assisted_service_ns": int(first_cache["app_receive_mono_ns"]) - int(first["federation_ready_ns"]) if first_cache else ""})
    seed_rounds, grouped = [], defaultdict(list)
    for row in run_rounds: grouped[(row["config"], row["seed"], row["round"], row["direction"])].append(row)
    for (config, seed, rnd, direction), rows in grouped.items():
        item = {"config": config, "seed": seed, "round": rnd, "direction": direction,
                "repetition_count": len(rows)}
        for name in ("discovery_latency_ns", "return_path_latency_ns", "end_to_end_latency_ns",
                     "cross_link_request_frames", "cross_link_data_frames",
                     "cross_link_request_bytes", "cross_link_data_bytes"):
            item[name] = optional_mean(rows, name)
        seed_rounds.append(item)
    overall = []
    grouped.clear()
    for row in seed_rounds: grouped[(row["config"], row["round"], row["direction"])].append(float(row["end_to_end_latency_ns"]))
    for (config, rnd, direction), values in grouped.items():
        mean, low, high = summary(values)
        item = {"config": config, "round": rnd, "direction": direction, "seed_count": len(values),
                "end_to_end_latency_ns_mean": mean, "end_to_end_ci95_low": low, "end_to_end_ci95_high": high,
                "ci95_low": low, "ci95_high": high}
        for name in ("discovery_latency_ns", "return_path_latency_ns", "cross_link_request_frames",
                     "cross_link_data_frames", "cross_link_request_bytes", "cross_link_data_bytes"):
            metric_values = [float(row[name]) for row in seed_rounds
                             if row["config"] == config and row["round"] == rnd and row["direction"] == direction
                             and row.get(name) != ""]
            if metric_values:
                metric_mean, metric_low, metric_high = summary(metric_values)
                item[f"{name}_mean"] = metric_mean
                item[f"{name}_ci95_low"] = metric_low
                item[f"{name}_ci95_high"] = metric_high
            else:
                item[f"{name}_mean"] = item[f"{name}_ci95_low"] = item[f"{name}_ci95_high"] = ""
        overall.append(item)
    seed_metrics, grouped = [], defaultdict(list)
    for row in run_metrics: grouped[(row["config"], row["seed"])].append(row)
    metric_names = ("activation_apply_latency_ns", "activation_to_request_start_ns",
                    "activation_to_first_remote_data_ns", "first_remote_request_latency_ns",
                    "early_latency_ns", "late_latency_ns")
    for (config, seed), rows in grouped.items():
        item = {"config": config, "seed": seed, "repetition_count": len(rows)}
        item.update({name: statistics.mean(r[name] for r in rows) for name in metric_names})
        for name in ("requests_to_first_cache_assisted_service", "time_to_first_cache_assisted_service_ns"):
            values = [float(r[name]) for r in rows if r.get(name) != ""]
            item[name] = statistics.mean(values) if values else ""
        seed_metrics.append(item)
    metrics = []
    for config in sorted({r["config"] for r in seed_metrics}):
        items = [r for r in seed_metrics if r["config"] == config]
        for name in metric_names:
            mean, low, high = summary([float(r[name]) for r in items])
            metrics.append({"config": config, "metric": name, "seed_count": len(items), "mean": mean, "ci95_low": low, "ci95_high": high})
        for name in ("requests_to_first_cache_assisted_service", "time_to_first_cache_assisted_service_ns"):
            values = [float(r[name]) for r in items if r.get(name) != ""]
            if values:
                mean, low, high = summary(values)
                metrics.append({"config": config, "metric": name, "seed_count": len(values), "mean": mean, "ci95_low": low, "ci95_high": high})
    effects = []
    comparisons = (("repeated-known-owner", "repeated-nc", "known_owner_minus_nc"),
                   ("repeated-tcp-direct", "repeated-nc", "tcp_direct_minus_nc"),
                   ("uniform-nc", "repeated-nc", "uniform_minus_repeated"))
    by = {(r["config"], r["seed"]): r for r in seed_metrics}
    for left, right, label in comparisons:
        for metric in ("first_remote_request_latency_ns", "late_latency_ns"):
            values = [float(by[(left, str(seed))][metric]) - float(by[(right, str(seed))][metric]) for seed in range(1, 11)]
            mean, low, high = summary(values)
            effects.append({"comparison": label, "metric": metric, "seed_count": 10,
                            "difference_mean": mean, "difference_ci95_low": low, "difference_ci95_high": high})
    for config in sorted({r["config"] for r in seed_metrics}):
        direction_values = []
        for seed in range(1, 11):
            a = [float(r["end_to_end_latency_ns"]) for r in seed_rounds
                 if r["config"] == config and r["seed"] == str(seed) and r["direction"] == "A_to_B" and int(r["round"]) >= 7]
            b = [float(r["end_to_end_latency_ns"]) for r in seed_rounds
                 if r["config"] == config and r["seed"] == str(seed) and r["direction"] == "B_to_A" and int(r["round"]) >= 7]
            direction_values.append(statistics.mean(a) - statistics.mean(b))
        mean, low, high = summary(direction_values)
        effects.append({"comparison": f"{config}:A_to_B_minus_B_to_A", "metric": "late_latency_ns",
                        "seed_count": 10, "difference_mean": mean,
                        "difference_ci95_low": low, "difference_ci95_high": high})

    convergences = []
    for config in sorted({r["config"] for r in seed_rounds}):
        values = []
        for seed in range(1, 11):
            rounds = sorted((int(r["round"]), float(r["end_to_end_latency_ns"])) for r in seed_rounds
                            if r["config"] == config and r["seed"] == str(seed) and r["direction"] == "both")
            qualifying = convergence_round([value for _, value in rounds])
            convergences.append({"config": config, "seed": seed, "convergence_round": qualifying,
                                 "requests_to_converge": (int(qualifying) - 1) * 16 + 1 if qualifying != "" else "",
                                 "ci95_low": "", "ci95_high": ""})
            if qualifying != "": values.append(float(qualifying))
        if values:
            mean, low, high = summary(values)
            convergences.append({"config": config, "seed": "overall", "convergence_round": mean,
                                 "requests_to_converge": (mean - 1) * 16 + 1,
                                 "ci95_low": low, "ci95_high": high})

    runner_spec = importlib.util.spec_from_file_location("federation_v1_runner", SCRIPT_DIR / "run_federation_v1_suite.py")
    runner = importlib.util.module_from_spec(runner_spec); sys.modules[runner_spec.name] = runner
    assert runner_spec.loader; runner_spec.loader.exec_module(runner)
    sensitivity = []
    all_attempt_metric_means: dict[tuple[str, str], float] = {}
    for config in runner.CONFIGS:
        selected_config = [r for r in seed_metrics if r["config"] == config.slug]
        for metric, rounds in (("early_latency_ns", {"1"}), ("late_latency_ns", {"7", "8"})):
            selected_mean = statistics.mean(float(r[metric]) for r in selected_config)
            all_seed_means = []
            for seed in range(1, 11):
                attempt_means = []
                for path in runner.dirs(config, seed):
                    if not runner.validate(config, path)[0]: continue
                    measured = [r for r in read(path / "request_results.csv")
                                if r["phase"] == "measurement" and r["round"] in rounds]
                    attempt_means.append(statistics.mean(int(r["end_to_end_latency_ns"]) for r in measured))
                all_seed_means.append(statistics.mean(attempt_means))
            all_mean = statistics.mean(all_seed_means)
            all_attempt_metric_means[(config.slug, metric)] = all_mean
            relative = abs(all_mean - selected_mean) / selected_mean
            sensitivity.append({"config": config.slug, "metric": metric,
                                "selected_earliest_two_mean_ns": selected_mean,
                                "all_qualified_attempts_mean_ns": all_mean,
                                "relative_difference": relative, "within_5_percent": int(relative <= 0.05),
                                "direction_consistent": ""})
    for left, right, label in comparisons:
        for metric in ("early_latency_ns", "late_latency_ns"):
            selected_left = statistics.mean(float(r[metric]) for r in seed_metrics if r["config"] == left)
            selected_right = statistics.mean(float(r[metric]) for r in seed_metrics if r["config"] == right)
            selected_effect = selected_left - selected_right
            all_effect = all_attempt_metric_means[(left, metric)] - all_attempt_metric_means[(right, metric)]
            consistent = selected_effect == 0 or all_effect == 0 or (selected_effect > 0) == (all_effect > 0)
            sensitivity.append({"config": label, "metric": f"{metric}:paired_direction",
                                "selected_earliest_two_mean_ns": selected_effect,
                                "all_qualified_attempts_mean_ns": all_effect,
                                "relative_difference": "", "within_5_percent": "",
                                "direction_consistent": int(consistent)})
    for target in (ANALYSIS, ASSETS):
        write(target / "overall_round_mean_ci95.csv", overall)
        write(target / "federation_metrics.csv", metrics)
        write(target / "paired_effects.csv", effects)
        write(target / "convergence.csv", convergences)
        write(target / "sensitivity.csv", sensitivity)
    if any(row["within_5_percent"] == 0 for row in sensitivity):
        raise SystemExit("federation-v1 sensitivity gate failed: selected/all-attempt difference exceeds 5%")
    if any(row["direction_consistent"] == 0 for row in sensitivity):
        raise SystemExit("federation-v1 sensitivity gate failed: a predefined comparison changes direction")
    print(f"wrote federation-v1 analysis to {ANALYSIS}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
