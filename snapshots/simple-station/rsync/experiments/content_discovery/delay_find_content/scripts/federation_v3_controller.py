#!/usr/bin/env python3
"""Closed-loop controller for one large-catalog federation-v3 pilot."""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path

import federation_v2_controller as v2
from federation_v3_cache_probe import COLUMNS as CACHE_COLUMNS


RESULT_COLUMNS = [
    "request_index", "phase", "bin", "round", "request_class", "direction", "key",
    "key_rank", "owner_local_rank", "key_u", "bloom_slot", "bloom_positions", "requester", "owner",
    "requester_cluster", "owner_cluster", "original_owner_overlay_hops",
    "overlay_edge_physical_signature", "expected_value_bytes", "expected_fnv1a64", "status",
    "actual_value_bytes", "actual_fnv1a64", "request_start_mono_ns", "app_receive_mono_ns",
    "end_to_end_latency_ns", "discovery_latency_ns", "return_path_latency_ns",
    "packet_request_id", "via", "schedule_sha256",
    "quiescence_ack_count", "quiescence_stable_ms", "federation_ready_ns",
    "activation_to_request_start_ns", "activation_to_remote_data_ns", "cached_route_node",
]
SNAPSHOT_PRE = {2, 4, 6, 8}
SNAPSHOT_POST = {1, 2, 4, 6, 8, 10, 12, 14, 16}


def append_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    existing = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle: existing = list(csv.DictReader(handle))
    v2.write_csv(path, columns, existing + rows)


def snapshot(control: Path, root: Path, snapshot_id: str, phase: str, bin_index: int,
             timeout_s: int = 90) -> None:
    marker_text = f"{snapshot_id}\t{phase}\t{bin_index}\n"
    v2.atomic_write(control / "occupancy-markers" / f"{snapshot_id}.tsv", marker_text)
    v2.atomic_write(control / "protocol-state-markers" / f"{snapshot_id}.tsv", marker_text)
    cache_paths = [control / "occupancy" / f"{snapshot_id}-node{node}.tsv" for node in range(1, 65)]
    state_paths = [control / "protocol-state" / f"{snapshot_id}-node{node}.tsv" for node in range(1, 65)]
    deadline = time.monotonic() + timeout_s
    for path in cache_paths + state_paths:
        if not v2.wait_for(path, deadline):
            raise RuntimeError(f"snapshot timeout {snapshot_id}: {path}")
    cache_rows = []
    for path in cache_paths:
        values = path.read_text().rstrip("\n").split("\t")
        if len(values) != len(CACHE_COLUMNS): raise RuntimeError(f"invalid cache snapshot {path}")
        cache_rows.append(dict(zip(CACHE_COLUMNS, values)))
    state_rows = []
    for path in state_paths:
        with path.open(newline="", encoding="utf-8") as handle: state_rows.extend(csv.DictReader(handle, delimiter="\t"))
    append_csv(root / "cache_occupancy.csv", CACHE_COLUMNS, cache_rows)
    append_csv(root / "sync_bloom_state.csv",
               ["snapshot_id", "phase", "bin", "node", "neighbor", "bits_set", "bit_count", "occupancy"], state_rows)


