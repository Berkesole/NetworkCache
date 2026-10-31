#!/usr/bin/env python3
"""Create descriptive, scope-explicit outputs for the staged-fallback pilots."""

from __future__ import annotations

import bisect
import csv
import json
import math
import os
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

import analyze_federation_v3_bf2048_h3_pilot as base
import analyze_federation_v3_bf2048_h3_trace as trace_base
import run_federation_v3_bf1024_h3_fallback_suite as suite


ASSETS = Path(os.environ.get(
    "FEDERATION_FALLBACK_PAPER_ASSETS_ROOT",
    str(suite.base.EXP / "paper_assets/federation-v3-bf1024-h3-fallback-pilot"),
)).resolve()
DATASET_LABEL = os.environ.get(
    "FEDERATION_FALLBACK_DATASET_LABEL",
    "pre-fix diagnostic only; invalidated for final trend interpretation by later "
    "staged-fallback DATA-route and local-provider obligation fixes",
)
PLOT_DATASET_LABEL = os.environ.get("FEDERATION_FALLBACK_PLOT_LABEL", DATASET_LABEL)

plt.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42})
base.PILOTS = suite.PILOTS
base.RESULTS = suite.RESULTS
base.run_dir = suite.run_dir
base.validate = suite.validate
base.ASSETS = ASSETS

END_TO_END_SCOPE = "requester-side monotonic request_start to first app_receive"
PROVIDER_PROXY_SCOPE = (
    "first observed matching provider_hit; proxy only, not verified as the DATA source "
    "that won the request race"
)
WINDOWED_REQUEST_SCOPE = (
    "logical send events observed from request_start through first app_receive; source "
    "broadcast counts as one logical event; excludes later lifecycle events; not physical "
    "link frames"
)
FULL_REQUEST_SCOPE = (
    "full raw-trace logical send events; source broadcast counts as one logical event; "
    "not physical link frames"
)
LIFECYCLE_ATTRIBUTION = (
    "assigned to the most recent global request_start until the next request_start; valid "
    "because the controller is single-request closed-loop and waits for quiescence"
)


