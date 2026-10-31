#!/usr/bin/env python3
"""Deterministic generator, pairing, matrix and timeline tests for federation-v4."""

from __future__ import annotations

import csv
import hashlib
import tempfile
from collections import Counter
from pathlib import Path

import analyze_federation_v4_bf1024_h3_formal as analysis
import generate_federation_v4_bf1024_h3_formal as gen
import run_federation_v4_bf1024_h3_formal_suite as runner


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_generated_workloads() -> None:
    gen.validate(gen.DEFAULT_ROOT)
    endpoint_fields = (
        "request_index", "phase", "bin", "request_class", "direction", "requester", "owner",
        "requester_cluster", "owner_cluster", "original_owner_overlay_hops",
        "overlay_edge_physical_signature",
    )
    seed_hashes = set()
    for seed in gen.SEEDS:
        schedules = {slug: rows(gen.DEFAULT_ROOT / "mapped" / gen.scenario_name(slug, seed) / "schedule.tsv")
                     for slug, _, _ in gen.WORKLOADS}
        baseline = [tuple(row[field] for field in endpoint_fields)
                    for row in schedules["catalog512-zipf"]]
        seed_hashes.add(digest(gen.DEFAULT_ROOT / "mapped" /
                               gen.scenario_name("catalog512-zipf", seed) / "schedule.tsv"))
        assert all(baseline == [tuple(row[field] for field in endpoint_fields) for row in values]
                   for values in schedules.values())
        for values in schedules.values():
            assert Counter(row["direction"] for row in values if row["phase"] == "pre") == {
                "A_local": 512, "B_local": 512}
            for batch in range(1, 17):
                selected = [row for row in values if row["phase"] == "post" and row["bin"] == str(batch)]
                assert Counter(row["direction"] for row in selected) == {
                    "A_to_B": 64, "B_to_A": 64, "A_local": 16, "B_local": 16}
    assert len(seed_hashes) == 10


def test_matrix_pairing() -> None:
    assert len(runner.CONFIGS) == 6
    assert sum(20 for _ in runner.CONFIGS) == 120
    baseline = [config for config in runner.CONFIGS if config.method != "nc"]
    assert {(config.method, config.workload) for config in baseline} == {
        ("known-owner", "catalog512-zipf"), ("tcp-direct", "catalog512-zipf")}


def test_phase_positions_and_plot() -> None:
    assert analysis.phase_position({"phase": "pre", "bin": "1"}) == -8
    assert analysis.phase_position({"phase": "pre", "bin": "8"}) == -1
    assert analysis.phase_position({"phase": "post", "bin": "1"}) == 1
    assert analysis.phase_position({"phase": "post", "bin": "16"}) == 16
    synthetic = []
    for cluster in ("A", "B"):
        local = f"{cluster}_local"
        remote = "A_to_B" if cluster == "A" else "B_to_A"
        for position in (-8, -1, 1, 16):
            synthetic.append({"config": "catalog512-zipf-nc", "phase_position": position,
                              "direction": local, "request_class": "local" if position < 0 else "local_control",
                              "end_to_end_latency_ns_mean": 1_000_000,
                              "end_to_end_latency_ns_mean_ci95_low": 900_000,
                              "end_to_end_latency_ns_mean_ci95_high": 1_100_000})
        for position in (1, 16):
            synthetic.append({"config": "catalog512-zipf-nc", "phase_position": position,
                              "direction": remote, "request_class": "remote",
                              "end_to_end_latency_ns_mean": 2_000_000,
                              "end_to_end_latency_ns_mean_ci95_low": 1_800_000,
                              "end_to_end_latency_ns_mean_ci95_high": 2_200_000})
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "timeline.png"
        analysis.plot_continuous(synthetic, "en", output)
        assert output.stat().st_size > 1000


def test_selection_and_paired_ci() -> None:
    config = runner.CONFIGS[0]
    original = runner.candidates
    try:
        runner.candidates = lambda _config, _seed: [
            (Path("attempt001"), False, "injected_failure"),
            (Path("attempt002"), True, "ok"),
            (Path("attempt003"), True, "ok"),
            (Path("attempt004"), True, "ok"),
        ]
        assert runner.selected(config, 1) == [Path("attempt002"), Path("attempt003")]
    finally:
        runner.candidates = original

    seed_periods = []
    configs = {item.slug for item in runner.CONFIGS}
    for seed in range(1, 11):
        for slug in configs:
            for direction in ("A_to_B", "B_to_A"):
                base = 2_000_000.0 if slug == "catalog512-zipf-nc" else 1_000_000.0
                seed_periods.append({
                    "config": slug, "seed": seed, "period": "post_late",
                    "direction": direction, "request_class": "remote",
                    "end_to_end_latency_ns_mean": base,
                })
            for direction in ("A_local", "B_local"):
                seed_periods.extend((
                    {"config": slug, "seed": seed, "period": "pre_steady",
                     "direction": direction, "request_class": "local",
                     "end_to_end_latency_ns_mean": 1_000_000.0},
                    {"config": slug, "seed": seed, "period": "post_late",
                     "direction": direction, "request_class": "local_control",
                     "end_to_end_latency_ns_mean": 1_100_000.0},
                ))
    _, overall = analysis.paired_effects(seed_periods)
    target = next(row for row in overall
                  if row["comparison"] == "nc_vs_known_owner_512_zipf_late")
    assert target["seed_count"] == 10
    assert target["paired_latency_difference_ns"] == 1_000_000.0
    assert target["paired_latency_ratio"] == 2.0
    local = [row for row in overall
             if row["comparison"] == "post_federation_local_vs_pre_steady"]
    assert len(local) == 6
    assert all(row["paired_latency_difference_ns"] == 100_000.0 for row in local)


def main() -> int:
    test_generated_workloads()
    test_matrix_pairing()
    test_phase_positions_and_plot()
    test_selection_and_paired_ci()
    print("federation-v4 tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
