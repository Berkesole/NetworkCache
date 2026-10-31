#!/usr/bin/env python3
"""Generate paper-style Matplotlib figures for content discovery results."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

os.environ.setdefault("MPLCONFIGDIR", str(Path(os.environ.get("TMPDIR", "/tmp")) / "rsync-matplotlib"))

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


LINE_SCENARIOS = ["line-3-cold", "line-4-cold", "line-5-cold"]
DC_TOPOLOGIES = ["kua-dc-16", "kua-dc-32"]
DC_TITLES = {
    "kua-dc-16": "Kua-DC 16",
    "kua-dc-32": "Kua-DC 32",
}
WARM_METRICS = [
    "provider_hit_hop",
    "miss_latency_ns",
    "discovery_delay_ns",
    "request_packets",
    "aggregate_flushes",
]
WARM_LABELS = {
    "provider_hit_hop": "Provider\nhop",
    "miss_latency_ns": "Miss\nlatency",
    "discovery_delay_ns": "Discovery\ndelay",
    "request_packets": "REQUEST\npackets",
    "aggregate_flushes": "Aggregate\nflushes",
}


def configure_matplotlib() -> None:
    mpl.rcParams.update(
        {
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 10,
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "legend.frameon": False,
            "lines.linewidth": 1.6,
            "lines.markersize": 4.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def ns_to_ms(value: float) -> float:
    return value / 1_000_000.0


def require_number(row: Dict[str, str], field: str) -> float:
    value = number(row.get(field))
    if value is None:
        scenario = row.get("scenario", "<unknown>")
        raise SystemExit(f"missing numeric field {field!r} in {scenario}")
    return value


def field_number(row: Dict[str, str], field: str) -> float:
    value = number(row.get(field))
    if value is None:
        raise SystemExit(f"missing numeric field {field!r} in {row.get('scenario', '<unknown>')}")
    return value


def collect_line_series(aggregate_rows: List[Dict[str, str]]) -> List[Dict[str, float]]:
    rows = {row.get("scenario", ""): row for row in aggregate_rows}
    series: List[Dict[str, float]] = []
    for scenario in LINE_SCENARIOS:
        row = rows.get(scenario)
        if not row:
            continue
        hop = require_number(row, "provider_hit_hop_median")
        discovery_median = ns_to_ms(require_number(row, "discovery_delay_ns_median"))
        discovery_min = ns_to_ms(require_number(row, "discovery_delay_ns_min"))
        discovery_max = ns_to_ms(require_number(row, "discovery_delay_ns_max"))
        miss_median = ns_to_ms(require_number(row, "miss_latency_ns_median"))
        miss_min = ns_to_ms(require_number(row, "miss_latency_ns_min"))
        miss_max = ns_to_ms(require_number(row, "miss_latency_ns_max"))
        series.append(
            {
                "hop": hop,
                "discovery_median": discovery_median,
                "discovery_err_low": discovery_median - discovery_min,
                "discovery_err_high": discovery_max - discovery_median,
                "miss_median": miss_median,
                "miss_err_low": miss_median - miss_min,
                "miss_err_high": miss_max - miss_median,
            }
        )
    if not series:
        raise SystemExit("line hop sweep plot has no usable aggregate rows")
    return series


def has_line_series(aggregate_rows: List[Dict[str, str]]) -> bool:
    scenarios = {row.get("scenario", "") for row in aggregate_rows}
    return any(scenario in scenarios for scenario in LINE_SCENARIOS)


def save_figure(fig: mpl.figure.Figure, stem: Path, formats: Sequence[str]) -> List[Path]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for fmt in formats:
        output = stem.with_suffix(f".{fmt}")
        fig.savefig(output)
        written.append(output)
    plt.close(fig)
    return written


def plot_line_hop_sweep(
    aggregate_rows: List[Dict[str, str]],
    output_stem: Path,
    formats: Sequence[str],
) -> List[Path]:
    points = collect_line_series(aggregate_rows)
    hops = [point["hop"] for point in points]

    fig, ax = plt.subplots(figsize=(3.45, 2.35), constrained_layout=True)
    styles = [
        ("discovery", "Discovery delay", "#1f77b4", "o"),
        ("miss", "End-to-end miss latency", "#d62728", "s"),
    ]
    for prefix, label, color, marker in styles:
        medians = [point[f"{prefix}_median"] for point in points]
        yerr = [
            [point[f"{prefix}_err_low"] for point in points],
            [point[f"{prefix}_err_high"] for point in points],
        ]
        ax.errorbar(
            hops,
            medians,
            yerr=yerr,
            label=label,
            color=color,
            marker=marker,
            capsize=2.5,
            elinewidth=0.8,
            markerfacecolor="white",
            markeredgewidth=1.1,
        )

    ax.set_xlabel("Provider hit hop")
    ax.set_ylabel("Latency (ms)")
    ax.set_xticks(hops)
    ax.grid(axis="y", color="#d0d0d0", linewidth=0.55, alpha=0.7)
    ax.legend(loc="upper left")
    ax.set_title("Line topology hop sweep")
    return save_figure(fig, output_stem, formats)


def plot_warm_comparison(
    comparison_rows: List[Dict[str, str]],
    output_stem: Path,
    formats: Sequence[str],
) -> List[Path]:
    rows = {row.get("metric", ""): row for row in comparison_rows}
    metrics = [metric for metric in WARM_METRICS if metric in rows]
    if not metrics:
        raise SystemExit("warm comparison plot has no usable comparison rows")

    ratios: List[float] = []
    labels: List[str] = []
    for metric in metrics:
        ratio = number(rows[metric].get("path_over_cold_ratio"))
        if ratio is None:
            continue
        ratios.append(ratio)
        labels.append(WARM_LABELS.get(metric, metric))
    if not ratios:
        raise SystemExit("warm comparison plot has no usable ratio rows")

    x = list(range(len(ratios)))
    width = 0.34
    fig, ax = plt.subplots(figsize=(3.65, 2.3), constrained_layout=True)
    ax.bar(
        [value - width / 2 for value in x],
        [1.0] * len(x),
        width,
        label="Cold",
        color="#8c8c8c",
        edgecolor="#4d4d4d",
        linewidth=0.5,
    )
    ax.bar(
        [value + width / 2 for value in x],
        ratios,
        width,
        label="Path materialization",
        color="#4daf4a",
        edgecolor="#2f6f2f",
        linewidth=0.5,
        hatch="//",
    )
    ax.axhline(1.0, color="#4d4d4d", linestyle="--", linewidth=0.8)
    ax.set_ylabel("Normalized median")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, max(1.08, max(ratios) * 1.18))
    ax.grid(axis="y", color="#d0d0d0", linewidth=0.55, alpha=0.7)
    ax.legend(loc="upper right")
    ax.set_title("Warm path materialization")

    for xpos, ratio in zip(x, ratios):
        ax.text(xpos + width / 2, ratio + 0.035, f"{ratio:.2f}", ha="center", va="bottom", fontsize=7.5)

    return save_figure(fig, output_stem, formats)


def find_kua_dc_rows(
    aggregate_rows: List[Dict[str, str]],
    topology: str,
) -> Optional[Tuple[Dict[str, str], Dict[str, str]]]:
    cold: Optional[Dict[str, str]] = None
    warm: Optional[Dict[str, str]] = None
    for row in aggregate_rows:
        if row.get("topology") != topology:
            continue
        if row.get("warm_type") == "cold":
            cold = row
        elif row.get("warm_type") == "path_materialization":
            warm = row
    if cold and warm:
        return cold, warm
    return None


def annotate_bars(ax: mpl.axes.Axes, bars: Sequence[mpl.patches.Rectangle], fmt: str = "{:.0f}") -> None:
    upper = ax.get_ylim()[1]
    for bar in bars:
        height = bar.get_height()
        if height <= 0:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height + upper * 0.025,
            fmt.format(height),
            ha="center",
            va="bottom",
            fontsize=7.2,
        )


def plot_kua_dc_topology_summary(
    aggregate_rows: List[Dict[str, str]],
    topology: str,
    output_stem: Path,
    formats: Sequence[str],
) -> List[Path]:
    rows = find_kua_dc_rows(aggregate_rows, topology)
    if not rows:
        return []
    cold, warm = rows

    title = DC_TITLES.get(topology, topology)
    cold_color = "#6f6f6f"
    warm_color = "#2ca25f"
    discovery_color = "#4c78a8"
    return_color = "#f58518"

    cold_hop = field_number(cold, "provider_hit_hop_median")
    warm_hop = field_number(warm, "provider_hit_hop_median")

    cold_discovery = ns_to_ms(field_number(cold, "discovery_delay_ns_median"))
    warm_discovery = ns_to_ms(field_number(warm, "discovery_delay_ns_median"))
    cold_return = ns_to_ms(field_number(cold, "return_delay_ns_median"))
    warm_return = ns_to_ms(field_number(warm, "return_delay_ns_median"))
    cold_total = ns_to_ms(field_number(cold, "miss_latency_ns_median"))
    warm_total = ns_to_ms(field_number(warm, "miss_latency_ns_median"))

    traffic_metrics = [
        ("request_packets", "REQUEST\npackets"),
        ("data_packets", "DATA\npackets"),
        ("aggregate_flushes", "Aggregate\nflushes"),
    ]
    cold_traffic = [field_number(cold, f"{metric}_median") for metric, _ in traffic_metrics]
    warm_traffic = [field_number(warm, f"{metric}_median") for metric, _ in traffic_metrics]

    fig, axes = plt.subplots(
        1,
        3,
        figsize=(6.85, 2.55),
        constrained_layout=True,
        gridspec_kw={"width_ratios": [0.8, 1.15, 1.35]},
    )

    # Panel A: provider-hit distance.
    ax = axes[0]
    hop_bars = ax.bar(
        [0, 1],
        [cold_hop, warm_hop],
        color=[cold_color, warm_color],
        edgecolor="#333333",
        linewidth=0.5,
    )
    ax.set_ylabel("Provider-hit hop")
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Cold", "Warm"])
    ax.set_xlabel(f"(a) {title}: hit distance", labelpad=8)
    ax.set_ylim(0, max(cold_hop, warm_hop) * 1.35)
    annotate_bars(ax, hop_bars)

    # Panel B: end-to-end miss latency as discovery plus return delay.
    ax = axes[1]
    x_latency = [0, 1]
    discovery_bars = ax.bar(
        x_latency,
        [cold_discovery, warm_discovery],
        color=discovery_color,
        edgecolor="#333333",
        linewidth=0.5,
        label="Discovery",
    )
    ax.bar(
        x_latency,
        [cold_return, warm_return],
        bottom=[cold_discovery, warm_discovery],
        color=return_color,
        edgecolor="#333333",
        linewidth=0.5,
        label="Return",
    )
    ax.set_ylabel("Median latency (ms)")
    ax.set_xticks(x_latency)
    ax.set_xticklabels(["Cold", "Warm"])
    ax.set_xlabel("(b) Miss latency", labelpad=28)
    max_total = max(cold_total, warm_total)
    ax.set_ylim(0, max_total * 1.28)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2, fontsize=7.2, borderaxespad=0)
    for xpos, total in zip(x_latency, [cold_total, warm_total]):
        ax.text(xpos, total + max_total * 0.045, f"{total:.0f}", ha="center", va="bottom", fontsize=7.2)
    for bar in discovery_bars:
        if bar.get_height() > max_total * 0.13:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() / 2,
                f"{bar.get_height():.0f}",
                ha="center",
                va="center",
                fontsize=7,
                color="white",
            )

    # Panel C: packet and aggregate pressure. A log scale keeps warm values visible.
    ax = axes[2]
    x_traffic = list(range(len(traffic_metrics)))
    width = 0.34
    cold_bars = ax.bar(
        [x - width / 2 for x in x_traffic],
        cold_traffic,
        width,
        color=cold_color,
        edgecolor="#333333",
        linewidth=0.5,
        label="Cold",
    )
    warm_bars = ax.bar(
        [x + width / 2 for x in x_traffic],
        warm_traffic,
        width,
        color=warm_color,
        edgecolor="#333333",
        linewidth=0.5,
        label="Warm",
    )
    ax.set_ylabel("Median count (log)")
    ax.set_xticks(x_traffic)
    ax.set_xticklabels([label for _, label in traffic_metrics])
    ax.set_xlabel("(c) Network work", labelpad=8)
    ax.set_yscale("log")
    ax.set_ylim(0.8, max(cold_traffic + warm_traffic) * 2.4)
    for bars in [cold_bars, warm_bars]:
        for bar in bars:
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height * 1.16,
                f"{height:g}",
                ha="center",
                va="bottom",
                fontsize=7.0,
            )

    return save_figure(fig, output_stem, formats)


def parse_formats(text: str) -> List[str]:
    formats = [item.strip().lower().lstrip(".") for item in text.split(",") if item.strip()]
    allowed = {"pdf", "svg", "png"}
    invalid = [fmt for fmt in formats if fmt not in allowed]
    if invalid:
        raise SystemExit(f"unsupported output format(s): {', '.join(invalid)}")
    return formats or ["pdf", "svg"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--formats", default="pdf,svg", help="comma-separated output formats: pdf,svg,png")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_matplotlib()

    batch_dir = args.batch_dir
    output_dir = args.output_dir or batch_dir / "plots"
    formats = parse_formats(args.formats)
    aggregate_rows = read_csv(batch_dir / "aggregate.csv")
    comparison_rows = read_csv(batch_dir / "comparison.csv")

    written: List[Path] = []
    if has_line_series(aggregate_rows):
        written.extend(plot_line_hop_sweep(aggregate_rows, output_dir / "line_hop_sweep", formats))

    legacy_comparison_rows = [
        row for row in comparison_rows if row.get("scenario", "") == "binary-tree-7-path-warm"
    ]
    if legacy_comparison_rows:
        written.extend(
            plot_warm_comparison(
                legacy_comparison_rows,
                output_dir / "warm_path_materialization",
                formats,
            )
        )

    for topology in DC_TOPOLOGIES:
        written.extend(
            plot_kua_dc_topology_summary(
                aggregate_rows,
                topology,
                output_dir / f"{topology.replace('-', '_')}_path_materialization",
                formats,
            )
        )

    if not written:
        raise SystemExit("no supported plot inputs found in aggregate/comparison CSVs")

    for output in written:
        print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
