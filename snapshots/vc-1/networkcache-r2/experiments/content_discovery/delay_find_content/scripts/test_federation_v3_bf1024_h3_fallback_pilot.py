#!/usr/bin/env python3
"""Deterministic harness checks for the 1K/3-hash staged-fallback pilot."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import generate_federation_v3_bf1024_h3_fallback_pilot as generator
import analyze_federation_v3_bf1024_h3_fallback_pilot as report
import run_federation_v3_bf1024_h3_fallback_suite as suite


HERE = Path(__file__).resolve().parent


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def check_generator() -> None:
    with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
        roots = (Path(first_tmp), Path(second_tmp))
        for root in roots:
            for config in generator.base.CONFIGS:
                generator.generate(root, *config)
            generator.validate(root)
        assert tree_digest(roots[0]) == tree_digest(roots[1])
        schedules = {}
        for slug, _, _ in generator.base.CONFIGS:
            scenario = f"kua-dc-v3-{generator.SUITE}-{slug}-nc-seed001"
            target = roots[0] / "mapped" / scenario
            schedules[slug] = read_tsv(target / "schedule.tsv")
            metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
            assert metadata["wire_version"] == 3
            assert metadata["bloom_bits"] == 1024
            assert metadata["bloom_hash_count"] == 3
            assert metadata["staged_fallback"] is True
            assert metadata["fallback_delay_ms"] == 1500
            assert metadata["query_table_timeout_ms"] == 7000
            assert metadata["controller_timeout_ms"] == 8000
            assert all(0 <= int(position) < 1024 for row in schedules[slug]
                       for position in row["bloom_positions"].split(";"))
        fields = ("request_index", "phase", "bin", "direction", "requester", "owner", "key_u")
        baseline = [tuple(row[field] for field in fields) for row in schedules["catalog512-zipf"]]
        assert all([tuple(row[field] for field in fields) for row in rows] == baseline
                   for rows in schedules.values())


def check_trace_analyzer() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        results = root / "requests.csv"
        traces = root / "traces"
        output = root / "trace.csv"
        traces.mkdir()
        request = {
            "request_index": "1", "phase": "post", "bin": "1", "round": "1",
            "request_class": "remote", "direction": "A_to_B", "key": "key-A",
            "key_rank": "1", "bloom_positions": "1;2;3", "requester": "2", "owner": "34",
            "request_start_mono_ns": "100", "app_receive_mono_ns": "300", "status": "ok",
            "packet_request_id": "9223934986808197121",
        }
        with results.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=request)
            writer.writeheader()
            writer.writerow(request)
        events = [
            {"ts_mono_ns": 100, "event": "request_start", "node": 2, "key": "key-A"},
            {"ts_mono_ns": 110, "event": "request_send_done", "node": 2, "src": 2, "dest": 1,
             "key": "key-A", "frame_bytes": 166},
            {"ts_mono_ns": 120, "event": "origin_fallback_armed", "node": 2, "key": "key-A"},
            {"ts_mono_ns": 200, "event": "origin_fallback_triggered", "node": 2, "key": "key-A"},
            {"ts_mono_ns": 210, "event": "fallback_forward", "node": 2, "src": 2, "dest": 1,
             "key": "key-A", "frame_bytes": 166},
            {"ts_mono_ns": 240, "event": "duplicate_request_suppressed", "node": 5, "key": "key-A"},
            {"ts_mono_ns": 250, "event": "provider_hit", "node": 34, "key": "key-A"},
            {"ts_mono_ns": 300, "event": "app_receive", "node": 2, "key": "key-A",
             "request_id": "9223934986808197121"},
        ]
        (traces / "node.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events),
                                           encoding="utf-8")
        subprocess.run([
            sys.executable, str(HERE / "analyze_federation_v3_bf1024_h3_fallback_trace.py"),
            "--results", str(results), "--traces", str(traces), "--output", str(output),
        ], check=True)
        with output.open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
        assert row["fallback_triggered"] == "1"
        assert row["fallback_recovery_latency_ns"] == "100"
        assert row["normal_request_frames"] == "1"
        assert row["fallback_request_frames"] == "1"
        assert row["normal_request_bytes"] == "166"
        assert row["fallback_request_bytes"] == "166"
        assert row["duplicate_request_suppressed_events"] == "1"
        assert row["query_expired_unsatisfied_events"] == "0"
        assert row["application_delivery_events"] == "1"


def check_failure_diagnostic() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "traces").mkdir()
        schedule = [{"request_index": "1", "key": "key-1"},
                    {"request_index": "2", "key": "key-2"}]
        with (root / "schedule.tsv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("request_index", "key"), delimiter="\t")
            writer.writeheader()
            writer.writerows(schedule)
        result = {"request_index": "1", "key": "key-1", "status": "ok"}
        with (root / "request_results.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=result)
            writer.writeheader()
            writer.writerow(result)
        (root / "metadata.json").write_text('{"status":"incomplete"}\n', encoding="utf-8")
        (root / "completion_summary.json").write_text(
            '{"scheduled":2,"issued":2,"completed":1}\n', encoding="utf-8")
        (root / "traces" / "node.jsonl").write_text(
            json.dumps({"ts_mono_ns": 5, "event": "query_expired_unsatisfied", "key": "key-2"}) + "\n",
            encoding="utf-8")
        subprocess.run([
            sys.executable, str(HERE / "diagnose_federation_v3_bf1024_h3_fallback.py"),
            "--run-dir", str(root),
        ], check=True, stdout=subprocess.DEVNULL)
        diagnostic = json.loads((root / "failure_diagnostic.json").read_text(encoding="utf-8"))
        assert diagnostic["first_unsatisfied_request"]["request_index"] == "2"
        assert diagnostic["fallback_event_counts"]["query_expired_unsatisfied"] == 1


def check_truthful_analysis_schema() -> None:
    source = {
        "config": "fixture", "catalog_size_per_cluster": 1, "distribution": "zipf",
        "phase": "post", "bin": 1, "direction": "A_to_B", "request_class": "remote",
        "usable_requests": 1, "mean_end_to_end_ms": 2.0, "mean_discovery_ms": 1.0,
        "mean_return_path_ms": 1.0, "cache_served_fraction": 1.0,
        "mean_request_forward_events": 3.0, "mean_correct_provider_hit_events": 2.0,
        "mean_unrelated_provider_hit_events": 0.0, "mean_unrelated_data_handle_events": 0.0,
        "mean_unrelated_data_bytes": 0.0, "mean_serving_overlay_hops": 2.0,
        "unique_keys_in_bin": 1, "repeat_fraction_in_bin": 0.0,
    }
    row = report.truthful_bins([source])[0]
    assert "mean_discovery_ms" not in row
    assert "cache_served_fraction" not in row
    assert "mean_request_forward_events" not in row
    assert row["mean_request_start_to_first_observed_provider_hit_ms_proxy"] == 1.0
    assert row["first_observed_provider_cache_fraction_proxy"] == 1.0
    assert "not physical link frames" in row["request_accounting_scope"]
    assert "first observed matching provider_hit" in row["provider_attribution_scope"]
    analyzer_source = (HERE / "analyze_federation_v3_bf1024_h3_fallback_pilot.py").read_text()
    assert "immediate_dead_end_recovered_requests" not in analyzer_source
    assert "immediate_dead_end_expansion_requests" in analyzer_source


def check_full_raw_request_accounting() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        traces = root / "traces"
        traces.mkdir()
        events = [
            {"ts_mono_ns": 50, "event": "request_send_done"},
            {"ts_mono_ns": 100, "event": "request_start", "key": "formal-key"},
            {"ts_mono_ns": 110, "event": "request_send_done"},
            {"ts_mono_ns": 120, "event": "fallback_forward", "frame_bytes": 166},
            {"ts_mono_ns": 125, "event": "origin_fallback_triggered"},
            {"ts_mono_ns": 140, "event": "fallback_forward", "frame_bytes": 166},
            {"ts_mono_ns": 200, "event": "request_start", "key": "fixture_probe_A"},
            {"ts_mono_ns": 210, "event": "request_forward_done"},
            {"ts_mono_ns": 220, "event": "fallback_forward", "frame_bytes": 166},
            {"ts_mono_ns": 225, "event": "origin_fallback_triggered"},
            {"ts_mono_ns": 230, "event": "immediate_dead_end_fallback"},
            {"ts_mono_ns": 240, "event": "query_expired_unsatisfied"},
            {"ts_mono_ns": 300, "event": "request_start", "key": "fixture_probe_B"},
            {"ts_mono_ns": 310, "event": "fallback_forward", "frame_bytes": 166},
            {"ts_mono_ns": 320, "event": "query_expired_unsatisfied"},
        ]
        (traces / "node.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        (root / "metadata.json").write_text(
            '{"git_commit":"fixture","git_dirty":false}\n', encoding="utf-8")
        row = report.raw_request_accounting_row("fixture", root)
        assert row["formal_measurement_requests"] == 1
        assert row["isolation_probes"] == 2
        assert row["unknown_request_starts"] == 0
        assert row["preferred_logical_send_events_full_raw"] == 3
        assert row["preferred_logical_send_events_formal_lifecycles"] == 1
        assert row["preferred_logical_send_events_isolation_probe_lifecycles"] == 1
        assert row["preferred_logical_send_events_unattributed"] == 1
        assert row["fallback_logical_send_events_full_raw"] == 4
        assert row["fallback_logical_send_events_formal_lifecycles"] == 2
        assert row["fallback_logical_send_events_isolation_probe_lifecycles"] == 2
        assert row["query_expired_unsatisfied_events_formal_lifecycles"] == 0
        assert row["query_expired_unsatisfied_events_isolation_probe_lifecycles"] == 2
        assert row["immediate_dead_end_expansion_events_isolation_probe_lifecycles"] == 1


def check_postfix_commit_gate() -> None:
    original = suite.REQUIRED_GIT_ANCESTOR
    try:
        suite.REQUIRED_GIT_ANCESTOR = "aa2c6f24f62d8529b30385dc90a3ebbe961ede51"
        assert not suite.commit_satisfies_required(
            {"git_commit": "9ca59fc4b878735cdc26da382a507dbd934c5182"})
        assert not suite.commit_satisfies_required(
            {"git_commit": "3744fd50b91166a958ee8a819436362978ceb42a"})
        current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=suite.base.REPO,
                                 check=True, text=True, capture_output=True).stdout.strip()
        assert suite.commit_satisfies_required({"git_commit": current})
    finally:
        suite.REQUIRED_GIT_ANCESTOR = original


def check_runner_provenance_and_topology_restore_contract() -> None:
    runner = (HERE / "run_federation_v3_bf1024_h3_fallback_once.sh").read_text(
        encoding="utf-8")
    launch = runner.index('"${REPO_ROOT}/scripts/kathara/run_ruikekkk_scenario.sh"')
    commit_snapshot = runner.index('GIT_COMMIT=$(git -C "${REPO_ROOT}" rev-parse HEAD')
    dirty_snapshot = runner.index('GIT_DIRTY=$([[ -n "$(git -C "${REPO_ROOT}" status')
    assert commit_snapshot < launch
    assert dirty_snapshot < launch
    assert runner.count("GIT_COMMIT=$(git -C") == 1
    assert runner.count("GIT_DIRTY=$([[ -n") == 1
    assert '"git_snapshot_phase": "before_kathara_launch"' in runner
    assert '"git_snapshot_at": git_snapshot_at' in runner

    cleanup_start = runner.index("cleanup() {")
    cleanup_end = runner.index("trap cleanup EXIT", cleanup_start)
    cleanup = runner[cleanup_start:cleanup_end]
    teardown = cleanup.index('down_ruikekkk_scenario.sh')
    restore_a = cleanup.index('restore_topology_file "${TOPOLOGY_A}"')
    restore_b = cleanup.index('restore_topology_file "${TOPOLOGY_B}"')
    assert teardown < restore_a < restore_b
    assert 'cp -p -- "${TOPOLOGY_A}"' in runner
    assert 'cp -p -- "${TOPOLOGY_B}"' in runner
    assert 'git checkout' not in runner
    assert 'git restore' not in runner


def main() -> int:
    assert suite.SUITE == generator.SUITE
    assert suite.WIRE_VERSION == 3
    assert suite.BLOOM_BITS == 1024
    assert suite.BLOOM_HASH_COUNT == 3
    assert suite.base.bootstrap_replacement_allowed([]) is False
    assert suite.base.bootstrap_replacement_allowed([Path("run001")]) is False
    check_generator()
    check_trace_analyzer()
    check_failure_diagnostic()
    check_truthful_analysis_schema()
    check_full_raw_request_accounting()
    check_postfix_commit_gate()
    check_runner_provenance_and_topology_restore_contract()
    print("federation-v3 bf1024-h3 staged-fallback harness tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
