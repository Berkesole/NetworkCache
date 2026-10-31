#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
os.environ.setdefault("NETWORKCACHE_REPO", str(HERE.parents[1]))
SPEC = importlib.util.spec_from_file_location("long_horizon_runner", HERE / "runner.py")
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class MatrixTests(unittest.TestCase):
    def test_tcp_control_tree_exists_before_helpers_and_controller(self) -> None:
        source = (runner.SCRIPTS / "run_federation_v3_bf1024_h3_fallback_once.sh").read_text()
        start = source.index('if [[ "${METHOD}" == tcp-direct ]]; then', source.index('reset_ephemeral_shared_path "${SHARED}"'))
        end = source.index('\nrm -f ', start)
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, SHARED=directory, METHOD="tcp-direct")
            subprocess.run(["bash", "-euc", source[start:end]], env=env, check=True)
            control = Path(directory) / "control"
            for node in range(1, 65):
                command = control / "commands" / f"node{node}" / "test.tsv"
                command.write_text("command")
            (control / "completions" / "test.tsv").write_text("completion")
            self.assertFalse(list((control / "ready").iterdir()))
            env["METHOD"] = "nc"
            env["SHARED"] = str(Path(directory) / "nc")
            subprocess.run(["bash", "-euc", source[start:end]], env=env, check=True)
            self.assertFalse(Path(env["SHARED"]).exists())

    def test_assignment_is_exact(self) -> None:
        assignment = runner.expected_assignment()
        self.assertEqual(assignment["selected_slots"], 40)
        self.assertEqual(assignment["seeds"], list(range(1, 11)))
        self.assertEqual(assignment["methods"], ["nc", "tcp-direct"])
        self.assertEqual(
            assignment["assignment_sha256"],
            runner.canonical_digest(assignment, "assignment_sha256"),
        )

    def test_queue_has_40_unique_balanced_slots(self) -> None:
        slots = runner.planned_slots()
        self.assertEqual(len(slots), 40)
        self.assertEqual(len(set(slots)), 40)
        for repetition in (1, 2):
            for seed in range(1, 11):
                methods = [method for rep, item_seed, method in slots
                           if rep == repetition and item_seed == seed]
                self.assertEqual(set(methods), {"nc", "tcp-direct"})
        self.assertNotEqual(slots[:2], slots[2:4])

    def test_attempt_budget_and_selection(self) -> None:
        config = runner.Config("nc")
        paths = [Path(f"attempt{index:03d}") for index in range(1, 5)]
        candidates = [
            (paths[0], False, "bad"),
            (paths[1], True, "ok"),
            (paths[2], True, "ok"),
            (paths[3], True, "ok"),
        ]
        with mock.patch.object(runner, "candidates", return_value=candidates):
            self.assertEqual(runner.selected(config, 1), paths[1:3])

    def test_prior_campaign_attempt_does_not_consume_v2_budget(self) -> None:
        config = runner.Config("tcp-direct")
        current = {"campaign_fingerprint": "b" * 64}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "attempt004"
            old.mkdir()
            (old / "campaign_lock.json").write_text(json.dumps({
                "campaign_fingerprint": "a" * 64,
            }), encoding="utf-8")
            (old / "assignment.json").write_text(json.dumps({
                "assignment_sha256": "old",
            }), encoding="utf-8")
            (old / "completion_summary.json").write_text(json.dumps({
                "issued": 0, "completed": 0,
            }), encoding="utf-8")
            with mock.patch.object(runner, "attempts", return_value=[old]), \
                    mock.patch.object(runner, "load_campaign", return_value=current):
                self.assertEqual(runner.evidence_attempts(config, 1), [])
                self.assertEqual(
                    runner.non_evidence_reason(config, old),
                    "prior_campaign_tcp_helper_startup_race",
                )
                self.assertEqual(runner.next_attempt(config, 1).name, "attempt005")

    def test_prelaunch_failure_does_not_consume_v2_budget(self) -> None:
        config = runner.Config("nc")
        current = {"campaign_fingerprint": "b" * 64}
        assignment = {"assignment_sha256": "current"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attempt005"
            path.mkdir()
            (path / "campaign_lock.json").write_text(
                json.dumps(current), encoding="utf-8"
            )
            (path / "assignment.json").write_text(
                json.dumps(assignment), encoding="utf-8"
            )
            with mock.patch.object(runner, "load_campaign", return_value=current), \
                    mock.patch.object(runner, "load_assignment", return_value=assignment):
                self.assertEqual(
                    runner.non_evidence_reason(config, path),
                    "infrastructure_prelaunch_before_runtime_snapshot",
                )

    def test_missing_kathara_path_failure_is_non_evidence(self) -> None:
        config = runner.Config("nc")
        current = {"campaign_fingerprint": "b" * 64}
        assignment = {"assignment_sha256": "current"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attempt009"
            path.mkdir()
            (path / "campaign_lock.json").write_text(json.dumps(current), encoding="utf-8")
            (path / "assignment.json").write_text(json.dumps(assignment), encoding="utf-8")
            (path / "metadata.json").write_text(json.dumps({
                "status": "driver_failed", "runtime_image_id": "unavailable",
                "controller_status": 99,
            }), encoding="utf-8")
            (path / "failure_diagnostic.json").write_text(json.dumps({
                "completion": {"issued": 0, "completed": 0},
            }), encoding="utf-8")
            (path / "driver.stderr.log").write_text(
                "FAIL: missing required command: kathara\n" * 2,
                encoding="utf-8",
            )
            with mock.patch.object(runner, "load_campaign", return_value=current), \
                    mock.patch.object(runner, "load_assignment", return_value=assignment):
                self.assertEqual(
                    runner.non_evidence_reason(config, path),
                    "infrastructure_prelaunch_kathara_path",
                )

    def test_runner_contains_strict_tcp_helper_readiness_gate(self) -> None:
        source = (
            runner.SCRIPTS / "run_federation_v3_bf1024_h3_fallback_once.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("tcp helper readiness gate", source)
        self.assertIn("helpers_ready == 64", source)
        self.assertIn("tcp control permission gate: writable", source)
        self.assertIn('"${RUN_DIR}/controller-error.txt"', source)

    def test_only_declared_prior_nc_campaign_is_compatible(self) -> None:
        fingerprint, assignment_sha = next(iter(runner.COMPATIBLE_PRIOR_NC.items()))
        current_assignment = runner.expected_assignment()
        current = {
            "campaign_fingerprint": "c" * 64,
            "assignment_sha256": current_assignment["assignment_sha256"],
            "runtime_image_id": runner.EXPECTED_IMAGE,
            "requests_per_run": 11264,
            "post_batches": 64,
        }
        attached = {
            "campaign_fingerprint": fingerprint,
            "assignment_sha256": assignment_sha,
            "runtime_image_id": runner.EXPECTED_IMAGE,
            "requests_per_run": 11264,
            "post_batches": 64,
        }
        prior_assignment = {"assignment_sha256": assignment_sha}
        with mock.patch.object(runner, "load_assignment", return_value=current_assignment):
            self.assertTrue(runner.campaign_compatible(
                runner.Config("nc"), attached, prior_assignment, current
            ))
            self.assertFalse(runner.campaign_compatible(
                runner.Config("tcp-direct"), attached, prior_assignment, current
            ))

    def test_status_is_read_only(self) -> None:
        assignment = runner.expected_assignment()
        campaign = {
            "campaign_fingerprint": "c" * 64,
            "assignment_sha256": assignment["assignment_sha256"],
        }
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(runner, "RESULTS", Path(directory) / "missing"), \
                mock.patch.object(runner, "load_assignment", return_value=assignment), \
                mock.patch.object(runner, "load_campaign", return_value=campaign), \
                mock.patch.object(runner, "selected", return_value=[]), \
                mock.patch.object(runner, "attempts", return_value=[]), \
                mock.patch.object(runner, "service_state", return_value="inactive"):
            before = list(Path(directory).rglob("*"))
            status = runner.status_payload()
            after = list(Path(directory).rglob("*"))
        self.assertEqual(status["qualified"], 0)
        self.assertEqual(status["evidence_level"], "COLLECTING_NO_PARTIAL_STATS")
        self.assertEqual(before, after)


class ClassifierTests(unittest.TestCase):
    def test_tcp_rejects_raw_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            isolation = root / "isolation_results.csv"
            isolation.write_text(
                "probe_index,direction,requester,owner,key,status,timeout_ms,completion_present,quiescence_ack_count\n"
                "1,A_to_B,2,40,federation_v4h3_probe_B,ok,8000,1,64\n"
                "2,B_to_A,34,8,federation_v4h3_probe_A,ok,8000,1,64\n",
                encoding="utf-8",
            )
            traces = root / "traces"
            traces.mkdir()
            (traces / "node2.jsonl").write_text(
                json.dumps({"event": "query_expired_unsatisfied"}) + "\n",
                encoding="utf-8",
            )
            valid, reason = runner.isolation_ok(runner.Config("tcp-direct"), root, [])
        self.assertFalse(valid)
        self.assertEqual(reason, "tcp_direct_raw_query_expiry")


if __name__ == "__main__":
    unittest.main()
