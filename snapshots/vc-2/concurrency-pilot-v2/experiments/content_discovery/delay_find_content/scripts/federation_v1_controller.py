#!/usr/bin/env python3
"""Closed-loop controller for one federation-v1 run."""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path


COLUMNS = ["request_index", "phase", "round", "direction", "key", "key_rank",
           "requester", "owner", "requester_cluster", "owner_cluster",
           "expected_value_bytes", "expected_fnv1a64", "status", "actual_value_bytes",
           "actual_fnv1a64", "request_start_mono_ns", "app_receive_mono_ns",
           "end_to_end_latency_ns", "packet_request_id", "via", "schedule_sha256",
           "quiescence_ack_count", "quiescence_stable_ms", "federation_ready_ns",
           "activation_to_request_start_ns", "activation_to_remote_data_ns", "cached_route_node"]


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8"); temporary.replace(path)


def wait_for(path: Path, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if path.exists(): return True
        time.sleep(.01)
    return path.exists()


def completion(path: Path) -> dict[str, str]:
    fields = path.read_text().rstrip("\n").split("\t")
    names = ["request_index", "node", "key", "status", "actual_value_bytes",
             "actual_fnv1a64", "request_start_mono_ns", "app_receive_mono_ns",
             "packet_request_id", "via", "schedule_sha256", "cached_route_node"]
    if len(fields) < len(names): raise ValueError(f"invalid completion {path}")
    return dict(zip(names, fields))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS); writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in COLUMNS} for row in rows)


def quiesce(control: Path, index: str, nodes: int, timeout_ms: int, stable_ms: int) -> bool:
    marker = control / "quiesce" / f"{int(index):06d}.marker"
    atomic_write(marker, index + "\n")
    paths = [control / "quiesced" / f"{int(index):06d}-node{node}.ack" for node in range(1, nodes + 1)]
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if not all(path.exists() for path in paths): time.sleep(.01); continue
        snapshots = [(p.stat().st_mtime_ns, p.read_text()) for p in paths]
        time.sleep(stable_ms / 1000)
        if snapshots == [(p.stat().st_mtime_ns, p.read_text()) for p in paths]:
            marker.unlink(missing_ok=True)
            for path in paths: path.unlink(missing_ok=True)
            return True
    return False


def activate(control: Path, schedule_hash: str, timeout_s: int) -> tuple[int, int]:
    issued = time.monotonic_ns()
    for node, peer in ((1, 33), (33, 1)):
        peer_link = "172.31.16.2" if node == 1 else "172.31.16.1"
        atomic_write(control / "activation" / f"node{node}.tsv",
                     f"{peer}\t10.90.0.{peer}\t{44000+peer}\truntime-local-bootstrap\t{peer_link}\t{schedule_hash}\n")
    deadline = time.monotonic() + timeout_s
    acknowledgements = []
    for node in (1, 33):
        path = control / "activated" / f"node{node}.tsv"
        if not wait_for(path, deadline): raise RuntimeError(f"activation timeout node{node}")
        fields = path.read_text().rstrip("\n").split("\t")
        if len(fields) < 4 or fields[2] != "ok": raise RuntimeError(f"activation rejected node{node}: {fields}")
        acknowledgements.append(int(fields[3]))
    return issued, max(acknowledgements)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--control-dir", required=True, type=Path)
    parser.add_argument("--method", choices=("nc", "known-owner", "tcp-direct"), required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-ms", type=int, default=8000)
    parser.add_argument("--quiescence-timeout-ms", type=int, default=20000)
    parser.add_argument("--stable-ms", type=int, default=500)
    args = parser.parse_args()
    schedule = read_tsv(args.schedule); control = args.control_dir
    for name in ("commands", "completions", "ready", "quiesce", "quiesced", "stopped", "activation", "activated"):
        (control / name).mkdir(parents=True, exist_ok=True)
    ready_deadline = time.monotonic() + 180
    for node in range(1, 65):
        if not wait_for(control / "ready" / f"node{node}.ready", ready_deadline): return 2
    results: list[dict[str, str]] = []; failed = False; federation_ready = 0
    activation_issued = 0; previous_end = 0
    try:
        for request in schedule:
            if request["phase"] == "measurement" and federation_ready == 0:
                # The final warm-up quiescence gate is the pre-activation isolation barrier.
                activation_issued, federation_ready = activate(control, request["schedule_sha256"], 30)
            index = request["request_index"]; requester = int(request["requester"])
            path = control / "completions" / f"{index}.tsv"
            atomic_write(control / "commands" / f"node{requester}" / f"{int(index):06d}.tsv",
                         "\t".join([index, args.method, request["key"], request["owner"],
                                    request["value_size_bytes"], request["expected_fnv1a64"],
                                    request["schedule_sha256"]]) + "\n")
            row = dict(request); row["expected_value_bytes"] = request["value_size_bytes"]
            if not wait_for(path, time.monotonic() + args.timeout_ms / 1000):
                row["status"] = "timeout"; results.append(row); failed = True; break
            actual = completion(path); row.update(actual)
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
            if not quiesce(control, index, 64, args.quiescence_timeout_ms, args.stable_ms):
                row["status"] = "quiescence_timeout"; failed = True; break
            row["quiescence_ack_count"] = "64"; row["quiescence_stable_ms"] = str(args.stable_ms)
            time.sleep(.1)
    except Exception as exc:
        failed = True
        atomic_write(control / "controller-error.txt", repr(exc) + "\n")
    finally:
        atomic_write(control / "stop", "stop\n")
        stop_deadline = time.monotonic() + 60
        for node in range(1, 65):
            if not wait_for(control / "stopped" / f"node{node}.stopped", stop_deadline): failed = True
        write_csv(args.output, results)
        (args.output.parent / "activation.json").write_text(json.dumps({
            "activation_command_issued_ns": activation_issued,
            "federation_ready_ns": federation_ready,
            "activation_apply_latency_ns": federation_ready - activation_issued if federation_ready else None,
        }, indent=2, sort_keys=True) + "\n")
    return 2 if failed or len(results) != len(schedule) else 0


if __name__ == "__main__": raise SystemExit(main())
