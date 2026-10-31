#!/usr/bin/env python3
"""Run the isolated 1024-bit/3-hash staged-fallback federation pilots."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import run_federation_v3_bf2048_h3_suite as base


BLOOM_BITS = 1024
BLOOM_HASH_COUNT = 3
WIRE_VERSION = 3
SUITE = "federation-v3-bf1024-h3-fallback-pilot"
RESULTS = Path(os.environ.get(
    "FEDERATION_FALLBACK_RESULTS_ROOT", str(base.EXP / f"results/{SUITE}"),
)).resolve()
REQUIRED_GIT_ANCESTOR = os.environ.get("FEDERATION_FALLBACK_REQUIRED_GIT_ANCESTOR", "")

base.BLOOM_BITS = BLOOM_BITS
base.BLOOM_HASH_COUNT = BLOOM_HASH_COUNT
base.SUITE = SUITE
base.RESULTS = RESULTS
base.GENERATOR = "generate_federation_v3_bf1024_h3_fallback_pilot.py"
base.RUN_ONCE = "run_federation_v3_bf1024_h3_fallback_once.sh"
base.ANALYZER = "analyze_federation_v3_bf1024_h3_fallback_pilot.py"
base.bootstrap_replacement_allowed = lambda attempts: False

PILOTS = base.PILOTS
read_csv = base.read_csv
attempt_dirs = base.attempt_dirs


def commit_satisfies_required(metadata: dict) -> bool:
    """Reject historical runs when a post-fix result namespace requires an ancestor."""
    if not REQUIRED_GIT_ANCESTOR:
        return True
    recorded = str(metadata.get("git_commit", ""))
    if not recorded:
        return False
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", REQUIRED_GIT_ANCESTOR, recorded],
        cwd=base.REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return completed.returncode == 0


def validate(pilot: base.Pilot, root: Path) -> tuple[bool, str]:
    try:
        metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        source = json.loads((root / "schedule_metadata.json").read_text(encoding="utf-8"))
        completion = json.loads((root / "completion_summary.json").read_text(encoding="utf-8"))
        activation = json.loads((root / "activation.json").read_text(encoding="utf-8"))
        schedule = read_csv(root / "schedule.tsv", "\t")
        requests = read_csv(root / "request_results.csv")
        trace = read_csv(root / "trace_breakdown.csv")
        isolation = read_csv(root / "isolation_results.csv")
        occupancy = read_csv(root / "cache_occupancy.csv")
        bloom = read_csv(root / "sync_bloom_state.csv")
    except Exception:
        return False, "missing_or_invalid_files"

    expected_parameters = {
        "wire_version": WIRE_VERSION,
        "bloom_bits": BLOOM_BITS,
        "bloom_hash_count": BLOOM_HASH_COUNT,
        "staged_fallback": True,
        "fallback_delay_ms": 1500,
        "query_table_timeout_ms": 7000,
        "controller_timeout_ms": 8000,
        "max_hop_count": 12,
    }
    if metadata.get("status") != "ok":
        return False, f"run_status_{metadata.get('status', 'missing')}"
    if not commit_satisfies_required(metadata):
        return False, "git_commit_precedes_required_postfix"
    if any(metadata.get(name) != value for name, value in expected_parameters.items()):
        return False, "wire_bloom_or_fallback_parameters"
    if (metadata.get("request_codec_frame_bytes_single_signature") != 162 or
            metadata.get("request_tcp_frame_bytes_single_signature") != 166):
        return False, "request_frame_parameters"
    if (source.get("catalog_size_per_cluster") != pilot.catalog_size or
            source.get("distribution") != pilot.distribution or
            any(source.get(name) != value for name, value in expected_parameters.items())):
        return False, "workload_metadata"
    if (source.get("request_codec_frame_bytes_single_signature") != 162 or
            source.get("request_tcp_frame_bytes_single_signature") != 166):
        return False, "workload_frame_parameters"
    if len(schedule) != 3584 or len(requests) != 3584:
        return False, "request_row_count"
    if any(completion.get(name) != 3584 for name in ("scheduled", "issued", "completed")):
        return False, "completion_summary"
    if (len(isolation) != 2 or
            any(row.get("status") != "ok" or row.get("completion_present") != "0" for row in isolation)):
        return False, "isolation"
    if not activation.get("federation_ready_ns") or activation.get("activation_apply_latency_ns", -1) < 0:
        return False, "activation"
    if {path.stem for path in (root / "activation_acks").glob("*.tsv")} != {"node1", "node33"}:
        return False, "activation_acks"

    expected = {row["request_index"]: row for row in schedule}
    intervals: list[tuple[int, int]] = []
    for row in requests:
        scheduled = expected.get(row.get("request_index", ""))
        if not scheduled or row.get("status") != "ok":
            return False, "request_status"
        if (row.get("actual_value_bytes") != scheduled.get("value_size_bytes") or
                row.get("actual_fnv1a64") != scheduled.get("expected_fnv1a64")):
            return False, "value_integrity"
        if row.get("quiescence_ack_count") != "64":
            return False, "quiescence"
        try:
            start, end = int(row["request_start_mono_ns"]), int(row["app_receive_mono_ns"])
        except (KeyError, TypeError, ValueError):
            return False, "request_clock"
        if end < start:
            return False, "clock_order"
        intervals.append((start, end))
    intervals.sort()
    if any(second[0] < first[1] for first, second in zip(intervals, intervals[1:])):
        return False, "request_overlap"
    if (sum(row["phase"] == "pre" for row in schedule) != 1024 or
            sum(row["request_class"] == "remote" for row in schedule) != 2048 or
            sum(row["request_class"] == "local_control" for row in schedule) != 512):
        return False, "schedule_shape"

    required_trace_columns = {
        "fallback_triggered", "fallback_recovery_latency_ns", "normal_request_frames",
        "fallback_request_frames", "normal_request_bytes", "fallback_request_bytes",
        "immediate_dead_end_fallback_events", "duplicate_request_suppressed_events",
        "query_expired_unsatisfied_events", "application_delivery_events",
    }
    if len(trace) != 3584 or not trace or not required_trace_columns <= set(trace[0]):
        return False, "fallback_trace_shape"
    if sum(row.get("trace_complete") == "1" for row in trace) < 3549:
        return False, "trace_coverage"
    if any(int(row.get("query_expired_unsatisfied_events") or 0) for row in trace):
        return False, "query_expired_unsatisfied"
    if any(int(row.get("application_delivery_events") or 0) != 1 for row in trace):
        return False, "duplicate_application_delivery"
    for bin_index in range(1, 17):
        rows = [row for row in trace if row.get("phase") == "post" and row.get("bin") == str(bin_index)]
        remote = [row for row in rows if row.get("request_class") == "remote"]
        local = [row for row in rows if row.get("request_class") == "local_control"]
        if sum(row.get("trace_complete") == "1" for row in remote) < 127:
            return False, "remote_bin_trace_coverage"
        if sum(row.get("trace_complete") == "1" for row in local) < 31:
            return False, "local_bin_trace_coverage"

    snapshot_ids = {
        "ready", "pre-bin02", "pre-bin04", "pre-bin06", "pre-bin08", "pre-t0", "post-t0",
        "post-bin01", "post-bin02", "post-bin04", "post-bin06", "post-bin08", "post-bin10",
        "post-bin12", "post-bin14", "post-bin16", "final",
    }
    if (len(occupancy) != len(snapshot_ids) * 64 or
            {row.get("snapshot_id") for row in occupancy} != snapshot_ids or
            any(not row.get("redis_used_memory_bytes") for row in occupancy)):
        return False, "occupancy"
    if (not bloom or {row.get("snapshot_id") for row in bloom} != snapshot_ids or
            any(row.get("bit_count") != str(BLOOM_BITS) for row in bloom)):
        return False, "bloom_state"
    logs = "\n".join(path.read_text(errors="replace") for path in (root / "logs").glob("node*.log"))
    if logs.count(f"bloom configuration bits={BLOOM_BITS} hashes={BLOOM_HASH_COUNT}") < 64:
        return False, "node_bloom_parameters"
    if logs.count(f"wire configuration version={WIRE_VERSION}") < 64:
        return False, "node_wire_parameters"
    clock = read_csv(root / "clock_state.tsv")
    if len(clock) != 64 or len({row.get("timens_offsets_sha256") for row in clock}) != 1:
        return False, "clock_gate"
    try:
        sections = (root / "network_state.txt").read_text(errors="replace").split("## node")[1:]
    except OSError:
        return False, "network_gate"
    if len(sections) != 88 or any("mtu 1500" not in section or "delay 2ms" not in section for section in sections):
        return False, "network_gate"
    return True, "ok"


base.validate = validate
run_dir = base.run_dir


if __name__ == "__main__":
    raise SystemExit(base.main())
