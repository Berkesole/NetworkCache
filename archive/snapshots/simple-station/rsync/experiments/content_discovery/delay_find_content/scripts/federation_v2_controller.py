#!/usr/bin/env python3
"""Closed-loop controller for one federation-v2 run."""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path


COLUMNS = ["request_index", "phase", "round", "request_class", "direction", "key", "key_rank",
           "requester", "owner", "requester_cluster", "owner_cluster", "expected_value_bytes",
           "original_owner_overlay_hops", "overlay_edge_physical_signature",
           "expected_fnv1a64", "status", "actual_value_bytes", "actual_fnv1a64",
           "request_start_mono_ns", "app_receive_mono_ns", "end_to_end_latency_ns",
           "packet_request_id", "via", "schedule_sha256", "quiescence_ack_count",
           "quiescence_stable_ms", "federation_ready_ns", "activation_to_request_start_ns",
           "activation_to_remote_data_ns", "cached_route_node"]
OCCUPANCY_COLUMNS = ["snapshot_id", "phase", "round", "node", "cluster", "ts_mono_ns",
                     "db_keys", "logical_value_bytes", "original_keys", "original_logical_bytes",
                     "cached_copy_keys", "cached_copy_logical_bytes", "key_memory_usage_bytes",
                     "redis_used_memory_bytes"]


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def wait_for(path: Path, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(.01)
    return path.exists()


def completion(path: Path) -> dict[str, str]:
    fields = path.read_text().rstrip("\n").split("\t")
    names = ["request_index", "node", "key", "status", "actual_value_bytes",
             "actual_fnv1a64", "request_start_mono_ns", "app_receive_mono_ns",
             "packet_request_id", "via", "schedule_sha256", "cached_route_node"]
    if len(fields) < len(names):
        raise ValueError(f"invalid completion {path}")
    return dict(zip(names, fields))


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in columns} for row in rows)


def acknowledgement_snapshot(paths: list[Path]) -> list[tuple[int, str]] | None:
    """Return one coherent ACK snapshot, or None if an ACK is withdrawn.

    Nodes deliberately remove their quiescence ACK when late protocol activity
    arrives.  Existence checks followed by stat/read therefore race with that
    withdrawal.  A disappearing ACK means "not stable yet", not a controller
    failure.
    """
    snapshot: list[tuple[int, str]] = []
    try:
        for path in paths:
            snapshot.append((path.stat().st_mtime_ns, path.read_text()))
    except OSError:
        return None
    return snapshot


def quiesce(control: Path, index: int, nodes: int, timeout_ms: int, stable_ms: int) -> bool:
    marker = control / "quiesce" / f"{index:06d}.marker"
    atomic_write(marker, str(index) + "\n")
    paths = [control / "quiesced" / f"{index:06d}-node{node}.ack" for node in range(1, nodes + 1)]
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        first = acknowledgement_snapshot(paths)
        if first is None:
            time.sleep(.01)
            continue
        time.sleep(stable_ms / 1000)
        second = acknowledgement_snapshot(paths)
        if second is not None and first == second:
            marker.unlink(missing_ok=True)
            for path in paths:
                path.unlink(missing_ok=True)
            return True
    return False


def activate(control: Path, schedule_hash: str, timeout_s: int) -> tuple[int, int]:
    issued = time.monotonic_ns()
    for node, peer in ((1, 33), (33, 1)):
        peer_link = "172.31.16.2" if node == 1 else "172.31.16.1"
        atomic_write(control / "activation" / f"node{node}.tsv",
                     f"{peer}\t10.90.0.{peer}\t{44000 + peer}\truntime-local-bootstrap\t"
                     f"{peer_link}\t{schedule_hash}\n")
    deadline = time.monotonic() + timeout_s
    acknowledgements = []
    for node in (1, 33):
        path = control / "activated" / f"node{node}.tsv"
        if not wait_for(path, deadline):
            raise RuntimeError(f"activation timeout node{node}")
        fields = path.read_text().rstrip("\n").split("\t")
        if len(fields) < 4 or fields[2] != "ok":
            raise RuntimeError(f"activation rejected node{node}: {fields}")
        acknowledgements.append(int(fields[3]))
    return issued, max(acknowledgements)


