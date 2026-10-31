#!/usr/bin/env python3
"""Aggregate federation-v2 as repetition -> seed -> paired ten-seed effects."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXP = SCRIPT_DIR.parent
RESULTS = EXP / "results/federation-v2"
ANALYSIS = RESULTS / "analysis"
ASSETS = EXP / "paper_assets/federation-v2"
FIGURES = ASSETS / "figures"
T975_DF9 = 2.262157


def read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        if columns:
            writer.writeheader(); writer.writerows(rows)


def mean(values: list[float]) -> float | str:
    return statistics.mean(values) if values else ""


def ci(values: list[float]) -> tuple[float, float, float]:
    centre = statistics.mean(values)
    half = T975_DF9 * statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0
    return centre, centre - half, centre + half


def numeric(rows: list[dict], field: str) -> list[float]:
    return [float(row[field]) for row in rows if row.get(field) not in (None, "")]


def seed_aggregate(run_rows: list[dict], keys: tuple[str, ...], metrics: tuple[str, ...]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in run_rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    output = []
    for identity, rows in grouped.items():
        item = dict(zip(keys, identity)); item["repetition_count"] = len(rows)
        for metric in metrics:
            item[metric] = mean(numeric(rows, metric))
        output.append(item)
    return output


def overall_aggregate(seed_rows: list[dict], keys: tuple[str, ...], metrics: tuple[str, ...]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in seed_rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    output = []
    for identity, rows in grouped.items():
        item = dict(zip(keys, identity)); item["seed_count"] = len(rows)
        for metric in metrics:
            values = numeric(rows, metric)
            if values:
                centre, low, high = ci(values)
                item[f"{metric}_mean"] = centre
                item[f"{metric}_ci95_low"] = low
                item[f"{metric}_ci95_high"] = high
            else:
                item[f"{metric}_mean"] = item[f"{metric}_ci95_low"] = item[f"{metric}_ci95_high"] = ""
        output.append(item)
    return output


def cluster_for_direction(direction: str) -> str:
    return direction[0]


def run_outputs(selected: dict[str, str]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    root = Path(selected["run_dir"])
    requests = read(root / "request_results.csv")
    trace_path = root / "trace_breakdown.csv"
    trace = read(trace_path) if trace_path.exists() else []
    activation = json.loads((root / "activation.json").read_text())
    trace_by_index = {row["request_index"]: row for row in trace}
    identity = {key: selected[key] for key in ("config", "workload", "method", "seed", "selection_index")}

    trajectory = []
    groups = []
    for phase, request_class, directions in (
        ("pre", "local", ("A_local", "B_local")),
        ("post", "remote", ("A_to_B", "B_to_A")),
        ("post", "local_control", ("A_local", "B_local")),
    ):
        for round_index in range(1, 9):
            for direction in (*directions, "both"):
                subset = [row for row in requests if row["phase"] == phase and
                          row["request_class"] == request_class and row["round"] == str(round_index) and
                          (direction == "both" or row["direction"] == direction)]
                if not subset:
                    continue
                traces = [trace_by_index[row["request_index"]] for row in subset
                          if row["request_index"] in trace_by_index]
                trajectory.append({**identity, "phase": phase, "request_class": request_class,
                                   "round": round_index, "direction": direction, "request_rows": len(subset),
                                   "end_to_end_latency_ns": mean(numeric(subset, "end_to_end_latency_ns")),
                                   "discovery_latency_ns": mean(numeric(traces, "discovery_latency_ns")),
                                   "return_path_latency_ns": mean(numeric(traces, "return_path_latency_ns")),
                                   "request_forward_events": mean(numeric(traces, "request_forward_events")),
                                   "request_frame_bytes": mean(numeric(traces, "request_frame_bytes")),
                                   "serving_overlay_hops": mean(numeric(traces, "serving_overlay_hops")),
                                   "cross_link_request_frames": mean(numeric(traces, "cross_link_request_frames")),
                                   "cross_link_data_frames": mean(numeric(traces, "cross_link_data_frames"))})

    remote = [row for row in requests if row["phase"] == "post" and row["request_class"] == "remote"]
    remote_trace = [trace_by_index[row["request_index"]] for row in remote if row["request_index"] in trace_by_index]
    for direction in ("A_to_B", "B_to_A", "both"):
        subset = [row for row in remote if direction == "both" or row["direction"] == direction]
        traces = [trace_by_index[row["request_index"]] for row in subset if row["request_index"] in trace_by_index]
        cold_indices = {min((int(row["request_index"]) for row in subset if row["key"] == key), default=-1)
                        for key in {row["key"] for row in subset}}
        cold = [row for row in subset if int(row["request_index"]) in cold_indices]
        cache = [row for row in traces if row.get("service_class") == "cache"]
        first = min(subset, key=lambda row: int(row["request_index"]))
        first_cache = min(cache, key=lambda row: int(row["request_index"])) if cache else None
        groups.append({**identity, "direction": direction,
                       "activation_apply_latency_ns": int(activation["activation_apply_latency_ns"]),
                       "activation_to_first_remote_request_start_ns": int(first["activation_to_request_start_ns"]),
                       "activation_to_first_remote_data_ns": int(first["activation_to_remote_data_ns"]),
                       "first_remote_request_latency_ns": int(first["end_to_end_latency_ns"]),
                       "cold_remote_latency_ns": mean(numeric(cold, "end_to_end_latency_ns")),
                       "early_remote_latency_ns": mean(numeric([row for row in subset if row["round"] in ("1", "2")], "end_to_end_latency_ns")),
                       "late_remote_latency_ns": mean(numeric([row for row in subset if row["round"] in ("7", "8")], "end_to_end_latency_ns")),
                       "cache_assisted_latency_ns": mean(numeric(cache, "end_to_end_latency_ns")),
                       "requests_to_first_cache_assisted_service":
                           sum(int(row["request_index"]) <= int(first_cache["request_index"]) for row in remote) if first_cache else "",
                       "time_to_first_cache_assisted_service_ns":
                           int(first_cache["app_receive_mono_ns"]) - int(first["federation_ready_ns"]) if first_cache else ""})

    service = []
    for round_index in range(1, 9):
        for direction in ("A_to_B", "B_to_A", "both"):
            subset = [row for row in remote_trace if row["round"] == str(round_index) and
                      (direction == "both" or row["direction"] == direction)]
            if not subset:
                continue
            service.append({**identity, "round": round_index, "direction": direction,
                            "request_rows": len(subset),
                            "original_owner_fraction": sum(row.get("service_class") == "original_owner" for row in subset) / len(subset) if subset else "",
                            "cache_fraction": sum(row.get("service_class") == "cache" for row in subset) / len(subset) if subset else "",
                            "serving_overlay_hops": mean(numeric(subset, "serving_overlay_hops")),
                            "request_forward_events": mean(numeric(subset, "request_forward_events"))})

    occupancy = []
    occupancy_path = root / "cache_occupancy.csv"
    if occupancy_path.exists():
        raw = read(occupancy_path)
        grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for row in raw: grouped[(row["snapshot_id"], row["phase"], row["cluster"])].append(row)
        for (snapshot_id, phase, cluster_name), rows in grouped.items():
            occupancy.append({**identity, "snapshot_id": snapshot_id, "phase": phase,
                              "round": rows[0]["round"], "cluster": cluster_name,
                              "db_keys": sum(float(row["db_keys"]) for row in rows),
                              "logical_value_bytes": sum(float(row["logical_value_bytes"]) for row in rows),
                              "original_keys": sum(float(row["original_keys"]) for row in rows),
                              "cached_copy_keys": sum(float(row["cached_copy_keys"]) for row in rows),
                              "cached_copy_logical_bytes": sum(float(row["cached_copy_logical_bytes"]) for row in rows),
                              "redis_used_memory_bytes": sum(float(row["redis_used_memory_bytes"]) for row in rows)})
    return trajectory, groups, service, occupancy


def figures(trajectory: list[dict], metrics: list[dict], service: list[dict], occupancy: list[dict]) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/rsync-federation-v2-matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    FIGURES.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.axis("off")
    ax.annotate("Cluster A\n32 nodes\nlocal warm-up", (.13, .55), ha="center", va="center",
                bbox={"boxstyle": "round", "fc": "#dbeafe"})
    ax.annotate("Cluster B\n32 nodes\nlocal warm-up", (.87, .55), ha="center", va="center",
                bbox={"boxstyle": "round", "fc": "#dcfce7"})
    ax.annotate("", (.76, .55), (.24, .55), arrowprops={"arrowstyle": "<->", "lw": 2})
    ax.text(.5, .68, "$t_0$: enable node1–node33 and add two local neighbours", ha="center")
    ax.text(.5, .25, "pre: independent local requests     post: bidirectional remote requests + local controls", ha="center")
    fig.tight_layout(); fig.savefig(FIGURES / "federation_method.pdf"); fig.savefig(FIGURES / "federation_method.png", dpi=180); plt.close(fig)

    configs = {"repeated-nc": "NetworkCache", "repeated-known-owner": "Known-owner", "repeated-tcp-direct": "TCP Direct"}
    fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharey=True)
    for axis, cluster_name in zip(axes, ("A", "B")):
        for config, label in configs.items():
            pre = sorted((int(row["round"]), float(row["end_to_end_latency_ns_mean"]) / 1e6)
                         for row in trajectory if row["config"] == config and row["phase"] == "pre" and
                         row["direction"] == f"{cluster_name}_local" and row.get("end_to_end_latency_ns_mean") not in (None, ""))
            remote_direction = f"{cluster_name}_to_{'B' if cluster_name == 'A' else 'A'}"
            post = sorted((int(row["round"]) + 9, float(row["end_to_end_latency_ns_mean"]) / 1e6)
                          for row in trajectory if row["config"] == config and row["request_class"] == "remote" and
                          row["direction"] == remote_direction and row.get("end_to_end_latency_ns_mean") not in (None, ""))
            if pre: axis.plot(*zip(*pre), marker="o", label=f"{label}: local before $t_0$")
            if post: axis.plot(*zip(*post), marker="s", linestyle="--", label=f"{label}: remote after $t_0$")
        local_post = sorted((int(row["round"]) + 9, float(row["end_to_end_latency_ns_mean"]) / 1e6)
                            for row in trajectory if row["config"] == "repeated-nc" and
                            row["request_class"] == "local_control" and row["direction"] == f"{cluster_name}_local" and
                            row.get("end_to_end_latency_ns_mean") not in (None, ""))
        if local_post: axis.plot(*zip(*local_post), marker="^", color="black", label="NetworkCache: post local control")
        axis.axvline(9, color="grey", linestyle=":"); axis.set_title(f"Cluster {cluster_name} viewpoint"); axis.set_ylabel("Mean latency (ms)"); axis.grid(alpha=.25)
    axes[-1].set_xlabel("Round (1–8 before $t_0$; 10–17 after $t_0$)")
    handles, labels = axes[0].get_legend_handles_labels(); fig.legend(handles, labels, loc="outside lower center", ncol=3, fontsize=8)
    fig.tight_layout(rect=(0, .1, 1, 1)); fig.savefig(FIGURES / "cluster_timelines.pdf"); fig.savefig(FIGURES / "cluster_timelines.png", dpi=180); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for config, label in {**configs, "uniform-nc": "NetworkCache Uniform"}.items():
        rows = sorted((int(row["round"]), float(row["end_to_end_latency_ns_mean"]) / 1e6,
                       float(row["end_to_end_latency_ns_ci95_low"]) / 1e6,
                      float(row["end_to_end_latency_ns_ci95_high"]) / 1e6)
                      for row in trajectory if row["config"] == config and row["request_class"] == "remote" and row["direction"] == "both")
        if rows:
            x, y, low, high = zip(*rows); ax.plot(x, y, marker="o", label=label); ax.fill_between(x, low, high, alpha=.12)
    ax.set(xlabel="Post-connection round", ylabel="End-to-end latency (ms)"); ax.grid(alpha=.25); ax.legend(); fig.tight_layout()
    fig.savefig(FIGURES / "remote_trajectory.pdf"); fig.savefig(FIGURES / "remote_trajectory.png", dpi=180); plt.close(fig)

    first_metrics = (("activation_apply_latency_ns", "Activation apply"),
                     ("activation_to_first_remote_request_start_ns", "Ready to first request"),
                     ("activation_to_first_remote_data_ns", "Ready to first DATA"),
                     ("first_remote_request_latency_ns", "First request latency"),
                     ("time_to_first_cache_assisted_service_ns", "Ready to cache service"))
    first_rows = [row for row in metrics if row["direction"] == "both" and
                  row["metric"] in {name for name, _ in first_metrics}]
    fig, (ax, count_ax) = plt.subplots(1, 2, figsize=(11, 4.8), gridspec_kw={"width_ratios": [3, 1]})
    configs_in_first = sorted({row["config"] for row in first_rows})
    width = .8 / max(1, len(first_metrics))
    for metric_index, (metric, title) in enumerate(first_metrics):
        rows = [next((row for row in first_rows if row["config"] == config and row["metric"] == metric), None)
                for config in configs_in_first]
        positions = [index - .4 + width / 2 + metric_index * width for index in range(len(configs_in_first))]
        values = [float(row["mean"]) / 1e6 if row else 0 for row in rows]
        ax.bar(positions, values, width=width, label=title)
    ax.set_xticks(range(len(configs_in_first)), configs_in_first, rotation=20, ha="right")
    ax.set_ylabel("Time (ms)"); ax.legend(fontsize=7); ax.grid(axis="y", alpha=.25)
    cache_counts = [row for row in metrics if row["direction"] == "both" and
                    row["metric"] == "requests_to_first_cache_assisted_service"]
    count_ax.bar(range(len(cache_counts)), [float(row["mean"]) for row in cache_counts], color="#72b7b2")
    count_ax.set_xticks(range(len(cache_counts)), [row["config"] for row in cache_counts], rotation=20, ha="right")
    count_ax.set_ylabel("Requests to first cache service"); count_ax.grid(axis="y", alpha=.25); fig.tight_layout()
    fig.savefig(FIGURES / "first_remote.pdf"); fig.savefig(FIGURES / "first_remote.png", dpi=180); plt.close(fig)

    period_metrics = (("cold_remote_latency_ns", "First occurrence per key"),
                      ("early_remote_latency_ns", "Post rounds 1–2"),
                      ("late_remote_latency_ns", "Post rounds 7–8"),
                      ("cache_assisted_latency_ns", "Actually cache-served"))
    cold_rows = [row for row in metrics if row["direction"] == "both" and
                 row["metric"] in {name for name, _ in period_metrics}]
    labels = sorted({row["config"] for row in cold_rows}); width = .8 / len(period_metrics)
    fig, ax = plt.subplots(figsize=(8, 4.5)); x = list(range(len(labels)))
    for metric_index, (metric, title) in enumerate(period_metrics):
        offset = -.4 + width / 2 + metric_index * width
        values = [next((float(row["mean"]) / 1e6 for row in cold_rows if row["config"] == label and row["metric"] == metric), 0) for label in labels]
        ax.bar([value + offset for value in x], values, width=width, label=title)
    ax.set_xticks(x, labels, rotation=20, ha="right"); ax.set_ylabel("End-to-end latency (ms)"); ax.legend(); fig.tight_layout()
    fig.savefig(FIGURES / "cold_and_cache_assisted.pdf"); fig.savefig(FIGURES / "cold_and_cache_assisted.png", dpi=180); plt.close(fig)

    nc_service = [row for row in service if row["config"] == "repeated-nc" and row["direction"] == "both"]
    fig, ax1 = plt.subplots(figsize=(8, 4.5)); x = [int(row["round"]) for row in nc_service]
    ax1.plot(x, [float(row["cache_fraction_mean"]) for row in nc_service], marker="o", label="Cache-served fraction")
    ax1.set(xlabel="Post-connection round", ylabel="Cache-served fraction", ylim=(0, 1)); ax1.grid(alpha=.25)
    ax2 = ax1.twinx(); ax2.plot(x, [float(row["request_forward_events_mean"]) for row in nc_service], marker="s", color="#f58518", label="REQUEST events")
    ax2.set_ylabel("Mean REQUEST send/forward events"); fig.tight_layout(); fig.savefig(FIGURES / "request_provider.pdf"); fig.savefig(FIGURES / "request_provider.png", dpi=180); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for cluster_name in ("A", "B"):
        rows = [row for row in occupancy if row["config"] == "repeated-nc" and row["cluster"] == cluster_name]
        order = {"ready": 0, **{f"pre-round{i:02d}": i for i in range(1, 9)}, "pre-t0": 9, "post-t0": 10,
                 **{f"post-round{i:02d}": 10 + i for i in range(1, 9)}, "final": 19}
        rows.sort(key=lambda row: order.get(row["snapshot_id"], 99))
        ax.plot(range(len(rows)), [float(row["cached_copy_keys_mean"]) for row in rows], marker="o", label=f"Cluster {cluster_name}")
    ax.axvline(9.5, color="grey", linestyle=":", label="$t_0$"); ax.set(xlabel="Occupancy snapshot", ylabel="Cached object replicas"); ax.grid(alpha=.25); ax.legend(); fig.tight_layout()
    fig.savefig(FIGURES / "cache_occupancy.pdf"); fig.savefig(FIGURES / "cache_occupancy.png", dpi=180); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5)); labels = list(configs); x = list(range(len(labels)))
    before, after = [], []
    for config in labels:
        before.append(statistics.mean(float(row["end_to_end_latency_ns_mean"]) / 1e6 for row in trajectory
                                      if row["config"] == config and row["phase"] == "pre" and
                                      row["direction"] == "both" and int(row["round"]) in (7, 8)))
        after.append(statistics.mean(float(row["end_to_end_latency_ns_mean"]) / 1e6 for row in trajectory
                                     if row["config"] == config and row["request_class"] == "local_control" and
                                     row["direction"] == "both" and int(row["round"]) in (7, 8)))
    ax.bar([value - .18 for value in x], before, width=.36, label="Before $t_0$, rounds 7–8")
    ax.bar([value + .18 for value in x], after, width=.36, label="After $t_0$ local control, rounds 7–8")
    ax.set_xticks(x, [configs[label] for label in labels]); ax.set_ylabel("Local request latency (ms)"); ax.legend(); fig.tight_layout()
    fig.savefig(FIGURES / "local_stability.pdf"); fig.savefig(FIGURES / "local_stability.png", dpi=180); plt.close(fig)


def main() -> int:
    selection = read(RESULTS / "federation_selection.csv")
    if len(selection) != 80:
        raise SystemExit(f"federation-v2 requires 80 selected runs, found {len(selection)}")
    run_trajectory: list[dict] = []; run_metrics: list[dict] = []; run_service: list[dict] = []; run_occupancy: list[dict] = []
    for selected in selection:
        trajectory, metrics, service, occupancy = run_outputs(selected)
        run_trajectory += trajectory; run_metrics += metrics; run_service += service; run_occupancy += occupancy

    trajectory_metrics = ("end_to_end_latency_ns", "discovery_latency_ns", "return_path_latency_ns",
                          "request_forward_events", "request_frame_bytes", "serving_overlay_hops",
                          "cross_link_request_frames", "cross_link_data_frames")
    seed_trajectory = seed_aggregate(run_trajectory,
        ("config", "seed", "phase", "request_class", "round", "direction"), trajectory_metrics)
    overall_trajectory = overall_aggregate(seed_trajectory,
        ("config", "phase", "request_class", "round", "direction"), trajectory_metrics)

    metric_names = ("activation_apply_latency_ns", "activation_to_first_remote_request_start_ns",
                    "activation_to_first_remote_data_ns", "first_remote_request_latency_ns",
                    "cold_remote_latency_ns", "early_remote_latency_ns", "late_remote_latency_ns",
                    "cache_assisted_latency_ns", "requests_to_first_cache_assisted_service",
                    "time_to_first_cache_assisted_service_ns")
    seed_metrics = seed_aggregate(run_metrics, ("config", "seed", "direction"), metric_names)
    metric_rows = []
    for row in overall_aggregate(seed_metrics, ("config", "direction"), metric_names):
        for metric in metric_names:
            if row[f"{metric}_mean"] == "": continue
            metric_rows.append({"config": row["config"], "direction": row["direction"], "metric": metric,
                                "seed_count": row["seed_count"], "mean": row[f"{metric}_mean"],
                                "ci95_low": row[f"{metric}_ci95_low"], "ci95_high": row[f"{metric}_ci95_high"]})

    service_metrics = ("original_owner_fraction", "cache_fraction", "serving_overlay_hops", "request_forward_events")
    seed_service = seed_aggregate(run_service, ("config", "seed", "round", "direction"), service_metrics)
    overall_service = overall_aggregate(seed_service, ("config", "round", "direction"), service_metrics)
    occupancy_metrics = ("db_keys", "logical_value_bytes", "original_keys", "cached_copy_keys",
                         "cached_copy_logical_bytes", "redis_used_memory_bytes")
    seed_occupancy = seed_aggregate(run_occupancy, ("config", "seed", "snapshot_id", "phase", "round", "cluster"), occupancy_metrics)
    overall_occupancy = overall_aggregate(seed_occupancy, ("config", "snapshot_id", "phase", "round", "cluster"), occupancy_metrics)

    by_metric = {(row["config"], row["seed"], row["direction"]): row for row in seed_metrics}
    effects = []
    for left, right, label in (("repeated-known-owner", "repeated-nc", "known_owner_minus_nc"),
                               ("repeated-tcp-direct", "repeated-nc", "tcp_direct_minus_nc"),
                               ("uniform-nc", "repeated-nc", "uniform_minus_repeated")):
        for metric in ("first_remote_request_latency_ns", "late_remote_latency_ns"):
            values = [float(by_metric[(left, str(seed), "both")][metric]) -
                      float(by_metric[(right, str(seed), "both")][metric]) for seed in range(1, 11)]
            centre, low, high = ci(values)
            effects.append({"comparison": label, "metric": metric, "seed_count": 10,
                            "difference_mean": centre, "difference_ci95_low": low, "difference_ci95_high": high})
    for config in sorted({row["config"] for row in seed_metrics}):
        values = [float(by_metric[(config, str(seed), "A_to_B")]["late_remote_latency_ns"]) -
                  float(by_metric[(config, str(seed), "B_to_A")]["late_remote_latency_ns"]) for seed in range(1, 11)]
        centre, low, high = ci(values)
        effects.append({"comparison": f"{config}:A_to_B_minus_B_to_A", "metric": "late_remote_latency_ns",
                        "seed_count": 10, "difference_mean": centre,
                        "difference_ci95_low": low, "difference_ci95_high": high})
        transition = [float(by_metric[(config, str(seed), "both")]["late_remote_latency_ns"]) -
                      float(by_metric[(config, str(seed), "both")]["early_remote_latency_ns"]) for seed in range(1, 11)]
        centre, low, high = ci(transition)
        effects.append({"comparison": f"{config}:late_minus_early", "metric": "remote_latency_ns",
                        "seed_count": 10, "difference_mean": centre,
                        "difference_ci95_low": low, "difference_ci95_high": high})
        local_effects = []
        for seed in range(1, 11):
            before = [float(row["end_to_end_latency_ns"]) for row in seed_trajectory
                      if row["config"] == config and row["seed"] == str(seed) and row["phase"] == "pre" and
                      row["direction"] == "both" and int(row["round"]) in (7, 8)]
            after = [float(row["end_to_end_latency_ns"]) for row in seed_trajectory
                     if row["config"] == config and row["seed"] == str(seed) and
                     row["request_class"] == "local_control" and row["direction"] == "both" and
                     int(row["round"]) in (7, 8)]
            local_effects.append(statistics.mean(after) - statistics.mean(before))
        centre, low, high = ci(local_effects)
        effects.append({"comparison": f"{config}:post_local_minus_pre_steady", "metric": "local_latency_ns",
                        "seed_count": 10, "difference_mean": centre,
                        "difference_ci95_low": low, "difference_ci95_high": high})

    # Selection sensitivity uses every quality-qualified attempt and never latency-sorts runs.
    runner_spec = importlib.util.spec_from_file_location("federation_v2_runner", SCRIPT_DIR / "run_federation_v2_suite.py")
    runner = importlib.util.module_from_spec(runner_spec); sys.modules[runner_spec.name] = runner
    assert runner_spec.loader; runner_spec.loader.exec_module(runner)
    sensitivity = []
    for config in runner.CONFIGS:
        selected_values = {metric: statistics.mean(float(row[metric]) for row in seed_metrics
                           if row["config"] == config.slug and row["direction"] == "both")
                           for metric in ("early_remote_latency_ns", "late_remote_latency_ns")}
        for metric, rounds in (("early_remote_latency_ns", {"1", "2"}), ("late_remote_latency_ns", {"7", "8"})):
            all_seed_means = []
            for seed in range(1, 11):
                attempt_means = []
                for path in runner.dirs(config, seed):
                    if not runner.validate(config, path)[0]: continue
                    rows = [row for row in read(path / "request_results.csv") if row["phase"] == "post" and
                            row["request_class"] == "remote" and row["round"] in rounds]
                    attempt_means.append(statistics.mean(float(row["end_to_end_latency_ns"]) for row in rows))
                all_seed_means.append(statistics.mean(attempt_means))
            all_mean = statistics.mean(all_seed_means)
            relative = abs(all_mean - selected_values[metric]) / selected_values[metric]
            sensitivity.append({"config": config.slug, "metric": metric,
                                "selected_earliest_two_mean_ns": selected_values[metric],
                                "all_qualified_attempts_mean_ns": all_mean,
                                "relative_difference": relative, "within_5_percent": int(relative <= .05)})

    for target in (ANALYSIS, ASSETS):
        write(target / "trajectory.csv", overall_trajectory)
        write(target / "federation_metrics.csv", metric_rows)
        write(target / "paired_effects.csv", effects)
        write(target / "service_and_request_overhead.csv", overall_service)
        write(target / "cache_occupancy.csv", overall_occupancy)
        write(target / "sensitivity.csv", sensitivity)
    if any(row["within_5_percent"] == 0 for row in sensitivity):
        raise SystemExit("federation-v2 sensitivity gate failed")
    figures(overall_trajectory, metric_rows, overall_service, overall_occupancy)
    print(f"wrote federation-v2 analysis to {ANALYSIS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
