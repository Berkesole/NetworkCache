#!/usr/bin/env python3
"""Run, resume, select, and validate federation-v2 without touching latency-v3."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_DIR = SCRIPT_DIR.parent
REPO_ROOT = EXP_DIR.parents[2]
RESULTS = EXP_DIR / "results/federation-v2"
TARGET = 2
MAX_ATTEMPTS = 4


@dataclass(frozen=True)
class Config:
    phase: str
    workload: str
    method: str
    slug: str

    def scenario(self, seed: int) -> str:
        return f"kua-dc-v3-federation-v2-{self.workload}-seed{seed:03d}"


CONFIGS = (
    Config("repeated", "repeated", "nc", "repeated-nc"),
    Config("repeated", "repeated", "known-owner", "repeated-known-owner"),
    Config("repeated", "repeated", "tcp-direct", "repeated-tcp-direct"),
    Config("uniform", "uniform", "nc", "uniform-nc"),
)


def read_csv(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.exists(): return []
    with path.open(newline="", encoding="utf-8") as handle: return list(csv.DictReader(handle, delimiter=delimiter))


def dirs(config: Config, seed: int) -> list[Path]:
    root = RESULTS / "attempts" / config.slug / f"seed{seed:03d}"
    return sorted(path for path in root.glob("attempt[0-9][0-9][0-9]") if path.is_dir()) if root.exists() else []


def validate(config: Config, path: Path) -> tuple[bool, str]:
    try:
        meta = json.loads((path / "metadata.json").read_text())
        activation = json.loads((path / "activation.json").read_text())
        schedule = read_csv(path / "schedule.tsv", "\t")
        rows = read_csv(path / "request_results.csv")
        isolation = read_csv(path / "isolation_results.csv")
        completion = json.loads((path / "completion_summary.json").read_text())
    except Exception: return False, "missing_or_invalid_files"
    if meta.get("status") != "ok" or meta.get("wire_version") != 2: return False, "driver_or_wire"
    if len(schedule) != 320 or len(rows) != 320: return False, "row_count"
    if completion != {"completed": 320, "completion_rate_issued": 1.0,
                       "completion_rate_schedule": 1.0, "issued": 320, "scheduled": 320}:
        return False, "completion_summary"
    if len(isolation) != 2 or any(row.get("status") != "ok" or row.get("quiescence_ack_count") != "64"
                                  for row in isolation):
        return False, "isolation_probe"
    if config.method != "tcp-direct" and any(row.get("completion_present") != "0" for row in isolation):
        return False, "isolation_probe_completion"
    if not activation.get("federation_ready_ns") or activation.get("activation_apply_latency_ns", -1) < 0:
        return False, "activation"
    ack_files = sorted((path / "activation_acks").glob("*.tsv")) if (path / "activation_acks").exists() else []
    if {file.stem for file in ack_files} != {"node1", "node33"}:
        return False, "activation_acks"
    for ack_file in ack_files:
        fields = ack_file.read_text().strip().split("\t")
        if len(fields) < 4 or fields[2] != "ok" or int(fields[3]) <= 0:
            return False, "activation_ack_content"
    clock = read_csv(path / "clock_state.tsv")
    if len(clock) != 64 or len({row.get("timens_offsets_sha256") for row in clock}) != 1:
        return False, "clock_gate"
    by_index = {row["request_index"]: row for row in rows}
    intervals = []
    for expected in schedule:
        actual = by_index.get(expected["request_index"])
        if not actual or actual.get("status") != "ok": return False, "request_status"
        if actual.get("actual_value_bytes") != expected["value_size_bytes"]: return False, "value_size"
        if actual.get("actual_fnv1a64") != expected["expected_fnv1a64"]: return False, "value_hash"
        if actual.get("quiescence_ack_count") != "64": return False, "quiescence"
        start, end = int(actual["request_start_mono_ns"]), int(actual["app_receive_mono_ns"])
        if end < start: return False, "clock_order"
        intervals.append((start, end))
        if expected["phase"] == "post" and int(actual.get("activation_to_remote_data_ns", "-1")) < 0:
            return False, "activation_latency"
    intervals.sort()
    if any(b[0] < a[1] for a, b in zip(intervals, intervals[1:])): return False, "overlap"
    # The immutable schedule itself proves that every pre-t0 request is cluster-local.
    pre = [row for row in schedule if row["phase"] == "pre"]
    remote = [row for row in schedule if row["phase"] == "post" and row["request_class"] == "remote"]
    local_control = [row for row in schedule if row["request_class"] == "local_control"]
    if len(pre) != 128 or len(remote) != 128 or len(local_control) != 64: return False, "schedule_shape"
    if any(row["requester_cluster"] != row["owner_cluster"] for row in pre): return False, "prelink_schedule"
    if config.method != "tcp-direct":
        trace = read_csv(path / "trace_breakdown.csv")
        if len(trace) != 320 or sum(row.get("trace_complete") == "1" for row in trace) < 317:
            return False, "trace_coverage"
        pre_trace = [row for row in trace if row.get("phase") == "pre"]
        if len(pre_trace) != 128 or any(int(row.get("cross_link_request_frames") or 0) or
                                       int(row.get("cross_link_data_frames") or 0) for row in pre_trace):
            return False, "prelink_traffic"
        for round_index in range(1, 9):
            if sum(row.get("phase") == "post" and row.get("request_class") == "remote" and
                   row.get("round") == str(round_index) and row.get("trace_complete") == "1"
                   for row in trace) < 15:
                return False, "round_trace_coverage"
            if sum(row.get("phase") == "post" and row.get("request_class") == "local_control" and
                   row.get("round") == str(round_index) and row.get("trace_complete") == "1"
                   for row in trace) < 7:
                return False, "local_control_trace_coverage"
            for cluster_name in ("A", "B"):
                if sum(row.get("phase") == "pre" and row.get("direction") == f"{cluster_name}_local" and
                       row.get("round") == str(round_index) and row.get("trace_complete") == "1"
                       for row in trace) < 7:
                    return False, "pre_round_trace_coverage"
        logs = "\n".join(file.read_text(errors="replace") for file in (path / "logs").glob("node*.log"))
        if logs.count("cache backend: redis") < 64: return False, "redis_not_ready"
        occupancy = read_csv(path / "cache_occupancy.csv")
        if len(occupancy) != 1280 or len({(row.get("snapshot_id"), row.get("node")) for row in occupancy}) != 1280:
            return False, "occupancy_shape"
        if any(not row.get("cached_copy_keys") or not row.get("cached_copy_logical_bytes")
               or not row.get("redis_used_memory_bytes") for row in occupancy):
            return False, "occupancy_content"
    network = (path / "network_state.txt").read_text(errors="replace") if (path / "network_state.txt").exists() else ""
    sections = network.split("## node")[1:]
    if len(sections) != 88: return False, "network_state_devices"
    for section in sections:
        if "mtu 1500" not in section or "qdisc netem" not in section or "delay 2ms" not in section:
            return False, "network_state_shape"
    if network.count("rate 1Gbit") + network.count("rate 1000Mbit") < 100:
        return False, "network_state_rate"
    generated = path / "generated_network"
    if not generated.exists(): return False, "generated_network_missing"
    boundary_scripts = "\n".join(file.read_text(errors="replace") for file in generated.rglob("node*.sh")
                                  if file.name in {"node1.network.sh", "node33.network.sh"})
    if boundary_scripts.count("ip link set dev eth0 down") < 2: return False, "prelink_interface_state"
    return True, "ok"


def selected(config: Config, seed: int) -> list[Path]:
    return [path for path in dirs(config, seed) if validate(config, path)[0]][:TARGET]


def run_phase(phase: str) -> int:
    subprocess.run([sys.executable, str(SCRIPT_DIR / "generate_federation_v2.py")], check=True)
    block = [config for config in CONFIGS if config.phase == phase]
    built = False
    for attempt_slot in range(1, MAX_ATTEMPTS + 1):
        for seed in range(1, 11):
            order = list(block); random.Random(f"federation-v2-block:{phase}:{seed}:{attempt_slot}").shuffle(order)
            for config in order:
                if len(selected(config, seed)) >= TARGET or len(dirs(config, seed)) >= MAX_ATTEMPTS: continue
                output = RESULTS / "attempts" / config.slug / f"seed{seed:03d}" / f"attempt{len(dirs(config, seed))+1:03d}"
                output.parent.mkdir(parents=True, exist_ok=True)
                env = os.environ.copy()
                if built: env["SKIP_BUILD"] = "1"
                completed = subprocess.run([str(SCRIPT_DIR / "run_federation_v2_once.sh"), config.scenario(seed), config.method, str(output)],
                                           cwd=REPO_ROOT, env=env, check=False)
                if completed.returncode == 3:
                    raise RuntimeError("federation-v2 lab lock is held; no additional attempts were started")
                built = True
    return validate_all(print_status=True)


def write_selection() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = []
    for config in CONFIGS:
        for seed in range(1, 11):
            for index, path in enumerate(selected(config, seed), 1):
                rows.append({"config": config.slug, "workload": config.workload, "method": config.method,
                             "seed": seed, "selection_index": index, "run_dir": str(path.resolve())})
    with (RESULTS / "federation_selection.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["config", "workload", "method", "seed", "selection_index", "run_dir"])
        writer.writeheader(); writer.writerows(rows)
    return len(rows)


def validate_all(print_status: bool = False) -> int:
    ready = 0; status_rows = []
    for config in CONFIGS:
        counts = [len(selected(config, seed)) for seed in range(1, 11)]
        attempts = sum(len(dirs(config, seed)) for seed in range(1, 11))
        state = "READY" if counts == [2] * 10 else "NEEDS_MORE"
        ready += state == "READY"
        status_rows.append({"config": config.slug, "status": state, "selected_runs": sum(counts),
                            "seed_counts": ";".join(map(str, counts)), "attempts": attempts})
        if print_status: print(f"{state:10} {config.slug:30} selected={sum(counts):3}/20 attempts={attempts}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / "run_status.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=status_rows[0].keys()); writer.writeheader(); writer.writerows(status_rows)
    completion_rows = []
    for config in CONFIGS:
        for seed in range(1, 11):
            for path in dirs(config, seed):
                try:
                    summary = json.loads((path / "completion_summary.json").read_text())
                    meta = json.loads((path / "metadata.json").read_text())
                except Exception:
                    summary, meta = {}, {}
                completion_rows.append({"config": config.slug, "seed": seed, "run_dir": str(path.resolve()),
                                        "driver_status": meta.get("status", "missing"),
                                        "scheduled": summary.get("scheduled", 320),
                                        "issued": summary.get("issued", 0),
                                        "completed": summary.get("completed", 0),
                                        "completion_rate_issued": summary.get("completion_rate_issued", 0),
                                        "completion_rate_schedule": summary.get("completion_rate_schedule", 0)})
    with (RESULTS / "attempt_completion.csv").open("w", newline="") as handle:
        columns = ["config", "seed", "run_dir", "driver_status", "scheduled", "issued", "completed",
                   "completion_rate_issued", "completion_rate_schedule"]
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(completion_rows)
    count = write_selection()
    if print_status: print(f"federation-v2: {ready}/4 READY, selected={count}/80")
    return 0 if ready == 4 and count == 80 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "validate", "status", "validate-run"))
    parser.add_argument("--phase", choices=("repeated", "uniform"))
    parser.add_argument("--config", choices=tuple(config.slug for config in CONFIGS))
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        if not args.phase: parser.error("run requires --phase")
        return run_phase(args.phase)
    if args.command == "validate-run":
        if not args.config or not args.run_dir: parser.error("validate-run requires --config and --run-dir")
        config = next(config for config in CONFIGS if config.slug == args.config)
        valid, reason = validate(config, args.run_dir)
        print(f"{'READY' if valid else 'REJECTED'} {args.run_dir} reason={reason}")
        return 0 if valid else 1
    return validate_all(print_status=True)


if __name__ == "__main__": raise SystemExit(main())
