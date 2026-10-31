#!/usr/bin/env python3
"""Render separate exploratory reports for the 2048/4096 latency-v4 pilots."""

from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean

os.environ.setdefault("MPLCONFIGDIR", "/tmp/rsync-latency-v4-pilot-matplotlib")
import matplotlib  # noqa: E402

matplotlib.use("Agg")
matplotlib.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42})
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402


REPO = Path(__file__).resolve().parents[4]
EXP = REPO / "experiments/content_discovery/delay_find_content"
RESULTS = EXP / "results/latency-v4-bf1024-h3-c512"
ASSETS = EXP / "paper_assets/latency-v4-bf1024-h3-c512"
FONT_PATH = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
FONT = FontProperties(fname=str(FONT_PATH)) if FONT_PATH.exists() else FontProperties()
FONT_BOLD_PATH = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
FONT_BOLD = FontProperties(fname=str(FONT_BOLD_PATH)) if FONT_BOLD_PATH.exists() else FONT

LABELS = {
    "iid-zipf-16-nc-b65536": "16-host Zipf",
    "iid-zipf-32-nc-b65536": "32-host Zipf",
    "iid-uniform-32-nc-b65536": "32-host Uniform",
}
COLORS = {
    "iid-zipf-16-nc-b65536": "#2563eb",
    "iid-zipf-32-nc-b65536": "#ea580c",
    "iid-uniform-32-nc-b65536": "#059669",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def selected_runs(count: int) -> tuple[list[dict[str, str]], dict[str, Path]]:
    root = RESULTS / f"pilots-iid{count}"
    assessment = read_csv(root / "pilot_assessment.csv")
    runs = {row["config"]: Path(row["run_dir"]) for row in assessment}
    if set(runs) != set(LABELS):
        raise RuntimeError(f"unexpected pilot set under {root}: {sorted(runs)}")
    return assessment, runs


def load_metrics(run: Path) -> dict[str, object]:
    request_rows = read_csv(run / "request_results.csv")
    trace_rows = read_csv(run / "trace_breakdown.csv")
    measured = [row for row in request_rows if row["phase"] == "measurement"]
    trace = [row for row in trace_rows if row["phase"] == "measurement"]
    by_bin_req: dict[int, list[dict[str, str]]] = defaultdict(list)
    by_bin_trace: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in measured:
        by_bin_req[int(row["bin"])].append(row)
    for row in trace:
        by_bin_trace[int(row["bin"])].append(row)
    bins = sorted(by_bin_req)
    latency = []
    cache = []
    preferred = []
    fallback = []
    unique = set()
    unique_progress = []
    for bin_index in bins:
        requests = by_bin_req[bin_index]
        traces = by_bin_trace[bin_index]
        latency.append(mean(int(row["end_to_end_latency_ns"]) / 1e6 for row in requests))
        cache.append(100 * mean(row["first_observed_service_class_proxy"] == "cache" for row in traces))
        preferred.append(mean(int(row["preferred_logical_request_events"]) for row in traces))
        fallback.append(sum(int(row["source_fallback_triggered"]) for row in traces))
        unique.update(row["key"] for row in requests)
        unique_progress.append(len(unique))
    metadata = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    return {
        "bins": bins,
        "latency": latency,
        "cache": cache,
        "preferred": preferred,
        "fallback": fallback,
        "unique": unique_progress,
        "measurement": len(measured),
        "ok": sum(row["status"] == "ok" for row in measured),
        "hash_ok": sum(row["actual_fnv1a64"] == row["expected_fnv1a64"] for row in measured),
        "single_delivery": sum(row["application_delivery_events"] == "1" for row in trace),
        "formal_expiry": sum(int(row["query_expired_unsatisfied_events_in_delivery_window"]) for row in trace),
        "fallback_total": sum(fallback),
        "commit": str(metadata.get("git_commit", "unknown"))[:12],
    }


def add_header(fig, title: str, subtitle: str) -> None:
    fig.text(0.06, 0.95, title, fontproperties=FONT_BOLD, fontsize=20, color="#172554", va="top")
    fig.text(0.06, 0.905, subtitle, fontproperties=FONT, fontsize=9.5, color="#475569", va="top")


def style_axis(ax) -> None:
    ax.grid(axis="y", color="#cbd5e1", alpha=0.55, linewidth=0.7)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=8)