def occupancy_snapshot(control: Path, output: Path, snapshot_id: str, phase: str,
                       round_index: int, enabled: bool, timeout_s: int = 30) -> None:
    if not enabled:
        return
    marker = control / "occupancy-markers" / f"{snapshot_id}.tsv"
    atomic_write(marker, f"{snapshot_id}\t{phase}\t{round_index}\n")
    paths = [control / "occupancy" / f"{snapshot_id}-node{node}.tsv" for node in range(1, 65)]
    deadline = time.monotonic() + timeout_s
    for path in paths:
        if not wait_for(path, deadline):
            errors = sorted((control / "occupancy").glob(f"{snapshot_id}-node*.error"))
            raise RuntimeError(f"occupancy timeout {snapshot_id}; errors={errors}")
    rows = []
    for path in paths:
        values = path.read_text().rstrip("\n").split("\t")
        if len(values) != len(OCCUPANCY_COLUMNS):
            raise RuntimeError(f"invalid occupancy row {path}")
        rows.append(dict(zip(OCCUPANCY_COLUMNS, values)))
    existing = []
    if output.exists():
        with output.open(newline="", encoding="utf-8") as handle:
            existing = list(csv.DictReader(handle))
    write_csv(output, OCCUPANCY_COLUMNS, existing + rows)


