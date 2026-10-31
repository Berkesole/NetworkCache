#!/usr/bin/env python3
"""Aggregate federation-v4 runs and render the continuous A/B timeline."""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

os.environ.setdefault("MPLCONFIGDIR", "/tmp/rsync-federation-v4-mpl")
import matplotlib.pyplot as plt

# Keep the bilingual static assets self-contained and renderable on the
# experiment host.  DejaVu Sans has no CJK glyphs, while this font is part of
# the frozen analysis environment.
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


EXP = Path(__file__).resolve().parent.parent
SUITE = "federation-v4-bf1024-h3-formal"
RESULTS = EXP / "results" / f"{SUITE}-r2"
ANALYSIS = RESULTS / "analysis"
ASSETS = EXP / "paper_assets" / "networkcache-bf1024-h3-formal-r2" / "federation"
T95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
       7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}


def read_csv(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.exists():
        return []
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


def clean_json(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    return value


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(clean_json(payload), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def number(row: Mapping[str, str], field: str) -> float:
    try:
        return float(row[field])
    except (KeyError, TypeError, ValueError):
        return math.nan


def average(values: Iterable[float]) -> float:
    finite = [value for value in values if not math.isnan(value)]
    return statistics.fmean(finite) if finite else math.nan


def ci95(values: Sequence[float]) -> tuple[float, float, float]:
    finite = [value for value in values if not math.isnan(value)]
    center = average(finite)
    if len(finite) < 2:
        return center, math.nan, math.nan
    half = T95.get(len(finite), 1.96) * statistics.stdev(finite) / math.sqrt(len(finite))
    return center, center - half, center + half


def phase_position(row: Mapping[str, str]) -> int:
    batch = int(row["bin"])
    return batch - 9 if row["phase"] == "pre" else batch


def request_period(row: Mapping[str, str]) -> str:
    phase, batch = row["phase"], int(row["bin"])
    if phase == "pre":
        return "pre_early" if batch <= 2 else "pre_steady" if batch >= 7 else "pre_middle"
    return "post_early" if batch <= 2 else "post_late" if batch >= 15 else "post_middle"


def summarize(selection: list[dict[str, str]]) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    timeline_groups: dict[tuple, list[dict]] = defaultdict(list)
    period_groups: dict[tuple, list[dict]] = defaultdict(list)
    first_access = []
    occupancy = []
    bloom_state = []
    for selected in selection:
        config = selected["config"]
        seed = int(selected["seed"])
        repetition = int(selected["selection_index"])
        run = Path(selected["run_dir"])
        method = selected["method"]
        requests = read_csv(run / "request_results.csv")
        trace_lookup = {row["request_index"]: row for row in read_csv(run / "trace_breakdown.csv")}
        joined = []
        for row in requests:
            item = dict(row)
            item.update({f"trace_{key}": value for key, value in trace_lookup.get(row["request_index"], {}).items()})
            joined.append(item)
            key = (config, seed, repetition, phase_position(row), row["direction"], row["request_class"])
            timeline_groups[key].append(item)
            period_groups[(config, seed, repetition, request_period(row), row["direction"], row["request_class"])].append(item)

        activation = json.loads((run / "activation.json").read_text())
        ready = int(activation["federation_ready_ns"])
        remote = [row for row in joined if row["request_class"] == "remote"]
        for direction in ("A_to_B", "B_to_A"):
            directional = sorted((row for row in remote if row["direction"] == direction),
                                 key=lambda row: int(row["request_index"]))
            first = directional[0]
            cache_rows = [row for row in directional
                          if row.get("trace_service_class") == "cache"]
            cache_first = cache_rows[0] if cache_rows else None
            first_access.append({
                "config": config, "seed": seed, "repetition": repetition,
                "method": method, "direction": direction,
                "activation_apply_latency_ns": int(activation["activation_apply_latency_ns"]),
                "activation_to_first_remote_data_ns": int(first["app_receive_mono_ns"]) - ready,
                "first_remote_request_latency_ns": int(first["end_to_end_latency_ns"]),
                "activation_to_first_cache_assisted_proxy_ns":
                    int(cache_first["app_receive_mono_ns"]) - ready if cache_first else math.nan,
                "requests_to_first_cache_assisted_proxy":
                    directional.index(cache_first) + 1 if cache_first else math.nan,
                "provider_attribution_scope": "first-observed matching provider_hit proxy",
            })

        occupancy_groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        for row in read_csv(run / "cache_occupancy.csv"):
            occupancy_groups[(row["snapshot_id"], row["phase"], row.get("cluster", ""))].append(row)
        for (snapshot, phase, cluster), members in occupancy_groups.items():
            occupancy.append({
                "config": config, "seed": seed, "repetition": repetition,
                "snapshot_id": snapshot, "phase": phase, "cluster": cluster,
                "cached_copy_keys_mean": sum(number(row, "cached_copy_keys") for row in members),
                "cached_copy_logical_bytes_mean": sum(
                    number(row, "cached_copy_logical_bytes") for row in members),
                "redis_used_memory_bytes_mean": sum(
                    number(row, "redis_used_memory_bytes") for row in members),
            })
        bloom_groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        for row in read_csv(run / "sync_bloom_state.csv"):
            cluster = "A" if int(row["node"]) <= 32 else "B"
            bloom_groups[(row["snapshot_id"], row["phase"], cluster)].append(row)
        for (snapshot, phase, cluster), members in bloom_groups.items():
            bloom_state.append({
                "config": config, "seed": seed, "repetition": repetition,
                "snapshot_id": snapshot, "phase": phase, "cluster": cluster,
                "bloom_occupancy_mean": average(number(row, "occupancy") for row in members),
                "bloom_occupancy_max_mean": max(number(row, "occupancy") for row in members),
            })

    def summarize_group(key: tuple, rows: list[dict], timeline: bool) -> dict:
        config, seed, repetition, group, direction, request_class = key
        result = {"config": config, "seed": seed, "repetition": repetition,
                  "direction": direction, "request_class": request_class,
                  "usable_rows": len(rows)}
        result["phase_position" if timeline else "period"] = group
        result["end_to_end_latency_ns_mean"] = average(number(row, "end_to_end_latency_ns") for row in rows)
        result["discovery_latency_proxy_ns_mean"] = average(
            number(row, "trace_discovery_latency_ns") for row in rows)
        result["return_path_latency_proxy_ns_mean"] = average(
            number(row, "trace_return_path_latency_ns") for row in rows)
        result["fallback_triggered_fraction"] = average(
            number(row, "trace_fallback_triggered") for row in rows)
        result["preferred_logical_request_events_mean"] = average(
            number(row, "trace_normal_request_frames") for row in rows)
        result["fallback_logical_request_events_mean"] = average(
            number(row, "trace_fallback_request_frames") for row in rows)
        result["request_forward_events_mean"] = average(
            number(row, "trace_request_forward_events") for row in rows)
        result["cross_link_request_frames_mean"] = average(
            number(row, "trace_cross_link_request_frames") for row in rows)
        result["cross_link_data_frames_mean"] = average(
            number(row, "trace_cross_link_data_frames") for row in rows)
        result["cross_link_request_bytes_mean"] = average(
            number(row, "trace_cross_link_request_bytes") for row in rows)
        result["cross_link_data_bytes_mean"] = average(
            number(row, "trace_cross_link_data_bytes") for row in rows)
        result["serving_overlay_hops_proxy_mean"] = average(
            number(row, "trace_serving_overlay_hops") for row in rows)
        result["unrelated_data_bytes_mean"] = average(
            number(row, "trace_unrelated_data_bytes") for row in rows)
        result["first_observed_cache_fraction_proxy"] = average(
            row.get("trace_service_class") == "cache" for row in rows
            if row.get("trace_service_class") not in (None, ""))
        return result

    timeline = [summarize_group(key, rows, True) for key, rows in sorted(timeline_groups.items())]
    periods = [summarize_group(key, rows, False) for key, rows in sorted(period_groups.items())]
    return timeline, periods, first_access, occupancy, bloom_state


def aggregate_repetitions(rows: list[dict], group_fields: tuple[str, ...]) -> list[dict]:
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row[field] for field in ("config", "seed", *group_fields))].append(row)
    output = []
    metrics = [field for field in rows[0] if field.endswith("_mean") or field.endswith("_fraction") or field.endswith("_proxy")]
    for key, members in sorted(buckets.items()):
        item = {field: value for field, value in zip(("config", "seed", *group_fields), key)}
        item["repetitions"] = len(members)
        for metric in metrics:
            item[metric] = average(float(row[metric]) for row in members)
        output.append(item)
    return output


def aggregate_seeds(rows: list[dict], group_fields: tuple[str, ...]) -> list[dict]:
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row[field] for field in ("config", *group_fields))].append(row)
    output = []
    metrics = [field for field in rows[0] if field.endswith("_mean") or field.endswith("_fraction") or field.endswith("_proxy")]
    for key, members in sorted(buckets.items()):
        item = {field: value for field, value in zip(("config", *group_fields), key)}
        item["seed_count"] = len(members)
        for metric in metrics:
            center, low, high = ci95([float(row[metric]) for row in members])
            item[metric] = center
            item[f"{metric}_ci95_low"] = low
            item[f"{metric}_ci95_high"] = high
        output.append(item)
    return output