def render(count: int) -> Path:
    assessment, runs = selected_runs(count)
    metrics = {config: load_metrics(path) for config, path in runs.items()}
    by_config = {row["config"]: row for row in assessment}
    out_dir = ASSETS / f"pilots-iid{count}"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"latency_v4_iid{count}_pilot_report.pdf"

    with PdfPages(output) as pdf:
        fig = plt.figure(figsize=(11.69, 8.27), facecolor="white")
        add_header(fig, f"NetworkCache latency-v4：{count} 请求 Pilot", "1024-bit Bloom filter · 3 hashes · 512-key catalog · wire v3 · staged fallback")
        fig.text(0.06, 0.83, "结论", fontproperties=FONT_BOLD, fontsize=15, color="#172554")
        zipf_ready = all(by_config[c]["stable"] == "True" for c in LABELS if "zipf" in c)
        conclusion = ("两组 Zipf 均达到预定义门槛，可进入正式实验放行审核。" if zipf_ready else
                      "运行完整，但至少一组 Zipf 未达到预定义稳定性门槛；本报告不放行正式矩阵。")
        fig.text(0.06, 0.785, conclusion, fontproperties=FONT, fontsize=12, color="#991b1b" if not zipf_ready else "#166534")
        columns = ["配置", "完成", "Latency Δ", "Cache Δ", "Preferred Δ", "Gate"]
        table_rows = []
        for config in LABELS:
            row = by_config[config]
            m = metrics[config]
            table_rows.append([
                LABELS[config], f"{m['ok']}/{m['measurement']}",
                f"{100*float(row['latency_relative_change']):.1f}%",
                f"{float(row['cache_fraction_change_pp']):.2f} pp",
                f"{100*float(row['preferred_events_relative_change']):.1f}%",
                "PASS" if row["stable"] == "True" else "FAIL",
            ])
        ax = fig.add_axes([0.06, 0.37, 0.88, 0.34]); ax.axis("off")
        table = ax.table(cellText=table_rows, colLabels=columns, cellLoc="center", loc="center", colWidths=[.25,.14,.14,.14,.16,.12])
        table.auto_set_font_size(False); table.set_fontsize(10); table.scale(1, 2.05)
        for (r, c), cell in table.get_celld().items():
            cell.get_text().set_fontproperties(FONT_BOLD if r == 0 else FONT)
            cell.set_edgecolor("#cbd5e1")
            cell.set_facecolor("#e2e8f0" if r == 0 else "#f8fafc")
            if r > 0 and c == 5:
                cell.get_text().set_color("#166534" if table_rows[r-1][5] == "PASS" else "#b91c1c")
        fig.text(0.06, 0.27, "质量门槛", fontproperties=FONT_BOLD, fontsize=12, color="#172554")
        fig.text(0.06, 0.225,
                 "每组请求全部完成、DATA hash/size 正确、每请求单次应用交付、正式 expiry=0、请求区间无重叠。",
                 fontproperties=FONT, fontsize=10, color="#334155")
        fig.text(0.06, 0.16,
                 "探索性限制：单 seed、单 repetition；不计算置信区间，不用于显著性结论。端到端延迟来自 requester monotonic clock。",
                 fontproperties=FONT, fontsize=9.5, color="#64748b")
        pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)

        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        fig.subplots_adjust(left=.09, right=.96, bottom=.12, top=.83)
        add_header(fig, "各请求批次的端到端请求延迟", "每批包含128个连续、顺序执行的请求；批次仅用于统计汇总，不表示并发提交")
        for config in LABELS:
            m = metrics[config]
            ax.plot(m["bins"], m["latency"], marker="o", markersize=3, linewidth=1.8, label=LABELS[config], color=COLORS[config])
        start = 13 if count == 2048 else 29
        ax.axvspan(start, start + 1, color="#f59e0b", alpha=.12, label="earlier late-stage batches")
        ax.axvspan(start + 2, start + 3, color="#7c3aed", alpha=.10, label="final late-stage batches")
        ax.set_xlabel("Request batch (128 sequential requests)", fontproperties=FONT); ax.set_ylabel("Mean end-to-end latency (ms)", fontproperties=FONT)
        style_axis(ax); ax.legend(prop=FONT, ncol=2, loc="upper right")
        fig.text(.09, .045, "均值按请求计算，仅用于单次 pilot 轨迹检查；图中不含置信区间。", fontproperties=FONT, fontsize=9, color="#64748b")
        pdf.savefig(fig); plt.close(fig)

        fig, axes = plt.subplots(2, 1, figsize=(11.69, 8.27), sharex=True)
        fig.subplots_adjust(left=.09, right=.96, bottom=.10, top=.82, hspace=.24)
        add_header(fig, "缓存形成与首选路由开销", "Cache 指 first-observed matching provider-hit proxy；REQUEST 指 logical send events，不是物理链路帧")
        for config in LABELS:
            m = metrics[config]
            axes[0].plot(m["bins"], m["cache"], linewidth=1.7, label=LABELS[config], color=COLORS[config])
            axes[1].plot(m["bins"], m["preferred"], linewidth=1.7, label=LABELS[config], color=COLORS[config])
        axes[0].set_ylabel("Cache-service proxy (%)", fontproperties=FONT)
        axes[1].set_ylabel("Preferred logical REQUEST / request", fontproperties=FONT)
        axes[1].set_xlabel("Request batch (128 sequential requests)", fontproperties=FONT)
        for ax in axes: style_axis(ax); ax.legend(prop=FONT, ncol=3, fontsize=8)
        pdf.savefig(fig); plt.close(fig)

        fig, axes = plt.subplots(2, 1, figsize=(11.69, 8.27), sharex=True)
        fig.subplots_adjust(left=.09, right=.96, bottom=.12, top=.82, hspace=.26)
        add_header(fig, "Fallback 与对象重复访问轨迹", "Fallback 是完整性机制；稀有的高延迟事件可能显著影响单个请求批次的算术均值")
        for config in LABELS:
            m = metrics[config]
            axes[0].step(m["bins"], m["fallback"], where="mid", linewidth=1.7, label=LABELS[config], color=COLORS[config])
            axes[1].plot(m["bins"], m["unique"], linewidth=1.7, label=LABELS[config], color=COLORS[config])
        axes[0].set_ylabel("Source fallback triggers", fontproperties=FONT)
        axes[1].set_ylabel("Cumulative unique keys", fontproperties=FONT)
        axes[1].set_xlabel("Request batch (128 sequential requests)", fontproperties=FONT)
        for ax in axes: style_axis(ax); ax.legend(prop=FONT, ncol=3, fontsize=8)
        totals = "；".join(f"{LABELS[c]}={metrics[c]['fallback_total']}" for c in LABELS)
        fig.text(.09, .045, f"全程 source fallback 触发次数：{totals}。", fontproperties=FONT, fontsize=9, color="#475569")
        pdf.savefig(fig); plt.close(fig)

    summary = {
        "dataset": f"latency-v4 pilots-iid{count}",
        "status": "exploratory_single_seed_single_run",
        "report": str(output),
        "assessment": assessment,
        "runs": {config: str(path) for config, path in runs.items()},
    }
    (out_dir / "report_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    for count in (2048, 4096):
        print(render(count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
