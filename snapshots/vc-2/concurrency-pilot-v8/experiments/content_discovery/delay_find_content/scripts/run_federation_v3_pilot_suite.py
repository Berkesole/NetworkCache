#!/usr/bin/env python3
"""Run exactly one attempt for each federation-v3 exploratory pilot."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[2]
RESULTS = EXP / "results/federation-v3-pilot"


@dataclass(frozen=True)
class Pilot:
    slug: str
    catalog_size: int
    distribution: str

    @property
    def scenario(self) -> str:
        return f"kua-dc-v3-federation-v3-pilot-{self.slug}-seed001"


PILOTS = (
    Pilot("catalog1000-zipf-nc", 1000, "zipf"),
    Pilot("catalog10000-zipf-nc", 10000, "zipf"),
    Pilot("catalog10000-uniform-nc", 10000, "uniform"),
)


def read_csv(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def run_dir(pilot: Pilot) -> Path:
    return RESULTS / "runs" / pilot.slug / "run001"


def validate(pilot: Pilot, root: Path) -> tuple[bool, str]:
    try:
        metadata = json.loads((root / "metadata.json").read_text())
        source = json.loads((root / "schedule_metadata.json").read_text())
        completion = json.loads((root / "completion_summary.json").read_text())
        activation = json.loads((root / "activation.json").read_text())
        schedule = read_csv(root / "schedule.tsv", "\t")
        requests = read_csv(root / "request_results.csv")
        trace = read_csv(root / "trace_breakdown.csv")
        isolation = read_csv(root / "isolation_results.csv")
        occupancy = read_csv(root / "cache_occupancy.csv")
        bloom = read_csv(root / "sync_bloom_state.csv")
    except Exception:
        return False, "missing_or_invalid_files"
    if metadata.get("status") != "ok" or metadata.get("wire_version") != 2:
        return False, "driver_or_wire"
    if source.get("catalog_size_per_cluster") != pilot.catalog_size or source.get("distribution") != pilot.distribution:
        return False, "workload_metadata"
    if len(schedule) != 3584 or len(requests) != 3584:
        return False, "request_row_count"
    if completion.get("scheduled") != 3584 or completion.get("issued") != 3584 or completion.get("completed") != 3584:
        return False, "completion_summary"
    if len(isolation) != 2 or any(row.get("status") != "ok" or row.get("completion_present") != "0"
                                  for row in isolation):
        return False, "isolation"
    if not activation.get("federation_ready_ns") or activation.get("activation_apply_latency_ns", -1) < 0:
        return False, "activation"
    acks = {path.stem for path in (root / "activation_acks").glob("*.tsv")}
    if acks != {"node1", "node33"}:
        return False, "activation_acks"
    expected = {row["request_index"]: row for row in schedule}
    intervals = []
    for row in requests:
        scheduled = expected.get(row.get("request_index", ""))
        if not scheduled or row.get("status") != "ok":
            return False, "request_status"
        if row.get("actual_value_bytes") != scheduled.get("value_size_bytes"):
            return False, "value_size"
        if row.get("actual_fnv1a64") != scheduled.get("expected_fnv1a64"):
            return False, "value_hash"
        if row.get("quiescence_ack_count") != "64":
            return False, "quiescence"
        start, end = int(row["request_start_mono_ns"]), int(row["app_receive_mono_ns"])
        if end < start:
            return False, "clock_order"
        intervals.append((start, end))
    intervals.sort()
    if any(second[0] < first[1] for first, second in zip(intervals, intervals[1:])):
        return False, "request_overlap"
    if sum(row["phase"] == "pre" for row in schedule) != 1024:
        return False, "pre_shape"
    if sum(row["request_class"] == "remote" for row in schedule) != 2048:
        return False, "remote_shape"
    if sum(row["request_class"] == "local_control" for row in schedule) != 512:
        return False, "local_control_shape"
    if len(trace) != 3584 or sum(row.get("trace_complete") == "1" for row in trace) < 3549:
        return False, "trace_coverage"
    for bin_index in range(1, 17):
        rows = [row for row in trace if row.get("phase") == "post" and row.get("bin") == str(bin_index)]
        remote = [row for row in rows if row.get("request_class") == "remote"]
        local = [row for row in rows if row.get("request_class") == "local_control"]
        if sum(row.get("trace_complete") == "1" for row in remote) < 127:
            return False, "remote_bin_trace_coverage"
        if sum(row.get("trace_complete") == "1" for row in local) < 31:
            return False, "local_bin_trace_coverage"
    snapshot_ids = {"ready", "pre-bin02", "pre-bin04", "pre-bin06", "pre-bin08", "pre-t0", "post-t0",
                    "post-bin01", "post-bin02", "post-bin04", "post-bin06", "post-bin08", "post-bin10",
                    "post-bin12", "post-bin14", "post-bin16", "final"}
    if len(occupancy) != len(snapshot_ids) * 64 or {row.get("snapshot_id") for row in occupancy} != snapshot_ids:
        return False, "occupancy_shape"
    if any(not row.get("redis_used_memory_bytes") for row in occupancy):
        return False, "occupancy_content"
    if not bloom or {row.get("snapshot_id") for row in bloom} != snapshot_ids:
        return False, "bloom_shape"
    clock = read_csv(root / "clock_state.tsv")
    if len(clock) != 64 or len({row.get("timens_offsets_sha256") for row in clock}) != 1:
        return False, "clock_gate"
    network = (root / "network_state.txt").read_text(errors="replace")
    sections = network.split("## node")[1:]
    if len(sections) != 88 or any("mtu 1500" not in section or "delay 2ms" not in section for section in sections):
        return False, "network_gate"
    return True, "ok"


def status(write: bool = True) -> int:
    rows = []
    for pilot in PILOTS:
        root = run_dir(pilot)
        ok, reason = validate(pilot, root) if root.exists() else (False, "not_run")
        rows.append({"config": pilot.slug, "status": "READY" if ok else "NOT_READY",
                     "selected_runs": int(ok), "reason": reason, "run_dir": str(root.resolve())})
        print(f"{'READY' if ok else 'NOT_READY':10} {pilot.slug:30} selected={int(ok)}/1 reason={reason}")
    if write:
        RESULTS.mkdir(parents=True, exist_ok=True)
        with (RESULTS / "run_status.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
    return 0 if all(row["status"] == "READY" for row in rows) else 1


def run() -> int:
    subprocess.run([sys.executable, str(HERE / "generate_federation_v3_pilot.py")], cwd=REPO, check=True)
    smoke_metadata = RESULTS / "smoke/10k-zipf-run001/metadata.json"
    built = smoke_metadata.exists() and json.loads(smoke_metadata.read_text()).get("status") == "ok"
    if not built:
        print("required 10k/88-container smoke is not READY; run federation_v3_pilot_suite/05_smoke.sh first",
              file=sys.stderr)
        return 4
    for pilot in PILOTS:
        root = run_dir(pilot)
        if root.exists():
            ok, reason = validate(pilot, root)
            if ok:
                print(f"skip READY {pilot.slug}")
                continue
            print(f"existing failed pilot stops sequence: {pilot.slug} ({reason}); raw directory was preserved", file=sys.stderr)
            return 2
        root.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        if built: env["SKIP_BUILD"] = "1"
        completed = subprocess.run([str(HERE / "run_federation_v3_pilot_once.sh"), pilot.scenario, str(root)],
                                   cwd=REPO, env=env, check=False)
        if completed.returncode:
            print(f"pilot failed; no additional run started: {pilot.slug}", file=sys.stderr)
            status()
            return completed.returncode
        built = True
        ok, reason = validate(pilot, root)
        if not ok:
            print(f"pilot failed quality gate; no additional run started: {pilot.slug} ({reason})", file=sys.stderr)
            status()
            return 3
    status()
    subprocess.run([sys.executable, str(HERE / "analyze_federation_v3_pilot.py")], cwd=REPO, check=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status", "validate"))
    args = parser.parse_args()
    return run() if args.command == "run" else status()


if __name__ == "__main__":
    raise SystemExit(main())
