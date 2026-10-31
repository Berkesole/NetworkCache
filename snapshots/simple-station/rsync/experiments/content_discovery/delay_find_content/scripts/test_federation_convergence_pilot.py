#!/usr/bin/env python3
"""Deterministic tests for the long-horizon Federation convergence pilot."""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

import analyze_federation_convergence_pilot as analysis
import federation_v2_controller as controller
import generate_federation_convergence_pilot as gen
import generate_federation_v3_pilot as timeline
import run_federation_convergence_pilot as runner


def test_schedule_extension() -> None:
    frozen = timeline.canonical(1)
    explicit = timeline.canonical(1, post_bins=16)
    extended = timeline.canonical(1, post_bins=64)
    assert frozen == explicit
    assert extended[: len(frozen)] == frozen
    assert len(frozen) == 3584
    assert len(extended) == gen.EXPECTED_REQUESTS == 11264


def test_generated_workload() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gen.generate(root)
        gen.validate(root)
        schedule = gen.read_rows(root / "mapped" / gen.SCENARIO / "schedule.tsv")
        assert schedule[1024]["phase"] == "post"
        assert schedule[-1]["bin"] == "64"


def synthetic_batches(*, mechanism_drift: bool) -> list[dict]:
    rows = []
    for batch in range(1, 65):
        latency = 300.0 - min(batch, 32) * 4.0
        for role in ("remote", "local_control"):
            rows.append(
                {
                    "batch": batch,
                    "request_class": role,
                    "requests": 128 if role == "remote" else 32,
                    "end_to_end_latency_ms": latency if role == "remote" else 100.0,
                    "discovery_latency_ms": latency * 0.45,
                    "return_path_latency_ms": latency * 0.55,
                    "preferred_logical_request_events": (
                        2.0 + batch * 0.2 if mechanism_drift else 2.0
                    ),
                    "fallback_logical_request_events": 0.4,
                    "cache_service_fraction_proxy": 0.9,
                    "serving_overlay_hops_proxy": 1.2,
                }
            )
    return rows


def test_convergence_gate() -> None:
    stable = synthetic_batches(mechanism_drift=False)
    report = analysis.convergence(analysis.window_rows(stable), stable)
    assert report["decision"] == "HORIZON_CANDIDATE_FOUND"
    assert report["earliest_recommended_post_batches"] == 40

    drifting = synthetic_batches(mechanism_drift=True)
    report = analysis.convergence(analysis.window_rows(drifting), drifting)
    assert report["decision"] == "NOT_CONVERGED_BY_BATCH_64"
    assert report["earliest_recommended_post_batches"] is None


def test_append_only_attempt_selection_and_unbounded_lifetime() -> None:
    with tempfile.TemporaryDirectory() as directory:
        results = Path(directory)
        first = runner.next_attempt(results)
        assert first.name.endswith("attempt001")
        first.mkdir(parents=True)
        second = runner.next_attempt(results)
        assert second.name.endswith("attempt002")
    source = Path(runner.__file__).read_text(encoding="utf-8")
    lower = (Path(runner.__file__).parent / "run_federation_v3_bf1024_h3_fallback_once.sh").read_text(
        encoding="utf-8"
    )
    assert '"RSYNC_LOOP_ITERATIONS": "2147483647"' in source
    assert 'RSYNC_LOOP_ITERATIONS="${RSYNC_LOOP_ITERATIONS:-100000000}"' in lower
    assert 'reset_ephemeral_shared_path "${SHARED}"' in lower
    assert 'sudo -n rm -rf -- "${target}"' in lower


def test_quiescence_ack_withdrawal_is_retried() -> None:
    with tempfile.TemporaryDirectory() as directory:
        control = Path(directory)
        ack = control / "quiesced" / "000007-node1.ack"
        ack.parent.mkdir(parents=True)
        ack.write_text("7\t1\t100\n", encoding="utf-8")

        def withdraw_then_restore() -> None:
            time.sleep(0.02)
            ack.unlink()
            time.sleep(0.05)
            ack.write_text("7\t1\t200\n", encoding="utf-8")

        worker = threading.Thread(target=withdraw_then_restore)
        worker.start()
        assert controller.quiesce(control, 7, 1, timeout_ms=500, stable_ms=40)
        worker.join()
        assert not (control / "quiesce" / "000007.marker").exists()
        assert not ack.exists()


def main() -> int:
    test_schedule_extension()
    test_generated_workload()
    test_convergence_gate()
    test_append_only_attempt_selection_and_unbounded_lifetime()
    test_quiescence_ack_withdrawal_is_retried()
    print("federation convergence pilot tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
