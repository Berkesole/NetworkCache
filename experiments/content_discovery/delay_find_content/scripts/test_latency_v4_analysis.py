#!/usr/bin/env python3
"""Unit tests for latency-v4 run/seed aggregation and paired effects."""

from __future__ import annotations

import math

import analyze_latency_v4_bf1024_h3_c512 as analysis
import latency_v3_controller as controller
import run_latency_v4_bf1024_h3_c512_suite as suite


def main() -> int:
    center, low, high = analysis.ci95([float(value) for value in range(1, 11)])
    assert center == 5.5 and low < center < high
    assert math.isclose(high - center, center - low)
    assert analysis.period("controlled", {"round": "2"}, 0) == "early"
    assert analysis.period("controlled", {"round": "7"}, 0) == "late"
    assert analysis.period("iid-zipf", {"bin": "4"}, 16) == "early"
    assert analysis.period("iid-zipf", {"bin": "13"}, 16) == "late"
    assert analysis.period("iid-zipf", {"bin": "29"}, 32) == "late"
    assert controller.request_group({"phase": "measurement", "round": "3", "bin": "3"}) == ("measurement", "3")
    assert controller.request_group({"phase": "measurement", "round": "0", "bin": "16"}) == ("measurement", "16")

    # Repetitions are averaged equally; request counts never enter this stage.
    run_rows = [
        {"config": "c", "seed": 1, "period": "early", "end_to_end_latency_ns_mean": 10.0},
        {"config": "c", "seed": 1, "period": "early", "end_to_end_latency_ns_mean": 30.0},
    ]
    seed = analysis.aggregate(run_rows, "period", True)
    assert len(seed) == 1 and seed[0]["repetitions"] == 2
    assert seed[0]["end_to_end_latency_ns_mean"] == 20.0

    metadata = {}
    period_seeds = []
    for config in suite.CONFIGS:
        metadata[config.slug] = {"workload": config.workload,
                                "topology": f"kua-dc-v4-{config.topology}",
                                "method": config.method, "value_size_bytes": config.size}
        method_offset = 100.0 if config.method == "known-owner" else 200.0 if config.method == "tcp-direct" else 0.0
        for seed_index in range(1, 11):
            for period_name in ("early", "late", "all"):
                row = {"config": config.slug, "seed": seed_index, "period": period_name}
                for metric in analysis.METRICS:
                    row[f"{metric}_mean"] = 1000.0 + method_offset + seed_index
                period_seeds.append(row)
    paired = analysis.paired(period_seeds, metadata)
    target = next(row for row in paired if row["comparison"] == "known_owner_minus_nc"
                  and row["left_config"] == "controlled-16-nc-b65536"
                  and row["period"] == "early" and row["metric"] == "end_to_end_latency_ns")
    assert target["paired_seed_count"] == 10
    assert math.isclose(target["difference_mean"], 100.0)
    print("latency-v4 aggregation and paired-effect tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