def run_isolation_probes(probes: list[dict[str, str]], control: Path, method: str,
                         timeout_ms: int, quiescence_timeout_ms: int, stable_ms: int,
                         output: Path) -> None:
    results: list[dict[str, str]] = []
    columns = ["probe_index", "direction", "requester", "owner", "key", "status",
               "timeout_ms", "completion_present", "quiescence_ack_count"]
    for probe in probes:
        synthetic_index = 900000 + int(probe["probe_index"])
        completion_path = control / "completions" / f"{synthetic_index}.tsv"
        requester = int(probe["requester"])
        atomic_write(control / "commands" / f"node{requester}" / f"{synthetic_index:06d}.tsv",
                     "\t".join([str(synthetic_index), method, probe["key"], probe["owner"],
                                probe["value_size_bytes"], probe["expected_fnv1a64"],
                                probe["schedule_sha256"]]) + "\n")
        completion_present = wait_for(completion_path, time.monotonic() + timeout_ms / 1000)
        unexpected = False
        if completion_present:
            actual = completion(completion_path)
            unexpected = actual.get("status") == "ok" or int(actual.get("actual_value_bytes") or 0) > 0
        elif method != "tcp-direct":
            cancel = control / "cancel" / f"{synthetic_index}-node{requester}.marker"
            atomic_write(cancel, f"{synthetic_index}\n")
            cancelled = control / "cancelled" / f"{synthetic_index}-node{requester}.tsv"
            if not wait_for(cancelled, time.monotonic() + 5):
                write_csv(output, columns, results)
                raise RuntimeError(f"isolation probe cancel timeout node{requester}")
        quiet = quiesce(control, synthetic_index, 64, quiescence_timeout_ms, stable_ms)
        row = {key: probe.get(key, "") for key in columns}
        row.update({"status": "unexpected_data" if unexpected else ("ok" if quiet else "quiescence_timeout"),
                    "timeout_ms": str(timeout_ms), "completion_present": "1" if completion_present else "0",
                    "quiescence_ack_count": "64" if quiet else "0"})
        results.append(row)
        if unexpected or not quiet:
            write_csv(output, columns, results)
            raise RuntimeError(f"pre-activation isolation probe failed: {row}")
    write_csv(output, columns, results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--isolation-probes", required=True, type=Path)
    parser.add_argument("--control-dir", required=True, type=Path)
    parser.add_argument("--method", choices=("nc", "known-owner", "tcp-direct"), required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-ms", type=int, default=8000)
    parser.add_argument("--isolation-timeout-ms", type=int, default=8000)
    parser.add_argument("--quiescence-timeout-ms", type=int, default=20000)
    parser.add_argument("--stable-ms", type=int, default=500)
    parser.add_argument("--cache-probes", action="store_true")
    args = parser.parse_args()

    schedule = read_tsv(args.schedule)
    probes = read_tsv(args.isolation_probes)
    control = args.control_dir
    for name in ("commands", "completions", "ready", "quiesce", "quiesced", "stopped",
                 "activation", "activated", "occupancy-markers", "occupancy", "cancel", "cancelled"):
        (control / name).mkdir(parents=True, exist_ok=True)
    ready_deadline = time.monotonic() + 180
    for node in range(1, 65):
        if not wait_for(control / "ready" / f"node{node}.ready", ready_deadline):
            return 2

    results: list[dict[str, str]] = []
    failed = False
    federation_ready = 0
    activation_issued = 0
    previous_end = 0
    occupancy_output = args.output.parent / "cache_occupancy.csv"
    try:
        occupancy_snapshot(control, occupancy_output, "ready", "ready", 0, args.cache_probes)
        current_group: tuple[str, int] | None = None
        for request in schedule:
            group = (request["phase"], int(request["round"]))
            if current_group and group != current_group:
                occupancy_snapshot(control, occupancy_output,
                                   f"{current_group[0]}-round{current_group[1]:02d}",
                                   current_group[0], current_group[1], args.cache_probes)
            if request["phase"] == "post" and federation_ready == 0:
                occupancy_snapshot(control, occupancy_output, "pre-t0", "pre-t0", 0, args.cache_probes)
                run_isolation_probes(probes, control, args.method, args.isolation_timeout_ms,
                                     args.quiescence_timeout_ms, args.stable_ms,
                                     args.output.parent / "isolation_results.csv")
                activation_issued, federation_ready = activate(control, request["schedule_sha256"], 30)
                occupancy_snapshot(control, occupancy_output, "post-t0", "post-t0", 0, args.cache_probes)
            current_group = group

            index = int(request["request_index"])
            requester = int(request["requester"])
            path = control / "completions" / f"{index}.tsv"
            atomic_write(control / "commands" / f"node{requester}" / f"{index:06d}.tsv",
                         "\t".join([str(index), args.method, request["key"], request["owner"],
                                    request["value_size_bytes"], request["expected_fnv1a64"],
                                    request["schedule_sha256"]]) + "\n")
            row = dict(request)
            row["expected_value_bytes"] = request["value_size_bytes"]
            if not wait_for(path, time.monotonic() + args.timeout_ms / 1000):
                row["status"] = "timeout"
                results.append(row)
                failed = True
                break
            actual = completion(path)
            row.update(actual)
            start = int(actual["request_start_mono_ns"])
            end = int(actual["app_receive_mono_ns"])
            row["end_to_end_latency_ns"] = str(end - start)
            if previous_end and start < previous_end:
                row["status"] = "overlap"
            previous_end = end
            if federation_ready:
                row["federation_ready_ns"] = str(federation_ready)
                row["activation_to_request_start_ns"] = str(start - federation_ready)
                row["activation_to_remote_data_ns"] = str(end - federation_ready)
            results.append(row)
            if row["status"] != "ok" or actual["schedule_sha256"] != request["schedule_sha256"]:
                failed = True
                break
            if not quiesce(control, index, 64, args.quiescence_timeout_ms, args.stable_ms):
                row["status"] = "quiescence_timeout"
                failed = True
                break
            row["quiescence_ack_count"] = "64"
            row["quiescence_stable_ms"] = str(args.stable_ms)
            time.sleep(.1)
        if current_group:
            occupancy_snapshot(control, occupancy_output,
                               f"{current_group[0]}-round{current_group[1]:02d}",
                               current_group[0], current_group[1], args.cache_probes)
        occupancy_snapshot(control, occupancy_output, "final", "final", 0, args.cache_probes)
    except Exception as exc:
        failed = True
        atomic_write(control / "controller-error.txt", repr(exc) + "\n")
    finally:
        atomic_write(control / "stop", "stop\n")
        stop_deadline = time.monotonic() + 60
        for node in range(1, 65):
            if not wait_for(control / "stopped" / f"node{node}.stopped", stop_deadline):
                failed = True
        write_csv(args.output, COLUMNS, results)
        (args.output.parent / "activation.json").write_text(json.dumps({
            "activation_command_issued_ns": activation_issued,
            "federation_ready_ns": federation_ready,
            "activation_apply_latency_ns": federation_ready - activation_issued if federation_ready else None,
        }, indent=2, sort_keys=True) + "\n")
        (args.output.parent / "completion_summary.json").write_text(json.dumps({
            "scheduled": len(schedule), "issued": len(results),
            "completed": sum(row.get("status") == "ok" for row in results),
            "completion_rate_issued": (sum(row.get("status") == "ok" for row in results) / len(results)) if results else 0,
            "completion_rate_schedule": sum(row.get("status") == "ok" for row in results) / len(schedule),
        }, indent=2, sort_keys=True) + "\n")
    return 2 if failed or len(results) != len(schedule) else 0


if __name__ == "__main__":
    raise SystemExit(main())