def read_csv(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def write_csv(path: Path, rows: list[dict]) -> None:
    """Write repository artifacts with stable LF endings on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def truthful_bins(rows: list[dict]) -> list[dict]:
    """Rename inherited fields so the exported estimator and event scope are explicit."""
    output = []
    mappings = {
        "mean_discovery_ms": "mean_request_start_to_first_observed_provider_hit_ms_proxy",
        "mean_return_path_ms":
            "mean_first_observed_provider_hit_to_request_completion_ms_proxy",
        "cache_served_fraction": "first_observed_provider_cache_fraction_proxy",
        "mean_request_forward_events":
            "mean_logical_request_send_events_through_first_app_receive",
        "mean_correct_provider_hit_events":
            "mean_matching_provider_hit_events_through_first_app_receive",
        "mean_unrelated_provider_hit_events":
            "mean_unrelated_provider_hit_events_through_first_app_receive",
        "mean_unrelated_data_handle_events":
            "mean_unrelated_data_handle_events_through_first_app_receive",
        "mean_unrelated_data_bytes":
            "mean_unrelated_data_bytes_through_first_app_receive",
        "mean_serving_overlay_hops":
            "mean_requester_to_first_observed_provider_overlay_hops_proxy",
    }
    for row in rows:
        renamed = dict(row)
        for old, new in mappings.items():
            renamed[new] = renamed.pop(old)
        renamed["end_to_end_latency_scope"] = END_TO_END_SCOPE
        renamed["provider_attribution_scope"] = PROVIDER_PROXY_SCOPE
        renamed["request_accounting_scope"] = WINDOWED_REQUEST_SCOPE
        renamed["dataset_status"] = DATASET_LABEL
        output.append(renamed)
    return output


def truthful_progress(rows: list[dict]) -> list[dict]:
    output = []
    for row in rows:
        renamed = dict(row)
        renamed["first_observed_provider_service_class_proxy"] = renamed.pop("service_class")
        renamed["requester_side_end_to_end_ms"] = renamed.pop("end_to_end_ms")
        renamed["end_to_end_latency_scope"] = END_TO_END_SCOPE
        renamed["provider_attribution_scope"] = PROVIDER_PROXY_SCOPE
        renamed["dataset_status"] = DATASET_LABEL
        output.append(renamed)
    return output


def truthful_decisions(rows: list[dict]) -> list[dict]:
    output = []
    for row in rows:
        renamed = dict(row)
        renamed["first_observed_provider_cache_fraction_proxy_change_percentage_points"] = (
            renamed.pop("cache_fraction_change_percentage_points")
        )
        renamed["logical_request_send_event_change_percent_through_first_app_receive"] = (
            renamed.pop("request_forward_change_percent")
        )
        renamed["assessment_scope"] = "single-seed, single-run descriptive threshold check"
        renamed["dataset_status"] = DATASET_LABEL
        output.append(renamed)
    return output


def add_dataset_status(rows: list[dict]) -> list[dict]:
    return [{**row, "dataset_status": DATASET_LABEL} for row in rows]


def fallback_summary() -> list[dict]:
    output = []
    for pilot in suite.PILOTS:
        root = suite.run_dir(pilot)
        ok, reason = suite.validate(pilot, root)
        if not ok:
            raise RuntimeError(f"{pilot.slug} is not READY: {reason}")
        grouped: dict[tuple[str, int, str], list[dict[str, str]]] = defaultdict(list)
        for row in read_csv(root / "trace_breakdown.csv"):
            grouped[(row["phase"], int(row["bin"]), row["direction"])].append(row)
        for (phase, bin_index, direction), rows in sorted(grouped.items()):
            recovered = [int(row["fallback_recovery_latency_ns"]) for row in rows
                         if row.get("fallback_recovery_latency_ns")]
            output.append({
                "config": pilot.slug,
                "phase": phase,
                "bin": bin_index,
                "direction": direction,
                "request_class": rows[0]["request_class"],
                "requests": len(rows),
                "fallback_triggered_requests": sum(int(row["fallback_triggered"]) for row in rows),
                "fallback_triggered_fraction": mean(int(row["fallback_triggered"]) for row in rows),
                "immediate_dead_end_expansion_requests": sum(
                    int(row["immediate_dead_end_fallback_events"]) > 0 for row in rows),
                "immediate_dead_end_expansion_events_through_first_app_receive": sum(
                    int(row["immediate_dead_end_fallback_events"]) for row in rows),
                "source_fallback_triggered_and_completed_requests": len(recovered),
                "mean_source_fallback_trigger_to_app_receive_ms":
                    mean(recovered) / 1e6 if recovered else "",
                "preferred_logical_send_events_through_first_app_receive": sum(
                    int(row["normal_request_frames"]) for row in rows),
                "fallback_logical_send_events_through_first_app_receive": sum(
                    int(row["fallback_request_frames"]) for row in rows),
                "preferred_logical_send_event_count_times_166_bytes_through_first_app_receive": sum(
                    int(row["normal_request_bytes"]) for row in rows),
                "fallback_reported_frame_bytes_sum_through_first_app_receive": sum(
                    int(row["fallback_request_bytes"]) for row in rows),
                "duplicate_request_suppressed_events_through_first_app_receive": sum(
                    int(row["duplicate_request_suppressed_events"]) for row in rows),
                "query_expired_unsatisfied_events_through_first_app_receive": sum(
                    int(row["query_expired_unsatisfied_events"]) for row in rows),
                "request_accounting_scope": WINDOWED_REQUEST_SCOPE,
                "preferred_byte_accounting_scope": (
                    "logical send-event count times the 166-byte single-signature frame; "
                    "aggregate signature_count was not logged; not observed physical bytes"
                ),
                "fallback_byte_accounting_scope":
                    "sum of frame_bytes reported by fallback_forward logical events",
                "dataset_status": DATASET_LABEL,
            })
    return output


def lifecycle_classes(events: list[dict]) -> list[str]:
    """Classify each event under the global closed-loop request lifecycle."""
    starts = sorted((event["_ts"], "isolation_probe"
                     if "_probe_" in str(event.get("key", "")) else "formal_measurement")
                    for event in events if event.get("event") == "request_start")
    start_times = [timestamp for timestamp, _ in starts]
    output = []
    for event in events:
        index = bisect.bisect_right(start_times, event["_ts"]) - 1
        output.append(starts[index][1] if index >= 0 else "unattributed")
    return output


def raw_request_accounting_row(config: str, root: Path, catalog_size: int | str = "",
                               distribution: str = "") -> dict:
    """Count full-trace logical send events and separate probe lifecycles."""
    def raw_events():
        for path in sorted((root / "traces").glob("*.jsonl")):
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                        event["_ts"] = int(event.get("ts_mono_ns", 0))
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if event["_ts"]:
                        yield event

    starts = sorted((event for event in raw_events() if event.get("event") == "request_start"),
                    key=lambda event: event["_ts"])
    start_times = [event["_ts"] for event in starts]
    schedule_path = root / "schedule.tsv"
    probe_path = root / "isolation_probes.tsv"
    schedule_keys = ({row["key"] for row in read_csv(schedule_path, "\t")}
                     if schedule_path.exists() else set())
    probe_keys = ({row["key"] for row in read_csv(probe_path, "\t")}
                  if probe_path.exists() else set())
    use_catalog_membership = bool(schedule_keys or probe_keys)

    def classify_start(event: dict) -> str:
        key = str(event.get("key", ""))
        if use_catalog_membership:
            if key in schedule_keys:
                return "formal_measurement"
            if key in probe_keys:
                return "isolation_probe"
            return "unknown_request"
        return "isolation_probe" if "_probe_" in key else "formal_measurement"

    start_classes = [classify_start(event) for event in starts]
    formal_starts = [event for event, label in zip(starts, start_classes)
                     if label == "formal_measurement"]
    probe_starts = [event for event, label in zip(starts, start_classes)
                    if label == "isolation_probe"]
    unknown_starts = [event for event, label in zip(starts, start_classes)
                      if label == "unknown_request"]

    event_names = {
        "request_send_done", "request_forward_done", "fallback_forward",
        "immediate_dead_end_fallback", "origin_fallback_triggered",
        "duplicate_request_suppressed", "query_expired_unsatisfied",
    }
    tagged: list[tuple[dict, str]] = []
    for event in raw_events():
        if event.get("event") not in event_names:
            continue
        index = bisect.bisect_right(start_times, event["_ts"]) - 1
        tagged.append((event, start_classes[index] if index >= 0 else "unattributed"))

    def selected(names: set[str], lifecycle: str | None = None) -> list[dict]:
        return [event for event, label in tagged if event.get("event") in names
                and (lifecycle is None or label == lifecycle)]

    preferred_names = {"request_send_done", "request_forward_done"}
    fallback_names = {"fallback_forward"}
    row: dict[str, object] = {
        "config": config,
        "catalog_size_per_cluster": catalog_size,
        "distribution": distribution,
        "formal_measurement_requests": len(formal_starts),
        "isolation_probes": len(probe_starts),
        "unknown_request_starts": len(unknown_starts),
        "dataset_status": DATASET_LABEL,
    }
    labels = ((None, "full_raw"), ("formal_measurement", "formal_lifecycles"),
              ("isolation_probe", "isolation_probe_lifecycles"),
              ("unknown_request", "unknown_request_lifecycles"),
              ("unattributed", "unattributed"))
    for lifecycle, suffix in labels:
        preferred = selected(preferred_names, lifecycle)
        fallback = selected(fallback_names, lifecycle)
        row[f"preferred_logical_send_events_{suffix}"] = len(preferred)
        row[f"fallback_logical_send_events_{suffix}"] = len(fallback)
        row[f"preferred_logical_send_event_count_times_166_bytes_{suffix}"] = sum(
            trace_base.number(event.get("frame_bytes")) or 166 for event in preferred)
        row[f"fallback_reported_frame_bytes_sum_{suffix}"] = sum(
            trace_base.number(event.get("frame_bytes")) for event in fallback)

    event_types = (
        ("immediate_dead_end_fallback", "immediate_dead_end_expansion_events"),
        ("origin_fallback_triggered", "origin_fallback_triggered_events"),
        ("duplicate_request_suppressed", "duplicate_request_suppressed_events"),
        ("query_expired_unsatisfied", "query_expired_unsatisfied_events"),
    )
    for event_type, prefix in event_types:
        for lifecycle, suffix in labels:
            row[f"{prefix}_{suffix}"] = len(selected({event_type}, lifecycle))
    preferred_all = selected(preferred_names)
    fallback_all = selected(fallback_names)
    row.update({
        "preferred_events_with_reported_frame_bytes_full_raw": sum(
            bool(trace_base.number(event.get("frame_bytes"))) for event in preferred_all),
        "preferred_events_without_reported_frame_bytes_full_raw": sum(
            not bool(trace_base.number(event.get("frame_bytes"))) for event in preferred_all),
        "fallback_events_with_reported_frame_bytes_full_raw": sum(
            bool(trace_base.number(event.get("frame_bytes"))) for event in fallback_all),
        "fallback_events_without_reported_frame_bytes_full_raw": sum(
            not bool(trace_base.number(event.get("frame_bytes"))) for event in fallback_all),
        "request_accounting_scope": FULL_REQUEST_SCOPE,
        "lifecycle_attribution_method": LIFECYCLE_ATTRIBUTION,
        "preferred_byte_accounting_scope": (
            "logical send-event count times 166-byte single-signature frame when frame_bytes "
            "is absent; aggregate frame size and physical egress multiplicity unavailable"
        ),
        "fallback_byte_accounting_scope":
            "sum of frame_bytes reported by fallback_forward logical events",
    })
    metadata_path = root / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        row["git_commit"] = metadata.get("git_commit", "")
        row["git_dirty"] = metadata.get("git_dirty", "")
    return row


def run_level_request_accounting() -> list[dict]:
    rows = [raw_request_accounting_row(pilot.slug, suite.run_dir(pilot), pilot.catalog_size,
                                       pilot.distribution) for pilot in suite.PILOTS]
    for row in rows:
        if (row["formal_measurement_requests"] != 3584 or row["isolation_probes"] != 2 or
                row["unknown_request_starts"] != 0):
            raise RuntimeError(f"unexpected raw request lifecycle shape for {row['config']}")
    return rows


def add_dataset_banner(fig) -> None:
    engine = fig.get_layout_engine()
    if engine is not None:
        engine.set(rect=(0.0, 0.045, 1.0, 0.955))
    fig.text(0.5, 0.012, f"Dataset status: {PLOT_DATASET_LABEL}", ha="center", va="bottom",
             fontsize=7, color="#7A1F1F")


def plot_standard(bins: list[dict], progress: list[dict], occupancy: list[dict],
                  bloom: list[dict], output: Path) -> None:
    colors = {pilot.slug: color for pilot, color in
              zip(suite.PILOTS, ("#0072B2", "#56B4E9", "#D55E00", "#009E73"))}
    with PdfPages(output) as pdf:
        fig, axes = plt.subplots(2, 2, figsize=(11, 8.5), constrained_layout=True)
        for pilot in suite.PILOTS:
            pre = [row for row in bins if row["config"] == pilot.slug and row["phase"] == "pre"]
            post = [row for row in bins if row["config"] == pilot.slug and
                    row["request_class"] == "remote"]
            for cluster, style in (("A_local", "-"), ("B_local", "--")):
                rows = [row for row in pre if row["direction"] == cluster]
                axes[0, 0].plot([row["bin"] for row in rows],
                                [row["mean_end_to_end_ms"] for row in rows], style,
                                color=colors[pilot.slug], alpha=.8,
                                label=f"{pilot.slug} {cluster[0]}")
            combined: dict[int, list[dict]] = defaultdict(list)
            for row in post:
                combined[int(row["bin"])].append(row)
            order_bins = sorted(combined)
            axes[0, 1].plot(order_bins,
                            [mean(x["mean_end_to_end_ms"] for x in combined[index])
                             for index in order_bins], color=colors[pilot.slug], label=pilot.slug)
            axes[1, 0].plot(order_bins,
                            [mean(x["first_observed_provider_cache_fraction_proxy"]
                                  for x in combined[index]) for index in order_bins],
                            color=colors[pilot.slug], label=pilot.slug)
            axes[1, 1].plot(order_bins,
                            [mean(x["mean_logical_request_send_events_through_first_app_receive"]
                                  for x in combined[index]) for index in order_bins],
                            color=colors[pilot.slug], label=pilot.slug)
        settings = (
            (axes[0, 0], "Pre-link local requester-side latency", "End-to-end latency (ms)"),
            (axes[0, 1], "Post-link remote requester-side latency", "End-to-end latency (ms)"),
            (axes[1, 0], "First-observed-provider cache fraction (proxy)", "Fraction"),
            (axes[1, 1], "Logical REQUEST send events through first app receive",
             "Logical events / application request"),
        )
        for axis, title, ylabel in settings:
            axis.set(title=title, xlabel="128-request bin", ylabel=ylabel)
            axis.grid(alpha=.25)
            axis.legend(fontsize=7)
        fig.suptitle("End-to-end latency is the authoritative requester-side measurement",
                     fontsize=10)
        add_dataset_banner(fig)
        pdf.savefig(fig)
        plt.close(fig)

        fig, axes = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True)
        unrelated_provider_values: list[float] = []
        unrelated_data_values: list[float] = []
        for pilot in suite.PILOTS:
            rows = [row for row in progress if row["config"] == pilot.slug]
            axes[0].plot([row["remote_request_number"] for row in rows],
                         [row["cumulative_unique_keys"] for row in rows],
                         color=colors[pilot.slug], label=pilot.slug)
            axes[1].plot([row["remote_request_number"] for row in rows],
                         [row["cumulative_repeat_fraction"] for row in rows],
                         color=colors[pilot.slug], label=pilot.slug)
        axes[0].set(title="Cumulative distinct remote keys", xlabel="Remote request number",
                    ylabel="Distinct keys")
        axes[1].set(title="Observed repetition", xlabel="Remote request number",
                    ylabel="Cumulative repeat fraction")
        for axis in axes:
            axis.grid(alpha=.25)
            axis.legend(fontsize=8)
        add_dataset_banner(fig)
        pdf.savefig(fig)
        plt.close(fig)

        order = ["ready", "pre-bin02", "pre-bin04", "pre-bin06", "pre-bin08", "pre-t0",
                 "post-t0", "post-bin01", "post-bin02", "post-bin04", "post-bin06",
                 "post-bin08", "post-bin10", "post-bin12", "post-bin14", "post-bin16",
                 "final"]
        fig, axes = plt.subplots(2, 1, figsize=(11, 8.5), constrained_layout=True)
        for pilot in suite.PILOTS:
            rows = [row for row in occupancy if row["config"] == pilot.slug]
            totals: dict[str, int] = defaultdict(int)
            for row in rows:
                totals[row["snapshot_id"]] += int(row["cached_copy_keys"])
            axes[0].plot(range(len(order)), [totals[x] for x in order],
                         color=colors[pilot.slug], label=pilot.slug)
            states = {row["snapshot_id"]: float(row["mean_bloom_occupancy"])
                      for row in bloom if row["config"] == pilot.slug}
            axes[1].plot(range(len(order)), [states.get(x, math.nan) for x in order],
                         color=colors[pilot.slug], label=pilot.slug)
        for axis, title, ylabel in (
                (axes[0], "Cached copies across both clusters", "Cached copy count"),
                (axes[1], "Mean SyncTable Bloom occupancy", "Set-bit fraction")):
            axis.set(title=title, ylabel=ylabel, xticks=range(len(order)), xticklabels=order)
            axis.tick_params(axis="x", rotation=45, labelsize=7)
            axis.grid(alpha=.25)
            axis.legend(fontsize=8)
        add_dataset_banner(fig)
        pdf.savefig(fig)
        plt.close(fig)

        fig, axes = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True)
        for pilot in suite.PILOTS:
            rows = [row for row in bins if row["config"] == pilot.slug and
                    row["request_class"] == "remote"]
            grouped: dict[int, list[dict]] = defaultdict(list)
            for row in rows:
                grouped[int(row["bin"])].append(row)
            order_bins = sorted(grouped)
            provider_values = [
                mean(x["mean_unrelated_provider_hit_events_through_first_app_receive"]
                     for x in grouped[index]) for index in order_bins
            ]
            data_values = [
                mean(x["mean_unrelated_data_bytes_through_first_app_receive"]
                     for x in grouped[index]) / 1024 for index in order_bins
            ]
            unrelated_provider_values.extend(provider_values)
            unrelated_data_values.extend(data_values)
            axes[0].plot(order_bins, provider_values, color=colors[pilot.slug], label=pilot.slug)
            axes[1].plot(order_bins, data_values, color=colors[pilot.slug], label=pilot.slug)
        axes[0].set(title="Unrelated provider-hit events through first app receive",
                    xlabel="Post remote bin", ylabel="Logical events / request")
        axes[1].set(title="Unrelated DATA handled through first app receive",
                    xlabel="Post remote bin", ylabel="KiB / request")
        for axis, values in zip(axes, (unrelated_provider_values, unrelated_data_values)):
            if values and not any(values):
                axis.set_ylim(0, 1)
                axis.set_yticks([0])
                axis.text(0.5, 0.5, "Zero observed in all configurations and bins",
                          transform=axis.transAxes, ha="center", va="center", fontsize=10)
            axis.grid(alpha=.25)
            axis.legend(fontsize=8)
        add_dataset_banner(fig)
        pdf.savefig(fig)
        plt.close(fig)


def plot_fallback(rows: list[dict], output: Path) -> None:
    colors = {pilot.slug: color for pilot, color in
              zip(suite.PILOTS, ("#0072B2", "#56B4E9", "#D55E00", "#009E73"))}
    with PdfPages(output) as pdf:
        fig, axes = plt.subplots(2, 2, figsize=(11, 8.5), constrained_layout=True)
        for pilot in suite.PILOTS:
            selected = [row for row in rows if row["config"] == pilot.slug and
                        row["phase"] == "post" and row["request_class"] == "remote"]
            grouped: dict[int, list[dict]] = defaultdict(list)
            for row in selected:
                grouped[int(row["bin"])].append(row)
            bins = sorted(grouped)
            fraction = [mean(float(item["fallback_triggered_fraction"])
                             for item in grouped[index]) for index in bins]
            recovery = []
            for index in bins:
                samples = [(float(item["mean_source_fallback_trigger_to_app_receive_ms"]),
                            int(item["source_fallback_triggered_and_completed_requests"]))
                           for item in grouped[index]
                           if item["mean_source_fallback_trigger_to_app_receive_ms"] != ""]
                recovery.append(sum(value * count for value, count in samples) /
                                sum(count for _, count in samples) if samples else math.nan)
            fallback_events = [sum(int(item["fallback_logical_send_events_through_first_app_receive"])
                                   for item in grouped[index]) /
                               sum(int(item["requests"]) for item in grouped[index])
                               for index in bins]
            preferred_events = [sum(int(item["preferred_logical_send_events_through_first_app_receive"])
                                    for item in grouped[index]) /
                                sum(int(item["requests"]) for item in grouped[index])
                                for index in bins]
            axes[0, 0].plot(bins, fraction, color=colors[pilot.slug], label=pilot.slug)
            axes[0, 1].plot(bins, recovery, color=colors[pilot.slug], label=pilot.slug)
            axes[1, 0].plot(bins, preferred_events, color=colors[pilot.slug], label=pilot.slug)
            axes[1, 1].plot(bins, fallback_events, color=colors[pilot.slug], label=pilot.slug)
        settings = (
            (axes[0, 0], "Source timed-fallback trigger fraction", "Fraction"),
            (axes[0, 1], "Source fallback trigger to app receive", "Milliseconds"),
            (axes[1, 0], "Preferred logical send events through first app receive",
             "Logical events / request"),
            (axes[1, 1], "Fallback logical send events through first app receive",
             "Logical events / request"),
        )
        for axis, title, ylabel in settings:
            axis.set(title=title, xlabel="Post remote 128-request bin", ylabel=ylabel)
            axis.grid(alpha=.25)
            axis.legend(fontsize=7)
        fig.suptitle("REQUEST counters are logical trace events, not physical link frames",
                     fontsize=10)
        add_dataset_banner(fig)
        pdf.savefig(fig)
        plt.close(fig)


def analysis_notes(decisions: list[dict], accounting: list[dict]) -> str:
    total_requests = sum(int(row["formal_measurement_requests"]) for row in accounting)
    formal_expiry = sum(int(row["query_expired_unsatisfied_events_formal_lifecycles"])
                        for row in accounting)
    probe_expiry = sum(int(row["query_expired_unsatisfied_events_isolation_probe_lifecycles"])
                       for row in accounting)
    commits = sorted({str(row.get("git_commit", "")) for row in accounting})
    dirty = sorted({str(row.get("git_dirty", "")) for row in accounting})
    lines = [
        "# Federation-v3 staged-fallback pilot analysis notes", "",
        f"**Dataset status:** {DATASET_LABEL}.", "",
        "## Statistical scope", "",
        "Each configuration contains one deterministic seed and one run. These outputs are "
        "exploratory descriptions only: they contain no confidence intervals, hypothesis tests, "
        "or population-level claims.", "",
        "## Authoritative latency and provider proxies", "",
        f"End-to-end latency is authoritative and is measured as `{END_TO_END_SCOPE}`. Discovery, "
        "return-path latency, service class, cache fraction, and serving distance use the first "
        f"matching `provider_hit` ({PROVIDER_PROXY_SCOPE}). Multiple providers can report a hit, so "
        "those fields do not prove which DATA response completed the request.", "",
        "## REQUEST accounting", "",
        "`pilot_bin_summary.csv` and `pilot_fallback_summary.csv` count logical send events only "
        "between request start and first application receipt. `pilot_run_level_request_accounting.csv` "
        "adds full raw-trace totals and separates the two isolation-probe lifecycles. A source "
        "broadcast is one logged logical event even if it targets multiple egresses; none of these "
        "counts are physical link-frame counts. Preferred-event bytes are 166-byte single-signature "
        "equivalents because aggregate frame size was not recorded.", "",
        "`immediate_dead_end_expansion` is an observed expansion to remaining neighbors after no "
        "valid matched next hop; it is not evidence that the action causally recovered the request.", "",
        "## Cache occupancy catalog accounting", "",
        "`original_keys` includes the configured catalog plus one reserved pre-activation isolation "
        "probe object in each cluster. It is therefore 513 for the 512-key runs and 1,001 for the "
        "1,000-key runs; the reserved probe is not part of the formal request workload.", "",
        "## Completion, expiry, and provenance", "",
        f"All four runs completed {total_requests:,}/{total_requests:,} formal requests. Full-trace "
        f"accounting found {formal_expiry} formal unsatisfied expiries and {probe_expiry} expiries "
        "belonging to the eight expected pre-activation isolation probes.", "",
        f"Recorded commit: `{', '.join(commits)}`; recorded `git_dirty`: `{', '.join(dirty)}`. "
        "The run metadata does not preserve the dirty patch or a container image identifier, so exact "
        "source provenance cannot be reconstructed from the manifest alone. The immutable raw "
        "metadata also retains an earlier, erroneous harness label saying that the two fixes were "
        "still required. Both fixes are ancestors of the recorded run commit; the derived artifacts "
        "use the corrected dataset status shown above.", "",
        "## Warm-up decisions", "",
        "The pre-link decision compares bins 5–6 with 7–8; the post-link decision compares remote "
        "bins 13–14 with 15–16. These are predeclared single-run thresholds, not statistical tests.", "",
        "| Configuration | pre A | pre B | post remote | Recorded consequence |",
        "|---|---|---|---|---|",
    ]
    by_config: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in decisions:
        by_config[str(row["config"])][str(row["test"])] = row
    for pilot in suite.PILOTS:
        rows = by_config[pilot.slug]
        label = lambda test: "stable" if int(rows[test]["stable"]) else "not stable"
        lines.append(f"| {pilot.slug} | {label('pre_A')} | {label('pre_B')} | "
                     f"{label('post_remote')} | {rows['post_remote']['consequence']} |")
    lines.extend(["", "The cache-fraction proxy and logical REQUEST-event changes are supporting "
                  "threshold inputs; requester-side end-to-end latency remains authoritative.", ""])
    return "\n".join(lines)


def main() -> int:
    inherited_bins, inherited_progress, inherited_occupancy, inherited_bloom, inherited_decisions = (
        base.summarize()
    )
    bins = truthful_bins(inherited_bins)
    progress = truthful_progress(inherited_progress)
    occupancy = add_dataset_status(inherited_occupancy)
    bloom = add_dataset_status(inherited_bloom)
    decisions = truthful_decisions(inherited_decisions)
    fallback = fallback_summary()
    accounting = run_level_request_accounting()
    ASSETS.mkdir(parents=True, exist_ok=True)
    write_csv(ASSETS / "pilot_bin_summary.csv", bins)
    write_csv(ASSETS / "pilot_key_progress.csv", progress)
    write_csv(ASSETS / "pilot_cache_occupancy.csv", occupancy)
    write_csv(ASSETS / "pilot_bloom_occupancy.csv", bloom)
    write_csv(ASSETS / "pilot_warmup_assessment.csv", decisions)
    write_csv(ASSETS / "pilot_fallback_summary.csv", fallback)
    write_csv(ASSETS / "pilot_run_level_request_accounting.csv", accounting)
    (ASSETS / "pilot_analysis_notes.md").write_text(
        analysis_notes(decisions, accounting), encoding="utf-8")
    with tempfile.TemporaryDirectory() as temporary:
        standard = Path(temporary) / "standard.pdf"
        fallback_pdf = Path(temporary) / "fallback.pdf"
        plot_standard(bins, progress, occupancy, bloom, standard)
        plot_fallback(fallback, fallback_pdf)
        subprocess.run(["pdfunite", str(standard), str(fallback_pdf),
                        str(ASSETS / "pilot_trends.pdf")], check=True)
    print(f"wrote exploratory staged-fallback outputs to {ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
