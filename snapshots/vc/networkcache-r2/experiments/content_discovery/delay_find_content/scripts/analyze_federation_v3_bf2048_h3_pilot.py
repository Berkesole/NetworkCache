#!/usr/bin/env python3
"""Create descriptive CSVs and a multi-page PDF for four matched BF pilots."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from run_federation_v3_bf2048_h3_suite import PILOTS, RESULTS, run_dir, validate


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
ASSETS = EXP / "paper_assets/federation-v3-bf2048-h3-pilot"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def percent_change(old: float, new: float) -> float:
    return abs(new - old) / old * 100 if old else (0.0 if new == 0 else math.inf)


def summarize() -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    bins, progress, occupancy, bloom, decisions = [], [], [], [], []
    for pilot in PILOTS:
        root = run_dir(pilot)
        ok, reason = validate(pilot, root)
        if not ok:
            raise RuntimeError(f"{pilot.slug} is not READY: {reason}")
        trace = read_csv(root / "trace_breakdown.csv")
        grouped: dict[tuple[str, int, str], list[dict[str, str]]] = defaultdict(list)
        for row in trace:
            if row.get("trace_complete") == "1":
                grouped[(row["phase"], int(row["bin"]), row["direction"])].append(row)
        for (phase, bin_index, direction), rows in sorted(grouped.items()):
            keys = [row["key"] for row in rows]
            bins.append({"config": pilot.slug, "catalog_size_per_cluster": pilot.catalog_size,
                         "distribution": pilot.distribution, "phase": phase, "bin": bin_index,
                         "direction": direction, "request_class": rows[0]["request_class"],
                         "usable_requests": len(rows),
                         "mean_end_to_end_ms": mean(int(row["end_to_end_latency_ns"]) for row in rows) / 1e6,
                         "mean_discovery_ms": mean(int(row["discovery_latency_ns"]) for row in rows) / 1e6,
                         "mean_return_path_ms": mean(int(row["return_path_latency_ns"]) for row in rows) / 1e6,
                         "cache_served_fraction": mean(row["service_class"] == "cache" for row in rows),
                         "mean_request_forward_events": mean(int(row["request_forward_events"]) for row in rows),
                         "mean_correct_provider_hit_events": mean(int(row["correct_provider_hit_events"]) for row in rows),
                         "mean_unrelated_provider_hit_events": mean(int(row["unrelated_provider_hit_events"]) for row in rows),
                         "mean_unrelated_data_handle_events": mean(int(row["unrelated_data_handle_events"]) for row in rows),
                         "mean_unrelated_data_bytes": mean(int(row["unrelated_data_bytes"]) for row in rows),
                         "mean_serving_overlay_hops": mean(int(row["serving_overlay_hops"]) for row in rows),
                         "unique_keys_in_bin": len(set(keys)), "repeat_fraction_in_bin": 1 - len(set(keys)) / len(keys)})
        remote = sorted((row for row in trace if row["phase"] == "post" and row["request_class"] == "remote"),
                        key=lambda row: int(row["request_index"]))
        seen: set[str] = set()
        for index, row in enumerate(remote, 1):
            was_seen = row["key"] in seen; seen.add(row["key"])
            progress.append({"config": pilot.slug, "remote_request_number": index, "bin": row["bin"],
                             "key": row["key"], "cumulative_unique_keys": len(seen),
                             "cumulative_repeat_fraction": 1 - len(seen) / index,
                             "key_seen_before": int(was_seen), "service_class": row["service_class"],
                             "end_to_end_ms": int(row["end_to_end_latency_ns"]) / 1e6})
        occ_group: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        for row in read_csv(root / "cache_occupancy.csv"):
            occ_group[(row["snapshot_id"], row["phase"], row["cluster"])].append(row)
        for (snapshot_id, phase, cluster), rows in occ_group.items():
            occupancy.append({"config": pilot.slug, "snapshot_id": snapshot_id, "phase": phase,
                              "cluster": cluster, "cached_copy_keys": sum(int(row["cached_copy_keys"]) for row in rows),
                              "cached_copy_logical_bytes": sum(int(row["cached_copy_logical_bytes"]) for row in rows),
                              "original_keys": sum(int(row["original_keys"]) for row in rows),
                              "redis_used_memory_bytes": sum(int(row["redis_used_memory_bytes"]) for row in rows)})
        state_group: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for row in read_csv(root / "sync_bloom_state.csv"):
            if row.get("neighbor") != "0": state_group[(row["snapshot_id"], row["phase"])].append(row)
        for (snapshot_id, phase), rows in state_group.items():
            values = [float(row["occupancy"]) for row in rows]
            bloom.append({"config": pilot.slug, "snapshot_id": snapshot_id, "phase": phase,
                          "neighbor_filters": len(values), "mean_bloom_occupancy": mean(values),
                          "max_bloom_occupancy": max(values)})

        def select(phase: str, directions: set[str], start: int, end: int) -> list[dict]:
            return [row for row in bins if row["config"] == pilot.slug and row["phase"] == phase
                    and row["direction"] in directions and start <= int(row["bin"]) <= end]

        for cluster in ("A", "B"):
            first = select("pre", {f"{cluster}_local"}, 5, 6)
            last = select("pre", {f"{cluster}_local"}, 7, 8)
            latency_change = percent_change(mean(row["mean_end_to_end_ms"] for row in first),
                                            mean(row["mean_end_to_end_ms"] for row in last))
            cache_pp = abs(mean(row["cache_served_fraction"] for row in last) -
                           mean(row["cache_served_fraction"] for row in first)) * 100
            decisions.append({"config": pilot.slug, "test": f"pre_{cluster}",
                              "latency_relative_change_percent": latency_change,
                              "cache_fraction_change_percentage_points": cache_pp,
                              "request_forward_change_percent": "",
                              "stable": int(latency_change <= 10 and cache_pp <= 5),
                              "consequence": "extend pre to 2048 in formal design" if latency_change > 10 or cache_pp > 5 else "pre length adequate"})
        first = select("post", {"A_to_B", "B_to_A"}, 13, 14)
        last = select("post", {"A_to_B", "B_to_A"}, 15, 16)
        latency_change = percent_change(mean(row["mean_end_to_end_ms"] for row in first),
                                        mean(row["mean_end_to_end_ms"] for row in last))
        cache_pp = abs(mean(row["cache_served_fraction"] for row in last) -
                       mean(row["cache_served_fraction"] for row in first)) * 100
        forwarding_change = percent_change(mean(row["mean_request_forward_events"] for row in first),
                                           mean(row["mean_request_forward_events"] for row in last))
        stable = latency_change <= 10 and cache_pp <= 5 and forwarding_change <= 10
        consequence = ("extend remote to 4096 in formal design" if pilot.slug == "catalog1000-zipf" and not stable
                       else "descriptive result; no automatic extension" if pilot.distribution == "uniform" and not stable
                       else "post length adequate" if stable else "review before formal design")
        decisions.append({"config": pilot.slug, "test": "post_remote",
                          "latency_relative_change_percent": latency_change,
                          "cache_fraction_change_percentage_points": cache_pp,
                          "request_forward_change_percent": forwarding_change,
                          "stable": int(stable), "consequence": consequence})
    return bins, progress, occupancy, bloom, decisions


def plot(bins: list[dict], progress: list[dict], occupancy: list[dict], bloom: list[dict], output: Path) -> None:
    colors = {pilot.slug: color for pilot, color in zip(PILOTS, ("#0072B2", "#56B4E9", "#D55E00", "#009E73"))}
    with PdfPages(output) as pdf:
        fig, axes = plt.subplots(2, 2, figsize=(11, 8.5), constrained_layout=True)
        for pilot in PILOTS:
            pre = [row for row in bins if row["config"] == pilot.slug and row["phase"] == "pre"]
            post = [row for row in bins if row["config"] == pilot.slug and row["request_class"] == "remote"]
            for cluster, style in (("A_local", "-"), ("B_local", "--")):
                rows = [row for row in pre if row["direction"] == cluster]
                axes[0, 0].plot([row["bin"] for row in rows], [row["mean_end_to_end_ms"] for row in rows],
                                style, color=colors[pilot.slug], alpha=.8, label=f"{pilot.slug} {cluster[0]}")
            combined = defaultdict(list)
            for row in post: combined[int(row["bin"])].append(row)
            axes[0, 1].plot(sorted(combined), [mean(x["mean_end_to_end_ms"] for x in combined[b]) for b in sorted(combined)],
                            color=colors[pilot.slug], label=pilot.slug)
            axes[1, 0].plot(sorted(combined), [mean(x["cache_served_fraction"] for x in combined[b]) for b in sorted(combined)],
                            color=colors[pilot.slug], label=pilot.slug)
            axes[1, 1].plot(sorted(combined), [mean(x["mean_request_forward_events"] for x in combined[b]) for b in sorted(combined)],
                            color=colors[pilot.slug], label=pilot.slug)
        for ax, title, ylabel in zip(axes.flat,
            ("Pre-link local latency", "Post-link remote latency", "Remote cache-served fraction", "Remote REQUEST forwarding events"),
            ("End-to-end latency (ms)", "End-to-end latency (ms)", "Fraction", "Events / application request")):
            ax.set(title=title, xlabel="128-request bin", ylabel=ylabel); ax.grid(alpha=.25); ax.legend(fontsize=7)
        pdf.savefig(fig); plt.close(fig)

        fig, axes = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True)
        for pilot in PILOTS:
            rows = [row for row in progress if row["config"] == pilot.slug]
            axes[0].plot([row["remote_request_number"] for row in rows], [row["cumulative_unique_keys"] for row in rows],
                         color=colors[pilot.slug], label=pilot.slug)
            axes[1].plot([row["remote_request_number"] for row in rows], [row["cumulative_repeat_fraction"] for row in rows],
                         color=colors[pilot.slug], label=pilot.slug)
        axes[0].set(title="Cumulative distinct remote keys", xlabel="Remote request number", ylabel="Distinct keys")
        axes[1].set(title="Observed repetition", xlabel="Remote request number", ylabel="Cumulative repeat fraction")
        for ax in axes: ax.grid(alpha=.25); ax.legend(fontsize=8)
        pdf.savefig(fig); plt.close(fig)

        order = ["ready", "pre-bin02", "pre-bin04", "pre-bin06", "pre-bin08", "pre-t0", "post-t0",
                 "post-bin01", "post-bin02", "post-bin04", "post-bin06", "post-bin08", "post-bin10",
                 "post-bin12", "post-bin14", "post-bin16", "final"]
        fig, axes = plt.subplots(2, 1, figsize=(11, 8.5), constrained_layout=True)
        for pilot in PILOTS:
            rows = [row for row in occupancy if row["config"] == pilot.slug]
            totals = defaultdict(int)
            for row in rows: totals[row["snapshot_id"]] += int(row["cached_copy_keys"])
            axes[0].plot(range(len(order)), [totals[x] for x in order], color=colors[pilot.slug], label=pilot.slug)
            states = {row["snapshot_id"]: float(row["mean_bloom_occupancy"]) for row in bloom if row["config"] == pilot.slug}
            axes[1].plot(range(len(order)), [states.get(x, math.nan) for x in order], color=colors[pilot.slug], label=pilot.slug)
        for ax, title, ylabel in ((axes[0], "Cached copies across both clusters", "Cached copy count"),
                                  (axes[1], "Mean SyncTable Bloom occupancy", "Set-bit fraction")):
            ax.set(title=title, ylabel=ylabel, xticks=range(len(order)), xticklabels=order)
            ax.tick_params(axis="x", rotation=45, labelsize=7); ax.grid(alpha=.25); ax.legend(fontsize=8)
        pdf.savefig(fig); plt.close(fig)

        fig, axes = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True)
        for pilot in PILOTS:
            rows = [row for row in bins if row["config"] == pilot.slug and row["request_class"] == "remote"]
            grouped = defaultdict(list)
            for row in rows: grouped[int(row["bin"])].append(row)
            order_bins = sorted(grouped)
            axes[0].plot(order_bins,
                         [mean(x["mean_unrelated_provider_hit_events"] for x in grouped[b]) for b in order_bins],
                         color=colors[pilot.slug], label=pilot.slug)
            axes[1].plot(order_bins,
                         [mean(x["mean_unrelated_data_bytes"] for x in grouped[b]) / 1024 for b in order_bins],
                         color=colors[pilot.slug], label=pilot.slug)
        axes[0].set(title="Unrelated provider hits", xlabel="Post remote bin", ylabel="Events / request")
        axes[1].set(title="Unrelated DATA received", xlabel="Post remote bin", ylabel="KiB / request")
        for ax in axes: ax.grid(alpha=.25); ax.legend(fontsize=8)
        pdf.savefig(fig); plt.close(fig)


def main() -> int:
    bins, progress, occupancy, bloom, decisions = summarize()
    ASSETS.mkdir(parents=True, exist_ok=True)
    write_csv(ASSETS / "pilot_bin_summary.csv", bins)
    write_csv(ASSETS / "pilot_key_progress.csv", progress)
    write_csv(ASSETS / "pilot_cache_occupancy.csv", occupancy)
    write_csv(ASSETS / "pilot_bloom_occupancy.csv", bloom)
    write_csv(ASSETS / "pilot_warmup_assessment.csv", decisions)
    plot(bins, progress, occupancy, bloom, ASSETS / "pilot_trends.pdf")
    print(f"wrote exploratory pilot outputs to {ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
