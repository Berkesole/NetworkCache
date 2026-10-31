#!/usr/bin/env python3
"""Drive one globally closed-loop latency-v3 schedule through shared files."""

from __future__ import annotations

import argparse
import csv
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


RESULT_COLUMNS = [
    "request_index", "phase", "round", "bin", "key", "key_rank", "requester", "owner",
    "requester_role", "owner_role", "original_owner_overlay_hops",
    "overlay_edge_physical_signature", "expected_value_bytes", "expected_fnv1a64",
    "status", "actual_value_bytes", "actual_fnv1a64", "request_start_mono_ns",
    "app_receive_mono_ns", "end_to_end_latency_ns", "packet_request_id", "via",
    "schedule_sha256", "provider_hit_mono_ns", "discovery_latency_ns",
    "return_path_latency_ns", "quiescence_ack_count", "quiescence_stable_ms",
]


def read_tsv(path: Path) -> List[Dict[str, str]]:
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
        time.sleep(0.01)
    return path.exists()


def parse_completion(path: Path) -> Dict[str, str]:
    fields = path.read_text(encoding="utf-8").rstrip("\n").split("\t")
    if len(fields) < 11:
        raise ValueError(f"invalid completion {path}")
    names = ["request_index", "node", "key", "status", "actual_value_bytes",
             "actual_fnv1a64", "request_start_mono_ns", "app_receive_mono_ns",
             "packet_request_id", "via", "schedule_sha256", "provider_hit_mono_ns",
             "cached_route_node"]
    return dict(zip(names, fields))


def write_results(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in RESULT_COLUMNS} for row in rows)


def request_group(row: Dict[str, str]) -> Tuple[str, str]:
    phase = row.get("phase", "")
    round_index = row.get("round", "")
    group = (round_index if phase == "measurement" and round_index not in ("", "0")
             else row.get("bin", ""))
    return phase, group or "0"


def append_snapshot_rows(paths: List[Path], output: Path) -> None:
    rows: List[Dict[str, str]] = []
    columns: Optional[List[str]] = None
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if columns is None:
                columns = list(reader.fieldnames or [])
            rows.extend(reader)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns or [], delimiter="\t")
        if handle.tell() == 0:
            writer.writeheader()
        writer.writerows(rows)