def aggregate_first_access(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    seed_buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        seed_buckets[(row["config"], row["seed"], row["direction"])].append(row)
    metrics = ("activation_apply_latency_ns", "activation_to_first_remote_data_ns",
               "first_remote_request_latency_ns", "activation_to_first_cache_assisted_proxy_ns",
               "requests_to_first_cache_assisted_proxy")
    seeds = []
    for key, members in sorted(seed_buckets.items()):
        item = {"config": key[0], "seed": key[1], "direction": key[2],
                "repetitions": len(members)}
        for metric in metrics:
            item[metric] = average(float(row[metric]) for row in members)
        seeds.append(item)
    overall_buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in seeds:
        overall_buckets[(row["config"], row["direction"])].append(row)
    overall = []
    for key, members in sorted(overall_buckets.items()):
        item = {"config": key[0], "direction": key[1], "seed_count": len(members)}
        for metric in metrics:
            center, low, high = ci95([float(row[metric]) for row in members])
            item[metric] = center; item[f"{metric}_ci95_low"] = low; item[f"{metric}_ci95_high"] = high
        overall.append(item)
    return seeds, overall


def paired_effects(seed_periods: list[dict]) -> tuple[list[dict], list[dict]]:
    """Compute predefined effects within each deterministic seed before CI."""
    lookup: dict[tuple[str, int, str, str], list[dict]] = defaultdict(list)
    for row in seed_periods:
        lookup[(row["config"], int(row["seed"]), row["period"], row["request_class"])].append(row)

    def value(config: str, seed: int, period: str, request_class: str, metric: str) -> float:
        return average(float(row[metric]) for row in lookup.get(
            (config, seed, period, request_class), []))

    comparisons = (
        ("nc_vs_known_owner_512_zipf_late", "catalog512-zipf-nc",
         "catalog512-zipf-known-owner", "post_late", "remote"),
        ("nc_vs_tcp_direct_512_zipf_late", "catalog512-zipf-nc",
         "catalog512-zipf-tcp-direct", "post_late", "remote"),
        ("zipf_vs_uniform_512_late", "catalog512-zipf-nc",
         "catalog512-uniform-nc", "post_late", "remote"),
        ("zipf_vs_uniform_1000_late", "catalog1000-zipf-nc",
         "catalog1000-uniform-nc", "post_late", "remote"),
        ("catalog_512_vs_1000_zipf_late", "catalog512-zipf-nc",
         "catalog1000-zipf-nc", "post_late", "remote"),
    )
    seed_rows = []
    latency_metric = "end_to_end_latency_ns_mean"
    for label, treatment, reference, period, request_class in comparisons:
        for seed in range(1, 11):
            left = value(treatment, seed, period, request_class, latency_metric)
            right = value(reference, seed, period, request_class, latency_metric)
            seed_rows.append({
                "comparison": label, "seed": seed,
                "treatment_config": treatment, "reference_config": reference,
                "period": period, "request_class": request_class,
                "paired_latency_difference_ns": left - right,
                "paired_latency_ratio": left / right if right else math.nan,
            })
    for config in sorted({row["config"] for row in seed_periods}):
        for seed in range(1, 11):
            before = value(config, seed, "pre_steady", "local", latency_metric)
            after = value(config, seed, "post_late", "local_control", latency_metric)
            seed_rows.append({
                "comparison": "post_federation_local_vs_pre_steady",
                "seed": seed, "treatment_config": config, "reference_config": config,
                "period": "post_late_minus_pre_steady", "request_class": "local",
                "paired_latency_difference_ns": after - before,
                "paired_latency_ratio": after / before if before else math.nan,
            })
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in seed_rows:
        buckets[(row["comparison"], row["treatment_config"], row["reference_config"],
                 row["period"], row["request_class"])].append(row)
    overall = []
    for key, members in sorted(buckets.items()):
        item = {field: value for field, value in zip(
            ("comparison", "treatment_config", "reference_config", "period", "request_class"), key)}
        item["seed_count"] = len(members)
        for metric in ("paired_latency_difference_ns", "paired_latency_ratio"):
            center, low, high = ci95([float(row[metric]) for row in members])
            item[metric] = center
            item[f"{metric}_ci95_low"] = low
            item[f"{metric}_ci95_high"] = high
        overall.append(item)
    return seed_rows, overall


def plot_continuous(rows: list[dict], language: str, output: Path) -> None:
    config = "catalog512-zipf-nc"
    chosen = [row for row in rows if row["config"] == config]
    if not chosen:
        raise RuntimeError(f"missing continuous timeline rows for {config}")
    labels = {
        "en": {"x": "Request batch relative to federation activation",
               "y": "End-to-end request latency (ms)", "local": "Local access",
               "remote": "Remote access", "a": "Cluster A perspective",
               "b": "Cluster B perspective", "pre": "Independent operation",
               "post": "Federated operation"},
        "zh": {"x": "相对于联邦激活时刻的请求批次", "y": "端到端请求延迟（ms）",
               "local": "本地访问", "remote": "远端访问", "a": "集群A视角",
               "b": "集群B视角", "pre": "独立运行", "post": "联邦运行"},
    }[language]
    colors = {"local": "#0072B2", "remote": "#E69F00"}
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True, constrained_layout=True)
    for axis, cluster in zip(axes, ("A", "B")):
        local_direction = f"{cluster}_local"
        remote_direction = "A_to_B" if cluster == "A" else "B_to_A"
        for direction, request_class, style, role in (
                (local_direction, None, "-", "local"),
                (remote_direction, "remote", "--", "remote")):
            series = [row for row in chosen if row["direction"] == direction and
                      (request_class is None or row["request_class"] == request_class)]
            series.sort(key=lambda row: int(row["phase_position"]))
            x = [int(row["phase_position"]) for row in series]
            y = [float(row["end_to_end_latency_ns_mean"]) / 1e6 for row in series]
            low = [float(row["end_to_end_latency_ns_mean_ci95_low"]) / 1e6 for row in series]
            high = [float(row["end_to_end_latency_ns_mean_ci95_high"]) / 1e6 for row in series]
            axis.plot(x, y, style, color=colors[role], linewidth=2.2, marker="o", markersize=3.5,
                      label=labels[role])
            axis.fill_between(x, low, high, color=colors[role], alpha=.12)
        axis.axvspan(-.35, .35, color="#B8BCC4", alpha=.28)
        axis.axvline(0, color="#666666", linewidth=1)
        axis.text(0, .98, r"$t_0$", transform=axis.get_xaxis_transform(), ha="center", va="top")
        axis.axvspan(-8.5, -.35, color="#0072B2", alpha=.035)
        axis.axvspan(.35, 16.5, color="#009E73", alpha=.035)
        axis.set_xlim(-8.5, 16.5)
        axis.set_title(labels[cluster.lower()])
        axis.set_xlabel(labels["x"])
        axis.grid(axis="y", color="#D9DEE7", linewidth=.7)
        axis.legend(frameon=False, loc="upper right")
    axes[0].set_ylabel(labels["y"])
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    selection = read_csv(RESULTS / "federation_selection.csv")
    if len(selection) != 120:
        raise SystemExit(f"federation-v4 requires 120 selected runs, found {len(selection)}")
    config_counts = Counter(row["config"] for row in selection)
    seed_counts = Counter(int(row["seed"]) for row in selection)
    slots = {(row["config"], int(row["seed"]), int(row["selection_index"]))
             for row in selection}
    if len(config_counts) != 6 or set(config_counts.values()) != {20}:
        raise SystemExit(f"federation-v4 config matrix is incomplete: {dict(config_counts)}")
    if seed_counts != Counter({seed: 12 for seed in range(1, 11)}):
        raise SystemExit(f"federation-v4 seed matrix is incomplete: {dict(seed_counts)}")
    if len(slots) != 120:
        raise SystemExit("federation-v4 selection contains duplicate slots")
    if any(row.get("evidence_level") != "FORMAL_R2" or
           row.get("validation_reason") not in
           ("ok", "ok_expected_isolation_expiries",
            "ok_expected_tcp_direct_failed_completions")
           for row in selection):
        raise SystemExit("federation-v4 selection contains non-formal or unvalidated rows")
    if any(not Path(row["run_dir"]).is_dir() for row in selection):
        raise SystemExit("federation-v4 selection contains a missing local run directory")
    timeline, periods, first_access, occupancy, bloom_state = summarize(selection)
    seed_timeline = aggregate_repetitions(timeline, ("phase_position", "direction", "request_class"))
    overall_timeline = aggregate_seeds(seed_timeline, ("phase_position", "direction", "request_class"))
    seed_periods = aggregate_repetitions(periods, ("period", "direction", "request_class"))
    overall_periods = aggregate_seeds(seed_periods, ("period", "direction", "request_class"))
    seed_first, overall_first = aggregate_first_access(first_access)
    seed_paired, overall_paired = paired_effects(seed_periods)
    seed_occupancy = aggregate_repetitions(occupancy, ("snapshot_id", "phase", "cluster"))
    overall_occupancy = aggregate_seeds(seed_occupancy, ("snapshot_id", "phase", "cluster"))
    seed_bloom = aggregate_repetitions(bloom_state, ("snapshot_id", "phase", "cluster"))
    overall_bloom = aggregate_seeds(seed_bloom, ("snapshot_id", "phase", "cluster"))
    outputs = {
        "run_timeline_means.csv": timeline,
        "seed_timeline_means.csv": seed_timeline,
        "overall_timeline_mean_ci95.csv": overall_timeline,
        "run_period_means.csv": periods,
        "seed_period_means.csv": seed_periods,
        "overall_period_mean_ci95.csv": overall_periods,
        "run_first_access.csv": first_access,
        "seed_first_access.csv": seed_first,
        "overall_first_access_ci95.csv": overall_first,
        "seed_paired_effects.csv": seed_paired,
        "overall_paired_effects_ci95.csv": overall_paired,
        "run_cache_occupancy.csv": occupancy,
        "seed_cache_occupancy.csv": seed_occupancy,
        "overall_cache_occupancy_ci95.csv": overall_occupancy,
        "run_bloom_state.csv": bloom_state,
        "seed_bloom_state.csv": seed_bloom,
        "overall_bloom_state_ci95.csv": overall_bloom,
    }
    for filename, rows in outputs.items():
        write_csv(ANALYSIS / filename, rows)
        if filename.startswith("overall_"):
            write_csv(ASSETS / filename, rows)
    result_payload = {
        "schema_version": 1,
        "family": "federation-v4",
        "status": "READY",
        "evidence_level": "FORMAL_R2",
        "campaign_fingerprint": selection[0]["campaign_fingerprint"],
        "runtime_image_id": selection[0]["runtime_image_id"],
        "selected_runs": len(selection),
        "config_count": len(config_counts),
        "seed_count": len(seed_counts),
        "repetitions_per_seed": 2,
        "formal_requests_per_run": 3584,
        "formal_requests_total": len(selection) * 3584,
        "statistical_hierarchy": [
            "request",
            "run batch/period mean",
            "repetition-averaged seed mean",
            "10 seed means with 95% t CI",
        ],
        "overall_timeline": overall_timeline,
        "overall_periods": overall_periods,
        "overall_first_access": overall_first,
        "overall_paired_effects": overall_paired,
        "overall_cache_occupancy": overall_occupancy,
        "overall_bloom_state": overall_bloom,
    }
    write_json(ANALYSIS / "formal_results.json", result_payload)
    write_json(ASSETS / "formal_results.json", result_payload)
    plot_continuous(overall_timeline, "en", ANALYSIS / "federation_continuous_timeline.en.png")
    plot_continuous(overall_timeline, "zh", ANALYSIS / "federation_continuous_timeline.zh.png")
    plot_continuous(overall_timeline, "en", ASSETS / "federation_continuous_timeline.en.png")
    plot_continuous(overall_timeline, "zh", ASSETS / "federation_continuous_timeline.zh.png")
    plot_continuous(overall_timeline, "en", ASSETS / "federation_continuous_timeline.en.svg")
    plot_continuous(overall_timeline, "zh", ASSETS / "federation_continuous_timeline.zh.svg")
    output_hashes = {
        filename: sha256_file(ANALYSIS / filename)
        for filename in outputs
    }
    output_hashes["formal_results.json"] = sha256_file(ANALYSIS / "formal_results.json")
    output_hashes["federation_continuous_timeline.en.png"] = sha256_file(
        ANALYSIS / "federation_continuous_timeline.en.png")
    output_hashes["federation_continuous_timeline.zh.png"] = sha256_file(
        ANALYSIS / "federation_continuous_timeline.zh.png")
    write_json(ANALYSIS / "analysis_manifest.json", {
        "schema_version": 1,
        "family": "federation-v4",
        "status": "READY",
        "evidence_level": "FORMAL_R2",
        "selection_sha256": sha256_file(RESULTS / "federation_selection.csv"),
        "selected_runs": 120,
        "output_sha256": output_hashes,
        "pdf_generated": False,
        "slides_updated": False,
    })
    print(f"wrote federation-v4 analysis to {ANALYSIS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
