#!/usr/bin/env python3
"""Run, resume, select and validate the federation-v4 formal matrix."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import formal_campaign
import generate_federation_v4_bf1024_h3_formal as gen


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[2]
SUITE = gen.SUITE
RESULTS = EXP / "results" / f"{SUITE}-r2"
TARGET = 2
MAX_ATTEMPTS = 4


@dataclass(frozen=True)
class Config:
    phase: str
    workload: str
    catalog_size: int
    distribution: str
    method: str

    @property
    def slug(self) -> str:
        return f"{self.workload}-{self.method}"

    def scenario(self, seed: int) -> str:
        return gen.scenario_name(self.workload, seed)


CONFIGS = (
    Config("nc", "catalog512-zipf", 512, "zipf", "nc"),
    Config("nc", "catalog512-uniform", 512, "uniform", "nc"),
    Config("nc", "catalog1000-zipf", 1000, "zipf", "nc"),
    Config("nc", "catalog1000-uniform", 1000, "uniform", "nc"),
    Config("baselines", "catalog512-zipf", 512, "zipf", "known-owner"),
    Config("baselines", "catalog512-zipf", 512, "zipf", "tcp-direct"),
)


def read_csv(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def dirs(config: Config, seed: int) -> list[Path]:
    root = RESULTS / "attempts" / config.slug / f"seed{seed:03d}"
    return sorted(path for path in root.glob("attempt[0-9][0-9][0-9]") if path.is_dir()) \
        if root.exists() else []


def build_failure(path: Path) -> bool:
    try:
        metadata = json.loads((path / "metadata.json").read_text())
        stderr = (path / "driver.stderr.log").read_text(errors="replace")
    except Exception:
        return False
    return metadata.get("status") == "driver_failed" and "FAIL: docker build" in stderr


def quota_dirs(config: Config, seed: int) -> list[Path]:
    return [path for path in dirs(config, seed) if not build_failure(path)]


def campaign_ok(path: Path) -> bool:
    try:
        attached = json.loads((path / "campaign_lock.json").read_text())
        current = json.loads(formal_campaign.LOCK.read_text())
    except Exception:
        return False
    try:
        metadata = json.loads((path / "metadata.json").read_text())
    except Exception:
        return False
    return (attached.get("campaign_fingerprint") == current.get("campaign_fingerprint") and
            metadata.get("runtime_image_id") == current.get("runtime_image_id"))


def network_ok(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(errors="replace")
    sections = text.split("## node")[1:]
    if len(sections) != 88:
        return False
    return all("mtu 1500" in section and "qdisc netem" in section and
               "delay 2ms" in section and
               ("rate 1Gbit" in section or "rate 1000Mbit" in section)
               for section in sections)


def clock_ok(path: Path) -> bool:
    rows = read_csv(path)
    try:
        values = [int(row["monotonic_ns"]) for row in rows]
        offsets = {row["timens_offsets_sha256"] for row in rows}
    except (KeyError, TypeError, ValueError):
        return False
    return len(rows) == 64 and len(offsets) == 1 and max(values) - min(values) <= 5_000_000_000


def raw_expiry(path: Path) -> int:
    count = 0
    for trace in (path / "traces").glob("*.jsonl"):
        for line in trace.read_text(errors="replace").splitlines():
            if '"event":"query_expired_unsatisfied"' in line or \
                    '"event": "query_expired_unsatisfied"' in line:
                count += 1
    return count


def snapshots_ok(path: Path) -> bool:
    occupancy = read_csv(path / "cache_occupancy.csv")
    bloom = read_csv(path / "sync_bloom_state.csv")
    snapshots = {
        "ready", "pre-bin02", "pre-bin04", "pre-bin06", "pre-bin08", "pre-t0",
        "post-t0", "post-bin01", "post-bin02", "post-bin04", "post-bin06",
        "post-bin08", "post-bin10", "post-bin12", "post-bin14", "post-bin16", "final",
    }
    if len(occupancy) != len(snapshots) * 64:
        return False
    if {row.get("snapshot_id") for row in occupancy} != snapshots:
        return False
    if not bloom or {row.get("snapshot_id") for row in bloom} != snapshots:
        return False
    try:
        return all(int(row["bit_count"]) == 1024 and 0 <= float(row["occupancy"]) <= 1
                   for row in bloom)
    except (KeyError, TypeError, ValueError):
        return False


def validate(config: Config, path: Path) -> tuple[bool, str]:
    try:
        metadata = json.loads((path / "metadata.json").read_text())
        source = json.loads((path / "schedule_metadata.json").read_text())
        completion = json.loads((path / "completion_summary.json").read_text())
        activation = json.loads((path / "activation.json").read_text())
        schedule = read_csv(path / "schedule.tsv", "\t")
        requests = read_csv(path / "request_results.csv")
        isolation = read_csv(path / "isolation_results.csv")
    except Exception:
        return False, "missing_or_invalid_files"
    if build_failure(path):
        return False, "infrastructure_build"
    if metadata.get("status") != "ok" or metadata.get("suite") != SUITE:
        return False, "driver_or_suite"
    if metadata.get("method") != config.method:
        return False, "method"
    required = {
        "wire_version": 3, "bloom_bits": 1024, "bloom_hash_count": 3,
        "staged_fallback": True, "fallback_delay_ms": 1500,
        "query_table_timeout_ms": 7000, "controller_timeout_ms": 8000,
        "max_hop_count": 12,
    }
    if any(metadata.get(key) != value for key, value in required.items()):
        return False, "protocol_configuration"
    if (source.get("catalog_size_per_cluster") != config.catalog_size or
            source.get("distribution") != config.distribution or
            any(source.get(key) != value for key, value in required.items())):
        return False, "workload_configuration"
    if not metadata.get("git_dirty_patch_sha256") or metadata.get("runtime_image_id") in ("", "unavailable"):
        return False, "provenance_missing"
    if metadata.get("runtime_image_snapshot_phase") != "after_build_before_kathara_launch":
        return False, "runtime_snapshot_phase"
    if not campaign_ok(path):
        return False, "campaign_lock"
    if len(schedule) != 3584 or len(requests) != 3584:
        return False, "request_row_count"
    if any(completion.get(field) != 3584 for field in ("scheduled", "issued", "completed")):
        return False, "completion_summary"
    if (len(isolation) != 2 or any(row.get("status") != "ok" or
            row.get("completion_present") != "0" or row.get("quiescence_ack_count") != "64"
            for row in isolation)):
        return False, "isolation"
    if not activation.get("federation_ready_ns") or activation.get("activation_apply_latency_ns", -1) < 0:
        return False, "activation"
    if {file.stem for file in (path / "activation_acks").glob("*.tsv")} != {"node1", "node33"}:
        return False, "activation_acks"
    expected = {row["request_index"]: row for row in schedule}
    intervals = []
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
            return False, "latency"
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
    if not clock_ok(path / "clock_state.tsv") or not network_ok(path / "network_state.txt"):
        return False, "clock_or_network"
    if config.method == "tcp-direct":
        return True, "ok"
    logs = "\n".join(file.read_text(errors="replace") for file in (path / "logs").glob("node*.log"))
    if logs.count("cache backend: redis") < 64:
        return False, "redis_not_ready"
    if logs.count("wire configuration version=3") < 64 or \
            logs.count("bloom configuration bits=1024 hashes=3 staged_fallback=enabled") < 64:
        return False, "node_protocol_gate"
    if raw_expiry(path):
        return False, "query_expired_unsatisfied"
    trace = read_csv(path / "trace_breakdown.csv")
    if len(trace) != 3584 or sum(row.get("trace_complete") == "1" for row in trace) < math.ceil(3584 * .99):
        return False, "trace_coverage"
    if any(row.get("application_delivery_events") != "1" for row in trace):
        return False, "application_delivery_count"
    for batch in range(1, 17):
        remote = [row for row in trace if row.get("phase") == "post" and
                  row.get("request_class") == "remote" and row.get("bin") == str(batch)]
        local = [row for row in trace if row.get("phase") == "post" and
                 row.get("request_class") == "local_control" and row.get("bin") == str(batch)]
        if sum(row.get("trace_complete") == "1" for row in remote) < 127:
            return False, "remote_batch_trace_coverage"
        if sum(row.get("trace_complete") == "1" for row in local) < 31:
            return False, "local_batch_trace_coverage"
    if config.method == "known-owner" and any(
            row.get("origin_fallback_triggered_events") != "0" for row in trace):
        return False, "known_owner_started_fallback"
    pre = [row for row in trace if row.get("phase") == "pre"]
    if any(int(row.get("cross_link_request_frames") or 0) or
           int(row.get("cross_link_data_frames") or 0) for row in pre):
        return False, "pre_activation_cross_link_traffic"
    if not snapshots_ok(path):
        return False, "snapshot_invalid"
    return True, "ok"


def candidates(config: Config, seed: int) -> list[tuple[Path, bool, str]]:
    return [(path, *validate(config, path)) for path in dirs(config, seed)]


def selected(config: Config, seed: int) -> list[Path]:
    return [path for path, valid, _ in candidates(config, seed) if valid][:TARGET]


def ready(config: Config) -> bool:
    return all(len(selected(config, seed)) == TARGET for seed in gen.SEEDS)


def next_attempt(config: Config, seed: int) -> Path:
    root = RESULTS / "attempts" / config.slug / f"seed{seed:03d}"
    return root / f"attempt{len(dirs(config, seed)) + 1:03d}"


def run_one(config: Config, seed: int, output: Path, built: bool) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "FEDERATION_FALLBACK_SUITE": SUITE,
        "FEDERATION_V3_FALLBACK_WORKLOAD_ROOT": str(gen.DEFAULT_ROOT),
        "FEDERATION_FALLBACK_DATASET_LABEL": "formal multi-seed federation-v4 campaign",
        "FEDERATION_V3_FALLBACK_BUILD_NO_CACHE": "0",
    })
    if built:
        env["SKIP_BUILD"] = "1"
    completed = subprocess.run(
        [str(HERE / "run_federation_v3_bf1024_h3_fallback_once.sh"),
         config.scenario(seed), config.method, str(output)],
        cwd=REPO, env=env, check=False,
    )
    if output.exists():
        formal_campaign.attach(output)
    if completed.returncode == 3:
        raise RuntimeError("federation lab lock is held; no additional attempt was started")
    return output.joinpath("runtime_image_id.before_launch").exists() and not build_failure(output)


def run_phase(phase: str) -> int:
    formal_campaign.verify()
    configs = list(CONFIGS if phase == "all" else [config for config in CONFIGS if config.phase == phase])
    # The campaign lock already verified the smoke-built image ID.  Never
    # rebuild between formal attempts, otherwise a resume could mix runtimes.
    built = True
    for slot in range(1, MAX_ATTEMPTS + 1):
        for seed in gen.SEEDS:
            block = list(configs)
            random.Random(f"{SUITE}:block:{phase}:{seed}:{slot}").shuffle(block)
            for config in block:
                if len(selected(config, seed)) >= TARGET or len(quota_dirs(config, seed)) >= slot:
                    continue
                output = next_attempt(config, seed)
                built = run_one(config, seed, output, built) or built
                if output.exists() and build_failure(output):
                    write_status()
                    print(f"INFRASTRUCTURE_ERROR retained at {output}", file=sys.stderr)
                    return 3
    write_status()
    return 0 if all(ready(config) for config in configs) else 2


def status_rows() -> list[dict[str, str]]:
    rows = []
    for config in CONFIGS:
        counts = [len(selected(config, seed)) for seed in gen.SEEDS]
        attempts = [len(quota_dirs(config, seed)) for seed in gen.SEEDS]
        infra = [len(dirs(config, seed)) - attempts[index] for index, seed in enumerate(gen.SEEDS)]
        rows.append({
            "phase": config.phase,
            "config": config.slug,
            "status": "READY" if counts == [2] * 10 else "NEEDS_MORE",
            "selected_runs": str(sum(counts)),
            "attempts": str(sum(attempts)),
            "infrastructure_failures": str(sum(infra)),
            "selected_seed_counts": ";".join(
                f"seed{seed:03d}={count}" for seed, count in zip(gen.SEEDS, counts)),
        })
    return rows


def write_status() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = status_rows()
    with (RESULTS / "run_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    attempts, selection, completion = [], [], []
    for config in CONFIGS:
        for seed in gen.SEEDS:
            chosen = set(selected(config, seed))
            for index, (path, valid, reason) in enumerate(candidates(config, seed), 1):
                attempts.append({"config": config.slug, "seed": seed, "attempt_index": index,
                                 "run_dir": str(path.resolve()),
                                 "quality_status": "QUALIFIED" if valid else "REJECTED",
                                 "reason": reason, "selected": int(path in chosen)})
                try:
                    summary = json.loads((path / "completion_summary.json").read_text())
                    metadata = json.loads((path / "metadata.json").read_text())
                except Exception:
                    summary, metadata = {}, {}
                completion.append({"config": config.slug, "seed": seed,
                                   "run_dir": str(path.resolve()),
                                   "driver_status": metadata.get("status", "missing"),
                                   "scheduled": summary.get("scheduled", 3584),
                                   "issued": summary.get("issued", 0),
                                   "completed": summary.get("completed", 0),
                                   "completion_rate_schedule": summary.get("completion_rate_schedule", 0)})
            for index, path in enumerate(selected(config, seed), 1):
                selection.append({"config": config.slug, "workload": config.workload,
                                  "catalog_size": config.catalog_size,
                                  "distribution": config.distribution, "method": config.method,
                                  "seed": seed, "selection_index": index,
                                  "run_dir": str(path.resolve())})
    outputs = (
        ("attempt_manifest.csv", attempts,
         ["config", "seed", "attempt_index", "run_dir", "quality_status", "reason", "selected"]),
        ("federation_selection.csv", selection,
         ["config", "workload", "catalog_size", "distribution", "method", "seed", "selection_index", "run_dir"]),
        ("attempt_completion.csv", completion,
         ["config", "seed", "run_dir", "driver_status", "scheduled", "issued", "completed", "completion_rate_schedule"]),
    )
    for filename, records, columns in outputs:
        with (RESULTS / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader(); writer.writerows(records)


def print_status() -> int:
    write_status()
    print("campaign=r2")
    for row in status_rows():
        print(f"{row['status']:10} {row['config']:42} selected={row['selected_runs']:>3}/20 "
              f"attempts={row['attempts']} infra={row['infrastructure_failures']}")
    return 0 if all(ready(config) for config in CONFIGS) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--phase", choices=("all", "nc", "baselines"), default="all")
    sub.add_parser("status")
    sub.add_parser("validate")
    check = sub.add_parser("validate-run")
    check.add_argument("--config", required=True, choices=tuple(config.slug for config in CONFIGS))
    check.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "run":
        return run_phase(args.phase)
    if args.command == "validate-run":
        config = next(item for item in CONFIGS if item.slug == args.config)
        valid, reason = validate(config, args.run_dir)
        print(f"{'READY' if valid else 'REJECTED'} {args.run_dir} reason={reason}")
        return 0 if valid else 2
    status = print_status()
    if args.command == "validate" and status == 0:
        subprocess.run([sys.executable, str(HERE / "analyze_federation_v4_bf1024_h3_formal.py")],
                       cwd=REPO, check=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