def take_snapshot(control: Path, snapshot_id: int, phase: str, group: str,
                  host_count: int, timeout_s: int, output_dir: Path) -> bool:
    stem = f"snapshot-{snapshot_id:03d}"
    marker_text = f"{stem}\t{phase}\t{group}\n"
    atomic_write(control / "occupancy-markers" / f"{stem}.tsv", marker_text)
    atomic_write(control / "protocol-state-markers" / f"{stem}.tsv", marker_text)
    deadline = time.monotonic() + timeout_s
    occupancy = [control / "occupancy" / f"{stem}-node{node}.tsv"
                 for node in range(1, host_count + 1)]
    protocol = [control / "protocol-state" / f"{stem}-node{node}.tsv"
                for node in range(1, host_count + 1)]
    while time.monotonic() < deadline:
        errors = list((control / "occupancy").glob(f"{stem}-node*.error"))
        if errors:
            return False
        if all(path.exists() for path in occupancy + protocol):
            append_snapshot_rows(occupancy, output_dir / "cache_occupancy.tsv")
            append_snapshot_rows(protocol, output_dir / "sync_bloom_state.tsv")
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--control-dir", required=True, type=Path)
    parser.add_argument("--host-count", required=True, type=int)
    parser.add_argument("--method", required=True, choices=["nc", "known-owner", "tcp-direct"])
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--think-ms", type=int, default=100)
    parser.add_argument("--quiescence-timeout-ms", type=int, default=15000)
    parser.add_argument("--quiescence-stable-ms", type=int, default=500)
    parser.add_argument("--ready-timeout-s", type=int, default=60)
    parser.add_argument("--stop-timeout-s", type=int, default=30)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--snapshot-host-count", type=int, default=0)
    parser.add_argument("--snapshot-timeout-s", type=int, default=60)
    parser.add_argument("--snapshot-output-dir", type=Path)
    args = parser.parse_args()

    schedule = read_tsv(args.schedule)
    control = args.control_dir
    for name in ("commands", "completions", "ready", "quiesce", "quiesced", "stopped"):
        (control / name).mkdir(parents=True, exist_ok=True)
    if args.snapshot_host_count:
        for name in ("occupancy-markers", "occupancy", "protocol-state-markers", "protocol-state"):
            (control / name).mkdir(parents=True, exist_ok=True)
    ready_deadline = time.monotonic() + args.ready_timeout_s
    for node in range(1, args.host_count + 1):
        if not wait_for(control / "ready" / f"node{node}.ready", ready_deadline):
            write_results(args.output, [])
            return 2

    output: List[Dict[str, str]] = []
    failed = False
    previous_end = 0
    snapshot_id = 0
    previous_group: Optional[Tuple[str, str]] = None
    try:
        if args.snapshot_host_count:
            snapshot_id += 1
            if not take_snapshot(control, snapshot_id, "ready", "0", args.snapshot_host_count,
                                 args.snapshot_timeout_s, args.snapshot_output_dir or args.output.parent):
                failed = True
                return 2
        for request in schedule:
            group = request_group(request)
            if previous_group is not None and group != previous_group and args.snapshot_host_count:
                snapshot_id += 1
                if not take_snapshot(control, snapshot_id, previous_group[0], previous_group[1],
                                     args.snapshot_host_count, args.snapshot_timeout_s,
                                     args.snapshot_output_dir or args.output.parent):
                    failed = True
                    break
            previous_group = group
            index = request["request_index"]
            requester = int(request["requester"])
            completion_path = control / "completions" / f"{index}.tsv"
            command = "\t".join([
                index, args.method, request["key"], request["owner"],
                request["value_size_bytes"], request["expected_fnv1a64"],
                request["schedule_sha256"],
            ]) + "\n"
            atomic_write(control / "commands" / f"node{requester}" / f"{int(index):06d}.tsv", command)
            deadline = time.monotonic() + args.timeout_ms / 1000.0
            row = dict(request)
            row["expected_value_bytes"] = request["value_size_bytes"]
            if not wait_for(completion_path, deadline):
                row["status"] = "timeout"
                output.append(row)
                failed = True
                break
            completion = parse_completion(completion_path)
            row.update(completion)
            start = int(completion["request_start_mono_ns"])
            end = int(completion["app_receive_mono_ns"])
            row["end_to_end_latency_ns"] = str(end - start if end >= start else -1)
            # TCP Direct explicitly reports the remote server timestamp in the
            # final completion field. The NetworkCache node uses that legacy
            # field for cached-route metadata; its cross-node decomposition is
            # reconstructed from event traces instead.
            provider_hit = (int(completion.get("provider_hit_mono_ns") or 0)
                            if args.method == "tcp-direct" else 0)
            if args.method != "tcp-direct":
                row["provider_hit_mono_ns"] = ""
            if provider_hit:
                row["discovery_latency_ns"] = str(provider_hit - start)
                row["return_path_latency_ns"] = str(end - provider_hit)
                if not (start <= provider_hit <= end):
                    row["status"] = "clock_order"
            if previous_end and start < previous_end:
                row["status"] = "overlap"
            previous_end = end
            output.append(row)
            if row["status"] != "ok" or completion["schedule_sha256"] != request["schedule_sha256"]:
                failed = True
                break
            quiesce = control / "quiesce" / f"{int(index):06d}.marker"
            atomic_write(quiesce, f"{index}\n")
            quiesce_deadline = time.monotonic() + args.quiescence_timeout_ms / 1000.0
            acknowledgements = [control / "quiesced" / f"{int(index):06d}-node{node}.ack"
                                for node in range(1, args.host_count + 1)]
            stable = False
            while time.monotonic() < quiesce_deadline:
                if not all(path.exists() for path in acknowledgements):
                    time.sleep(0.01)
                    continue
                try:
                    snapshots = [(path.stat().st_mtime_ns, path.read_text()) for path in acknowledgements]
                except FileNotFoundError:
                    continue
                time.sleep(args.quiescence_stable_ms / 1000.0)
                try:
                    current = [(path.stat().st_mtime_ns, path.read_text()) for path in acknowledgements]
                except FileNotFoundError:
                    continue
                if snapshots == current:
                    stable = True
                    break
            if not stable:
                row["status"] = "quiescence_timeout"
                failed = True
                break
            row["quiescence_ack_count"] = str(len(acknowledgements))
            row["quiescence_stable_ms"] = str(args.quiescence_stable_ms)
            quiesce.unlink(missing_ok=True)
            for path in acknowledgements:
                path.unlink(missing_ok=True)
            time.sleep(args.think_ms / 1000.0)
        if not failed and previous_group is not None and args.snapshot_host_count:
            snapshot_id += 1
            if not take_snapshot(control, snapshot_id, previous_group[0], previous_group[1],
                                 args.snapshot_host_count, args.snapshot_timeout_s,
                                 args.snapshot_output_dir or args.output.parent):
                failed = True
    finally:
        atomic_write(control / "stop", "stop\n")
        stop_deadline = time.monotonic() + args.stop_timeout_s
        for node in range(1, args.host_count + 1):
            if not wait_for(control / "stopped" / f"node{node}.stopped", stop_deadline):
                failed = True
        write_results(args.output, output)
    return 2 if failed or len(output) != len(schedule) else 0


if __name__ == "__main__":
    raise SystemExit(main())
