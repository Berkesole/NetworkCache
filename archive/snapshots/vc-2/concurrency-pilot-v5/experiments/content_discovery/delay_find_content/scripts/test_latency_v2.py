#!/usr/bin/env python3
"""Fast deterministic checks for the latency-v2 generator and matrix."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import classify_latency_v2_run
import collect_success_runs
import aggregate_latency_v2
import curate_latency_v2
import generate_latency_v2 as generator
import run_latency_v2_suite as suite


class LatencyV2GeneratorTest(unittest.TestCase):
    def make_latency_candidate(self, root: Path, usable_indices, status="incomplete", count=100):
        batch = root / "controlled-s2000-16-nc-attempts-001"
        run = batch / "runs" / "run-1"
        run.mkdir(parents=True, exist_ok=True)
        (run / "summary.csv").write_text("phase,miss_latency_ns\n", encoding="utf-8")
        (run / "metadata.json").write_text(
            json.dumps({"trace_clock": "monotonic_ns", "trace_enabled": "1"}),
            encoding="utf-8",
        )
        requests = [
            {
                "phase": "measurement",
                "request_index": index,
                "measurement_position": index,
            }
            for index in range(1, count + 1)
        ]
        (run / "schedule_metadata.json").write_text(
            json.dumps({"requests": requests}), encoding="utf-8"
        )
        columns = [
            "phase", "request_index", "request_id", "provider", "discovery_delay_ns",
            "return_delay_ns", "miss_latency_ns", "provider_hit_hop", "trace_complete",
        ]
        with (run / "trace_breakdown.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for index in usable_indices:
                writer.writerow(
                    {
                        "phase": "measurement",
                        "request_index": index,
                        "request_id": f"request-{index}",
                        "provider": "2",
                        "discovery_delay_ns": "10",
                        "return_delay_ns": "20",
                        "miss_latency_ns": "30",
                        "provider_hit_hop": "1",
                        "trace_complete": "1",
                    }
                )
        manifest = {
            "run_id": "run-1",
            "scenario": "scenario-seed001",
            "seed": "1",
            "status": status,
            "summary_path": "runs/run-1/summary.csv",
            "metadata_path": "runs/run-1/metadata.json",
        }
        return batch, manifest

    def assert_common(self, requests, expected_measurement):
        warmup = [row for row in requests if row["phase"] == "warmup"]
        measurement = [row for row in requests if row["phase"] == "measurement"]
        self.assertEqual(len(warmup), 16)
        self.assertEqual(len(measurement), expected_measurement)
        self.assertFalse({row["key"] for row in warmup} & {row["key"] for row in measurement})
        self.assertTrue(all(row["node"] != row["provider"] for row in requests))
        self.assertEqual(len({row["tick"] for row in requests}), len(requests))

    def test_controlled_is_deterministic_and_seeded(self):
        first = generator.controlled_requests("kua-dc-v3-16", 1, 2000)
        again = generator.controlled_requests("kua-dc-v3-16", 1, 2000)
        other = generator.controlled_requests("kua-dc-v3-16", 2, 2000)
        self.assertEqual(first, again)
        self.assertNotEqual(
            [(row["key"], row["node"]) for row in first],
            [(row["key"], row["node"]) for row in other],
        )
        self.assert_common(first, 120)

    def test_iid_zipf_is_deterministic_and_seeded(self):
        first = generator.iid_requests("kua-dc-v3-32", "zipf", 1, 2000)
        again = generator.iid_requests("kua-dc-v3-32", "zipf", 1, 2000)
        other = generator.iid_requests("kua-dc-v3-32", "zipf", 2, 2000)
        self.assertEqual(first, again)
        self.assertNotEqual(
            [(row["key"], row["node"]) for row in first],
            [(row["key"], row["node"]) for row in other],
        )
        self.assert_common(first, 240)
        self.assertEqual(
            generator.render_yaml("kua-dc-v3-32", "iid-zipf", 2000, first),
            generator.render_yaml("kua-dc-v3-32", "iid-zipf", 2000, again),
        )

    def test_uniform_uses_measured_catalog(self):
        requests = generator.iid_requests("kua-dc-v3-16", "uniform", 3, 2000)
        self.assert_common(requests, 240)
        measured = [row for row in requests if row["phase"] == "measurement"]
        self.assertTrue({row["key"] for row in measured} <= set(generator.measured_keys()))

    def test_load_profile_enforces_same_key_gap(self):
        requests = generator.iid_requests("kua-dc-v3-32", "zipf", 4, 250)
        self.assert_common(requests, 240)
        keys = [row["key"] for row in requests if row["phase"] == "measurement"]
        for index, key in enumerate(keys):
            self.assertNotIn(key, keys[max(0, index - 4):index])

    def test_formal_matrix_has_twelve_configs_and_240_runs(self):
        self.assertEqual(len(suite.CONFIGS), 12)
        self.assertEqual(len(suite.CONFIGS) * suite.TARGET_RUNS, 240)
        self.assertEqual(sum(config.method == "known-owner" for config in suite.CONFIGS), 5)
        schedules = {}
        for config in suite.CONFIGS:
            key = (config.workload, config.spacing_ms, config.topology)
            if key in schedules:
                self.assertEqual(config.scenarios, schedules[key])
            schedules[key] = config.scenarios

    def test_collector_prefers_balanced_seeds_and_has_round_robin_fallback(self):
        scenarios = [f"seed-{seed}" for seed in range(1, 6)]
        balanced = {
            scenario: [{"scenario": scenario, "run_id": f"{scenario}-{index}"} for index in range(4)]
            for scenario in scenarios
        }
        selected = collect_success_runs.build_balanced_total_manifest(balanced, scenarios, 20, 4)
        self.assertEqual(len(selected), 20)
        self.assertEqual({scenario: sum(row["scenario"] == scenario for row in selected) for scenario in scenarios}, {scenario: 4 for scenario in scenarios})

        fallback = dict(balanced)
        fallback[scenarios[0]] = fallback[scenarios[0]][:2]
        fallback[scenarios[1]].extend(
            {"scenario": scenarios[1], "run_id": f"extra-{index}"} for index in range(2)
        )
        fallback[scenarios[2]].extend(
            {"scenario": scenarios[2], "run_id": f"extra2-{index}"} for index in range(2)
        )
        selected = collect_success_runs.build_balanced_total_manifest(fallback, scenarios, 20, 4)
        self.assertEqual(len(selected), 20)
        self.assertEqual(sum(row["scenario"] == scenarios[0] for row in selected), 2)

        with self.assertRaises(SystemExit):
            collect_success_runs.build_balanced_total_manifest(
                {**balanced, scenarios[0]: []}, scenarios, 20, 4
            )

    def test_latency_v2_classification_uses_complete_measurement_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            (run_dir / "schedule_metadata.json").write_text(
                json.dumps({"measurement_requests": 2}), encoding="utf-8"
            )
            columns = ["phase", "trace_complete", "miss_latency_ns"]
            with (run_dir / "trace_breakdown.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(
                    [
                        {"phase": "warmup", "trace_complete": "0", "miss_latency_ns": ""},
                        {"phase": "measurement", "trace_complete": "1", "miss_latency_ns": "10"},
                        {"phase": "measurement", "trace_complete": "1", "miss_latency_ns": "20"},
                    ]
                )
            self.assertTrue(classify_latency_v2_run.has_complete_measurement_trace(run_dir))

            with (run_dir / "trace_breakdown.csv").open("a", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=columns).writerow(
                    {"phase": "measurement", "trace_complete": "1", "miss_latency_ns": "30"}
                )
            self.assertFalse(classify_latency_v2_run.has_complete_measurement_trace(run_dir))

    def test_resume_allocates_new_raw_directory_and_skips_ready_config(self):
        config = suite.CONFIGS[0]
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(suite, "RESULTS_ROOT", Path(temporary)):
                (Path(temporary) / f"{config.slug}-attempts-001").mkdir()
                (Path(temporary) / f"{config.slug}-attempts-003").mkdir()
                name, index = suite.next_attempt_dir(config)
                self.assertEqual(index, 2)
                self.assertEqual(name, f"{config.slug}-attempts-002")
        with mock.patch.object(suite, "latency_ready", return_value=True), mock.patch.object(
            suite, "run_attempt"
        ) as run_attempt:
            self.assertTrue(suite.run_config(config))
            run_attempt.assert_not_called()

    def test_latency_selection_is_balanced_deterministic_and_not_latency_sorted(self):
        config = "iid-zipf-s250-32-nc"
        candidates = []
        for seed in range(1, 6):
            for attempt in range(1, 7):
                candidates.append(
                    {
                        "config": config,
                        "seed": str(seed),
                        "attempt_batch": "attempts-001",
                        "attempt_order": attempt,
                        "run_id": f"seed{seed}-run{attempt}",
                        "eligible": "1" if attempt != 2 else "0",
                        "usable_measurement_rows": "228",
                        "scheduled_measurement_rows": "240",
                        "min_usable_rows_per_bin": "14",
                        "selected": "0",
                        "selection_index": "",
                        "synthetic_latency": str(1000 - attempt),
                    }
                )
        selected, status = curate_latency_v2.select_candidates(candidates, [config])
        self.assertEqual(status[config]["latency_status"], "READY")
        self.assertEqual(len(selected), 20)
        for seed in range(1, 6):
            run_ids = [row["run_id"] for row in selected if int(row["seed"]) == seed]
            self.assertEqual(run_ids, [f"seed{seed}-run{index}" for index in (1, 3, 4, 5)])

    def test_latency_candidate_accepts_incomplete_manifest_at_exact_quality_gates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usable = list(range(4, 101))  # 97/100 overall and 7/10 in the first bin.
            batch, manifest = self.make_latency_candidate(root, usable)
            row = curate_latency_v2.evaluate_candidate(
                "controlled-s2000-16-nc", batch, manifest, 1, check_clock=False
            )
            self.assertEqual(row["manifest_status"], "incomplete")
            self.assertEqual(row["eligible"], "1")
            self.assertEqual(row["min_usable_rows_per_bin"], 7)

            batch, manifest = self.make_latency_candidate(root, range(5, 101))
            row = curate_latency_v2.evaluate_candidate(
                "controlled-s2000-16-nc", batch, manifest, 1, check_clock=False
            )
            self.assertEqual(row["eligible"], "0")

            manifest["status"] = "driver_failed"
            row = curate_latency_v2.evaluate_candidate(
                "controlled-s2000-16-nc", batch, manifest, 1, check_clock=False
            )
            self.assertEqual(row["eligible"], "0")

            manifest["status"] = "incomplete"
            row = curate_latency_v2.evaluate_candidate(
                "controlled-s2000-16-nc", batch, manifest, 1, check_clock=True
            )
            self.assertEqual(row["eligible"], "0")
            (batch / manifest["metadata_path"]).unlink()
            row = curate_latency_v2.evaluate_candidate(
                "controlled-s2000-16-nc", batch, manifest, 1, check_clock=False
            )
            self.assertEqual(row["eligible"], "0")

    def test_analysis_uses_10_request_controlled_and_20_request_iid_bins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch, manifest = self.make_latency_candidate(root, range(1, 41), count=40)
            candidate = curate_latency_v2.evaluate_candidate(
                "controlled-s2000-16-nc", batch, manifest, 1, check_clock=False
            )
            controlled = aggregate_latency_v2.request_rows(
                [{key: str(value) for key, value in candidate.items()}]
            )
            self.assertEqual(sorted({row["request_bin"] for row in controlled}), ["1", "2", "3", "4"])

            candidate["config"] = "iid-zipf-s2000-16-nc"
            candidate["bin_width"] = "20"
            iid = aggregate_latency_v2.request_rows(
                [{key: str(value) for key, value in candidate.items()}]
            )
            self.assertEqual(sorted({row["request_bin"] for row in iid}), ["1", "2"])

    def test_seed_aggregation_weights_runs_equally(self):
        run_rows = [
            {
                "config": "config", "run_id": "run-1", "seed": "1", "request_bin": "1",
                "bin_width": "20", "usable_rows": 1, "scheduled_rows": 20,
                **{f"{metric}_mean": "0" for metric in aggregate_latency_v2.METRICS},
            },
            {
                "config": "config", "run_id": "run-2", "seed": "1", "request_bin": "1",
                "bin_width": "20", "usable_rows": 19, "scheduled_rows": 20,
                **{f"{metric}_mean": "100" for metric in aggregate_latency_v2.METRICS},
            },
        ]
        seed_rows = aggregate_latency_v2.aggregate_seed_rows(run_rows)
        self.assertEqual(seed_rows[0]["miss_latency_ns_mean"], "50")
        self.assertEqual(seed_rows[0]["run_count"], 2)

    def test_five_seed_ci_and_controlled_convergence(self):
        seed_rows = []
        for seed in range(1, 6):
            seed_rows.append(
                {
                    "config": "controlled-s2000-16-nc", "seed": str(seed),
                    "request_bin": "1", "bin_width": "10", "usable_rows": 40,
                    "scheduled_rows": 40,
                    **{f"{metric}_mean": str(seed * 10) for metric in aggregate_latency_v2.METRICS},
                }
            )
        overall = aggregate_latency_v2.aggregate_overall_rows(seed_rows)
        self.assertEqual(overall[0]["seed_count"], 5)
        self.assertEqual(overall[0]["miss_latency_ns_mean"], "30")
        self.assertTrue(overall[0]["miss_latency_ns_ci95_low"])
        self.assertTrue(overall[0]["miss_latency_ns_ci95_high"])

        curve = []
        for request_bin, latency in enumerate((100, 90, 80, 10, 10, 10), start=1):
            curve.append(
                {
                    "config": "controlled-s2000-16-nc", "request_bin": str(request_bin),
                    "bin_width": "10", "miss_latency_ns_mean": str(latency),
                }
            )
        summary = aggregate_latency_v2.summary_rows(
            curve, {"controlled-s2000-16-nc": 2000}
        )[0]
        self.assertEqual(summary["converged"], "1")
        self.assertEqual(summary["requests_to_converge"], 31)
        self.assertEqual(summary["time_to_converge_ms"], 60000)


if __name__ == "__main__":
    unittest.main()