def should_snapshot(phase: str, bin_index: int) -> bool:
    if phase == "pre":
        return bin_index in SNAPSHOT_PRE
    # Preserve the frozen 16-bin cadence and sample every four bins in extended
    # convergence pilots. Existing workloads therefore produce identical
    # snapshot sets, while a long pilot can observe continuing mechanism drift.
    return bin_index in SNAPSHOT_POST or (bin_index > 16 and bin_index % 4 == 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--isolation-probes", required=True, type=Path)
    parser.add_argument("--control-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--method", choices=("nc", "known-owner", "tcp-direct"), default="nc")
    parser.add_argument("--skip-cache-probes", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=8000)
    parser.add_argument("--quiescence-timeout-ms", type=int, default=20000)
    parser.add_argument("--stable-ms", type=int, default=500)
    args = parser.parse_args()
    schedule = v2.read_tsv(args.schedule); probes = v2.read_tsv(args.isolation_probes)
    control = args.control_dir; root = args.output.parent
    for name in ("commands", "completions", "ready", "quiesce", "quiesced", "stopped", "activation",
                 "activated", "occupancy-markers", "occupancy", "protocol-state-markers", "protocol-state",
                 "cancel", "cancelled"):
        (control / name).mkdir(parents=True, exist_ok=True)
    ready_deadline = time.monotonic() + 300
    for node in range(1, 65):
        if not v2.wait_for(control / "ready" / f"node{node}.ready", ready_deadline): return 2
    results = []; failed = False; federation_ready = 0; activation_issued = 0; previous_end = 0
    current_group: tuple[str, int] | None = None
    try:
        if not args.skip_cache_probes:
            snapshot(control, root, "ready", "ready", 0)
        for request in schedule:
            group = (request["phase"], int(request["bin"]))
            if (not args.skip_cache_probes and current_group and group != current_group
                    and should_snapshot(*current_group)):
                snapshot(control, root, f"{current_group[0]}-bin{current_group[1]:02d}", *current_group)
            if request["phase"] == "post" and federation_ready == 0:
                if not args.skip_cache_probes:
                    snapshot(control, root, "pre-t0", "pre-t0", 0)
                v2.run_isolation_probes(probes, control, args.method, 8000,
                                        args.quiescence_timeout_ms, args.stable_ms,
                                        root / "isolation_results.csv")
                activation_issued, federation_ready = v2.activate(control, request["schedule_sha256"], 30)
                if not args.skip_cache_probes:
                    snapshot(control, root, "post-t0", "post-t0", 0)
            current_group = group
            index = int(request["request_index"]); requester = int(request["requester"])
            completion_path = control / "completions" / f"{index}.tsv"
            v2.atomic_write(control / "commands" / f"node{requester}" / f"{index:06d}.tsv",
                            "\t".join([str(index), args.method, request["key"], request["owner"],
                                      request["value_size_bytes"], request["expected_fnv1a64"],
                                      request["schedule_sha256"]]) + "\n")
            row = dict(request); row["expected_value_bytes"] = request["value_size_bytes"]
            if not v2.wait_for(completion_path, time.monotonic() + args.timeout_ms / 1000):
                row["status"] = "timeout"; results.append(row); failed = True; break
            actual = v2.completion(completion_path); row.update(actual)
            start, end = int(actual["request_start_mono_ns"]), int(actual["app_receive_mono_ns"])
            row["end_to_end_latency_ns"] = str(end - start)
            if previous_end and start < previous_end: row["status"] = "overlap"
            previous_end = end
            if federation_ready:
                row["federation_ready_ns"] = str(federation_ready)
                row["activation_to_request_start_ns"] = str(start - federation_ready)
                row["activation_to_remote_data_ns"] = str(end - federation_ready)
            results.append(row)
            if row["status"] != "ok" or actual["schedule_sha256"] != request["schedule_sha256"]:
                failed = True; break
            if not v2.quiesce(control, index, 64, args.quiescence_timeout_ms, args.stable_ms):
                row["status"] = "quiescence_timeout"; failed = True; break
            row["quiescence_ack_count"] = "64"; row["quiescence_stable_ms"] = str(args.stable_ms)
            time.sleep(.1)
        if not args.skip_cache_probes and current_group and should_snapshot(*current_group):
            snapshot(control, root, f"{current_group[0]}-bin{current_group[1]:02d}", *current_group)
        if not args.skip_cache_probes:
            snapshot(control, root, "final", "final", 0)
    except Exception as exc:
        failed = True; v2.atomic_write(control / "controller-error.txt", repr(exc) + "\n")
    finally:
        v2.atomic_write(control / "stop", "stop\n")
        deadline = time.monotonic() + 60
        for node in range(1, 65):
            if not v2.wait_for(control / "stopped" / f"node{node}.stopped", deadline): failed = True
        v2.write_csv(args.output, RESULT_COLUMNS, results)
        (root / "activation.json").write_text(json.dumps({"activation_command_issued_ns": activation_issued,
             "federation_ready_ns": federation_ready,
             "activation_apply_latency_ns": federation_ready - activation_issued if federation_ready else None}, indent=2) + "\n")
        completed = sum(row.get("status") == "ok" for row in results)
        (root / "completion_summary.json").write_text(json.dumps({"scheduled": len(schedule), "issued": len(results),
             "completed": completed, "completion_rate_issued": completed / len(results) if results else 0,
             "completion_rate_schedule": completed / len(schedule)}, indent=2) + "\n")
    return 2 if failed or len(results) != len(schedule) else 0


if __name__ == "__main__": raise SystemExit(main())
