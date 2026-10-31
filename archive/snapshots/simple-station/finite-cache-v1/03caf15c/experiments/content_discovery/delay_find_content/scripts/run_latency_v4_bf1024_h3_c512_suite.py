#!/usr/bin/env python3
"""Run, resume and validate the isolated latency-v4 BF1024/H3/C512 suite."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import generate_latency_v4_bf1024_h3_c512 as gen
import formal_campaign


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_DIR = SCRIPT_DIR.parent
REPO_ROOT = EXP_DIR.parents[2]
SUITE = "latency-v4-bf1024-h3-c512"
PILOT_RESULTS_ROOT = EXP_DIR / "results" / SUITE
RESULTS_ROOT = EXP_DIR / "results" / f"{SUITE}-formal-r2"
WORKLOAD_ROOT = EXP_DIR / "workloads" / SUITE
TARGET_PER_SEED = 2
MAX_ATTEMPTS_PER_SEED = 4


@dataclass(frozen=True)
class Config:
    phase: str
    workload: str
    topology: int
    method: str
    size: int

    @property
    def slug(self) -> str:
        return f"{self.workload}-{self.topology}-{self.method}-b{self.size}"

    def scenario(self, seed: int) -> str:
        return gen.scenario_name(f"kua-dc-v4-{self.topology}", self.workload, self.size, seed)


CONFIGS = [
    *[Config("controlled", "controlled", topology, method, 65536)
      for topology in (16, 32) for method in ("nc", "known-owner")],
    *[Config("iid-zipf", "iid-zipf", topology, method, 65536)
      for topology in (16, 32) for method in ("nc", "known-owner")],
    *[Config("iid-uniform", "iid-uniform", topology, method, 65536)
      for topology in (16, 32) for method in ("nc", "known-owner")],
    *[Config("data-size", "controlled", 32, method, size)
      for method in ("nc", "known-owner") for size in (1024, 1048576)],
    *[Config("data-size", "controlled", 32, "tcp-direct", size)
      for size in (1024, 65536, 1048576)],
]
PILOTS = [
    Config("pilot", "iid-zipf", 16, "nc", 65536),
    Config("pilot", "iid-zipf", 32, "nc", 65536),
    Config("pilot", "iid-uniform", 32, "nc", 65536),
]


def read_csv(path: Path, delimiter: str = ",") -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iid_request_count() -> int:
    metadata = json.loads((WORKLOAD_ROOT / "canonical" / "iid-zipf-seed001.json").read_text())
    return int(metadata["iid_requests"])


def generator_commit() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%H", "--", str(Path(gen.__file__))],
        text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


def workload_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted((WORKLOAD_ROOT / "mapped").glob("*.metadata.json")):
        metadata = json.loads(path.read_text())
        digest.update(path.name.encode())
        digest.update(str(metadata.get("mapping_sha256", "")).encode())
        digest.update(str(metadata.get("canonical_sha256", "")).encode())
    return digest.hexdigest()


def run_dirs(config: Config, seed: int) -> List[Path]:
    root = RESULTS_ROOT / "attempts" / config.slug / f"seed{seed:03d}"
    return sorted(path for path in root.glob("attempt[0-9][0-9][0-9]") if path.is_dir()) if root.exists() else []


def is_build_failure(path: Path) -> bool:
    try:
        metadata = json.loads((path / "metadata.json").read_text())
        stderr = (path / "driver.stderr.log").read_text(errors="replace")
    except Exception:
        return False
    return metadata.get("status") == "driver_failed" and "FAIL: docker build" in stderr


def quota_run_dirs(config: Config, seed: int) -> List[Path]:
    return [path for path in run_dirs(config, seed) if not is_build_failure(path)]


def validate_network_state(path: Path, topology: int) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    expected = 64 if topology == 16 else 128
    netem = [line for line in text.splitlines() if line.startswith("qdisc netem")]
    mtu = [line for line in text.splitlines() if ": eth" in line and "mtu 1500" in line]
    return len(netem) == expected and len(mtu) >= expected and all(
        "delay 2ms" in line and ("rate 1Gbit" in line or "rate 1000Mbit" in line)
        and "limit 1000" in line for line in netem)


def validate_clock_state(path: Path, topology: int) -> bool:
    rows = read_csv(path)
    try:
        values = [int(row["monotonic_ns"]) for row in rows]
        offsets = {row["timens_offsets_sha256"] for row in rows}
    except (KeyError, TypeError, ValueError):
        return False
    return len(rows) == topology and len(offsets) == 1 and max(values) - min(values) <= 5_000_000_000


def raw_expiry_count(path: Path) -> int:
    count = 0
    for trace in (path / "traces").glob("*.jsonl"):
        for line in trace.read_text(errors="replace").splitlines():
            if '"event":"query_expired_unsatisfied"' in line or '"event": "query_expired_unsatisfied"' in line:
                count += 1
    return count


def snapshots_ok(path: Path, config: Config) -> bool:
    occupancy = read_csv(path / "cache_occupancy.tsv", "\t")
    bloom = read_csv(path / "sync_bloom_state.tsv", "\t")
    expected_snapshots = 10 if config.workload == "controlled" else iid_request_count() // 128 + 2
    snapshot_ids = {row.get("snapshot_id", "") for row in occupancy}
    if len(snapshot_ids) != expected_snapshots:
        return False
    for snapshot in snapshot_ids:
        nodes = {row.get("node", "") for row in occupancy if row.get("snapshot_id") == snapshot}
        bloom_nodes = {row.get("node", "") for row in bloom if row.get("snapshot_id") == snapshot}
        if len(nodes) != config.topology or len(bloom_nodes) != config.topology:
            return False
    try:
        return all(int(row["bit_count"]) == 1024 and 0 <= float(row["occupancy"]) <= 1
                   for row in bloom)
    except (KeyError, TypeError, ValueError):
        return False


def trace_coverage_ok(rows: Sequence[Dict[str, str]], config: Config,
                      measurement_count: int) -> bool:
    measured = [row for row in rows if row.get("phase") == "measurement"]
    complete = [row for row in measured if row.get("trace_complete_proxy") == "1"]
    if len(measured) != measurement_count or len(complete) < math.ceil(measurement_count * 0.99):
        return False
    field = "round" if config.workload == "controlled" else "bin"
    width = 32 if config.workload == "controlled" else 128
    minimum = 31 if config.workload == "controlled" else 127
    groups = 8 if config.workload == "controlled" else measurement_count // width
    counts: Dict[str, int] = {}
    for row in complete:
        counts[row.get(field, "")] = counts.get(row.get(field, ""), 0) + 1
    return all(counts.get(str(group), 0) >= minimum for group in range(1, groups + 1))


def validate_run(path: Path, config: Config) -> Tuple[bool, str]:
    try:
        metadata = json.loads((path / "metadata.json").read_text())
        schedule = read_csv(path / "schedule.tsv", "\t")
        results = read_csv(path / "request_results.csv")
    except Exception:
        return False, "missing_or_invalid_files"
    if is_build_failure(path):
        return False, "infrastructure_build"
    required = {"suite": SUITE, "wire_version": 3, "bloom_bits": 1024,
                "bloom_hash_count": 3, "fallback_delay_ms": 1500,
                "query_table_timeout_ms": 7000, "max_hop_count": 12,
                "request_timeout_ms": 8000}
    if metadata.get("status") != "ok" or any(metadata.get(key) != value for key, value in required.items()):
        return False, "driver_or_protocol_configuration"
    if metadata.get("staged_fallback") is not True:
        return False, "fallback_disabled"
    if "attempts" in path.parts:
        try:
            attached = json.loads((path / "campaign_lock.json").read_text())
            current_lock = json.loads(formal_campaign.LOCK.read_text())
        except Exception:
            return False, "campaign_lock_missing"
        if attached.get("campaign_fingerprint") != current_lock.get("campaign_fingerprint"):
            return False, "campaign_lock_mismatch"
        if metadata.get("runtime_image_id") != current_lock.get("runtime_image_id"):
            return False, "runtime_image_mismatch"
    if not metadata.get("git_dirty_patch_sha256") or metadata.get("runtime_image_id") in ("", "unavailable"):
        return False, "provenance_missing"
    if metadata.get("runtime_image_snapshot_phase") != "after_build_before_kathara_launch":
        return False, "image_snapshot_phase"
    if len(results) != len(schedule):
        return False, "row_count"
    by_index = {row["request_index"]: row for row in results}
    expected_stable = 250 if config.size <= 1024 else 500 if config.size <= 65536 else 2000
    intervals = []
    for expected in schedule:
        actual = by_index.get(expected["request_index"])
        if not actual or actual.get("status") != "ok":
            return False, "request_status"
        if actual.get("actual_value_bytes") != expected["value_size_bytes"]:
            return False, "value_size"
        if actual.get("actual_fnv1a64") != expected["expected_fnv1a64"]:
            return False, "value_hash"
        if actual.get("quiescence_ack_count") != str(config.topology):
            return False, "quiescence_ack"
        if actual.get("quiescence_stable_ms") != str(expected_stable):
            return False, "quiescence_stability"
        try:
            start, end = int(actual["request_start_mono_ns"]), int(actual["app_receive_mono_ns"])
        except (KeyError, ValueError):
            return False, "latency"
        if end < start:
            return False, "latency"
        intervals.append((start, end))
    intervals.sort()
    if any(current[0] < previous[1] for previous, current in zip(intervals, intervals[1:])):
        return False, "overlap"
    if not validate_network_state(path / "network_state.txt", config.topology):
        return False, "network_state"
    if not validate_clock_state(path / "clock_state.tsv", config.topology):
        return False, "clock_gate"
    logs = "\n".join(log.read_text(errors="replace") for log in sorted((path / "logs").glob("node*.log")))
    if config.method != "tcp-direct":
        if logs.count("cache backend: redis") < config.topology:
            return False, "redis_not_ready"
        if logs.count("wire configuration version=3") < config.topology:
            return False, "wire_node_gate"
        if logs.count("bloom configuration bits=1024 hashes=3 staged_fallback=enabled") < config.topology:
            return False, "bloom_node_gate"
        if logs.count("protocol.max_hop_count: 12") < config.topology:
            return False, "max_hop_node_gate"
        if raw_expiry_count(path):
            return False, "query_expired_unsatisfied"
        trace = read_csv(path / "trace_breakdown.csv")
        measurement_count = sum(row.get("phase") == "measurement" for row in schedule)
        if not trace_coverage_ok(trace, config, measurement_count):
            return False, "trace_coverage"
        if any(row.get("application_delivery_events") != "1" for row in trace):
            return False, "application_delivery_count"
        if config.method == "known-owner" and any(row.get("source_fallback_triggered") != "0" for row in trace):
            return False, "known_owner_started_fallback"
        if not snapshots_ok(path, config):
            return False, "snapshot_invalid"
    else:
        measurement = [row for row in results if row.get("phase") == "measurement"]
        if any(not row.get("discovery_latency_ns") or not row.get("return_path_latency_ns") for row in measurement):
            return False, "tcp_breakdown"
    return True, "ok"


def candidates(config: Config, seed: int) -> List[Tuple[Path, bool, str]]:
    return [(path, *validate_run(path, config)) for path in run_dirs(config, seed)]


def selected(config: Config, seed: int) -> List[Path]:
    return [path for path, valid, _ in candidates(config, seed) if valid][:TARGET_PER_SEED]


def config_ready(config: Config) -> bool:
    return all(len(selected(config, seed)) == TARGET_PER_SEED for seed in gen.SEEDS)


def next_attempt(config: Config, seed: int) -> Path:
    root = RESULTS_ROOT / "attempts" / config.slug / f"seed{seed:03d}"
    return root / f"attempt{len(run_dirs(config, seed)) + 1:03d}"


def acceptance_marker_valid() -> Tuple[bool, str]:
    marker_path = PILOT_RESULTS_ROOT / "pilot_acceptance.json"
    try:
        marker = json.loads(marker_path.read_text())
    except Exception:
        return False, "missing pilot_acceptance.json"
    if marker.get("suite") != SUITE or marker.get("accepted") is not True:
        return False, "invalid pilot acceptance"
    if marker.get("decision") != "fixed_observation_horizon_no_convergence_claim":
        return False, "pilot acceptance has another interpretation"
    if marker.get("iid_requests") != 4096:
        return False, "formal campaign requires 4096 IID requests"
    if marker.get("iid_requests") != iid_request_count():
        return False, "pilot acceptance uses another IID length"
    if marker.get("generator_sha256") != sha256(Path(gen.__file__)):
        return False, "pilot acceptance uses another generator"
    if marker.get("generator_commit") != generator_commit():
        return False, "pilot acceptance uses another generator commit"
    if marker.get("workload_fingerprint") != workload_fingerprint():
        return False, "pilot acceptance uses another generated workload"
    runs = marker.get("runs", [])
    if len(runs) != 3:
        return False, "pilot acceptance does not bind three runs"
    for item in runs:
        path = Path(item.get("run_dir", ""))
        for filename in ("metadata.json", "schedule.tsv", "request_results.csv", "trace_breakdown.csv"):
            if not path.joinpath(filename).is_file() or item.get("hashes", {}).get(filename) != sha256(path / filename):
                return False, f"pilot raw hash mismatch: {path}/{filename}"
    return True, "ok"


def run_one(config: Config, seed: int, output: Path, skip_build: bool) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if skip_build:
        env["SKIP_BUILD"] = "1"
    command = [str(SCRIPT_DIR / "run_latency_v4_bf1024_h3_c512_once.sh"),
               config.scenario(seed), config.method, str(output)]
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=False)
    if output.exists() and "attempts" in output.parts:
        formal_campaign.attach(output)
    return output.joinpath("metadata.json").exists() and not is_build_failure(output)


def run_phase(phase: str) -> int:
    formal_campaign.verify()
    valid, reason = acceptance_marker_valid()
    if not valid:
        print(f"PILOT_GATE: {reason}", file=sys.stderr)
        return 4
    configs = CONFIGS if phase == "all" else [config for config in CONFIGS if config.phase == phase]
    # The formal campaign lock freezes the smoke-built runtime image.  Formal
    # attempts must reuse it instead of rebuilding from a later worktree.
    built = True
    for slot in range(1, MAX_ATTEMPTS_PER_SEED + 1):
        for seed in gen.SEEDS:
            block = list(configs)
            random.Random(f"{SUITE}:block:{phase}:{seed}:{slot}").shuffle(block)
            for config in block:
                if len(selected(config, seed)) >= TARGET_PER_SEED:
                    continue
                if len(quota_run_dirs(config, seed)) >= slot:
                    continue
                output = next_attempt(config, seed)
                built = run_one(config, seed, output, built) or built
                if output.exists() and is_build_failure(output):
                    write_status()
                    print(f"INFRASTRUCTURE_ERROR retained at {output}", file=sys.stderr)
                    return 3
    write_status()
    return 0 if all(config_ready(config) for config in configs) else 2


def pilot_root() -> Path:
    return PILOT_RESULTS_ROOT / f"pilots-iid{iid_request_count()}"


def pilot_run_dirs(config: Config) -> List[Path]:
    root = pilot_root() / config.slug / "seed001"
    return sorted(path for path in root.glob("run[0-9][0-9][0-9]") if path.is_dir()) if root.exists() else []


def selected_pilot(config: Config) -> Tuple[Path | None, str]:
    last_reason = "missing"
    for path in pilot_run_dirs(config):
        valid, reason = validate_run(path, config)
        if valid:
            return path, "ok"
        last_reason = reason
    return None, last_reason


def run_pilots() -> int:
    built = False
    for config in PILOTS:
        selected_path, _ = selected_pilot(config)
        if selected_path is not None:
            continue
        existing = pilot_run_dirs(config)
        output = pilot_root() / config.slug / "seed001" / f"run{len(existing) + 1:03d}"
        reusable_image = any(path.joinpath("runtime_image_id.before_launch").is_file() for path in existing)
        built = built or reusable_image
        built = run_one(config, 1, output, built) or built
        valid, reason = validate_run(output, config)
        if not valid:
            print(f"pilot failed ({reason}): {output}", file=sys.stderr)
            return 2
    write_pilot_assessment()
    return 0


def run_smoke(method: str, topology: int) -> int:
    config = Config("smoke", "controlled", topology, method, 65536)
    root = RESULTS_ROOT / "smoke" / config.slug / "seed001"
    existing = sorted(path for path in root.glob("run[0-9][0-9][0-9]") if path.is_dir()) if root.exists() else []
    try:
        current_image = formal_campaign.runtime_image_id()
    except RuntimeError:
        current_image = None
    for output in existing:
        valid, reason = validate_run(output, config)
        try:
            metadata = json.loads((output / "metadata.json").read_text())
        except Exception:
            metadata = {}
        if valid and current_image is not None and metadata.get("runtime_image_id") == current_image:
            print(f"smoke READY ({reason}): {output}")
            return 0
    output = root / f"run{len(existing) + 1:03d}"
    reusable_image = current_image is not None and (os.environ.get("RSYNC_SMOKE_REUSE_IMAGE") == "1" or any(
        path.joinpath("runtime_image_id.before_launch").is_file() for path in existing
    ))
    run_one(config, 1, output, reusable_image)
    valid, reason = validate_run(output, config)
    if valid:
        metadata = json.loads((output / "metadata.json").read_text())
        current_image = formal_campaign.runtime_image_id()
        if metadata.get("runtime_image_id") != current_image:
            valid, reason = False, "runtime_image_mismatch"
    print(f"smoke {'READY' if valid else 'REJECTED'} ({reason}): {output}")
    return 0 if valid else 2


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def relative_change(a: float, b: float) -> float:
    return abs(b - a) / abs(a) if a else math.inf


def write_pilot_assessment() -> List[Dict[str, object]]:
    output = []
    requests = iid_request_count()
    late_a = (13, 14) if requests == 2048 else (29, 30)
    late_b = (15, 16) if requests == 2048 else (31, 32)
    for config in PILOTS:
        path, reason = selected_pilot(config)
        if path is None:
            raise RuntimeError(f"missing qualified pilot for {config.slug}: {reason}")
        rows = [row for row in read_csv(path / "trace_breakdown.csv") if row.get("phase") == "measurement"]
        def stats(bins: Tuple[int, int]) -> Tuple[float, float, float]:
            selected_rows = [row for row in rows if int(row["bin"]) in bins]
            return (
                mean([float(row["end_to_end_latency_ns"]) for row in selected_rows]),
                mean([row["first_observed_service_class_proxy"] == "cache" for row in selected_rows]),
                mean([float(row["preferred_logical_request_events"]) for row in selected_rows]),
            )
        first, second = stats(late_a), stats(late_b)
        latency_change = relative_change(first[0], second[0])
        cache_pp = abs(second[1] - first[1]) * 100
        preferred_change = relative_change(first[2], second[2])
        stable = latency_change <= .10 and cache_pp <= 5 and preferred_change <= .10
        output.append({"config": config.slug, "workload": config.workload,
                       "run_dir": str(path.resolve()), "iid_requests": requests,
                       "latency_relative_change": latency_change,
                       "cache_fraction_change_pp": cache_pp,
                       "preferred_events_relative_change": preferred_change,
                       "stable": stable, "zipf_gate": config.workload == "iid-zipf"})
    root = pilot_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "pilot_assessment.json").write_text(json.dumps(output, indent=2) + "\n")
    with (root / "pilot_assessment.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0])); writer.writeheader(); writer.writerows(output)
    return output


def approve_pilots() -> int:
    assessment = write_pilot_assessment()
    if iid_request_count() != 4096:
        print("formal campaign fixes the IID observation horizon at 4096 requests", file=sys.stderr)
        return 2
    runs = []
    for config in PILOTS:
        path, selected_reason = selected_pilot(config)
        if path is None:
            print(f"pilot invalid ({selected_reason}): {config.slug}", file=sys.stderr)
            return 2
        valid, reason = validate_run(path, config)
        if not valid:
            print(f"pilot invalid ({reason}): {path}", file=sys.stderr)
            return 2
        names = ("metadata.json", "schedule.tsv", "request_results.csv", "trace_breakdown.csv")
        runs.append({"config": config.slug, "run_dir": str(path.resolve()),
                     "hashes": {name: sha256(path / name) for name in names}})
    marker = {"suite": SUITE, "accepted": True, "iid_requests": iid_request_count(),
              "decision": "fixed_observation_horizon_no_convergence_claim",
              "convergence_claim": False,
              "generator_sha256": sha256(Path(gen.__file__)),
              "generator_commit": generator_commit(),
              "workload_fingerprint": workload_fingerprint(), "runs": runs,
              "assessment_sha256": sha256(pilot_root() / "pilot_assessment.json")}
    PILOT_RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = PILOT_RESULTS_ROOT / ".pilot_acceptance.json.tmp"
    temporary.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n")
    temporary.replace(PILOT_RESULTS_ROOT / "pilot_acceptance.json")
    print("pilot acceptance marker created")
    return 0


def status_rows() -> List[Dict[str, str]]:
    rows = []
    for config in CONFIGS:
        valid_counts = [sum(valid for _, valid, _ in candidates(config, seed)) for seed in gen.SEEDS]
        attempts = [len(quota_run_dirs(config, seed)) for seed in gen.SEEDS]
        infra = [len(run_dirs(config, seed)) - attempts[index] for index, seed in enumerate(gen.SEEDS)]
        rows.append({"phase": config.phase, "config": config.slug,
                     "status": "READY" if config_ready(config) else "NEEDS_MORE",
                     "selected_runs": str(sum(min(TARGET_PER_SEED, count) for count in valid_counts)),
                     "attempts": str(sum(attempts)), "infrastructure_failures": str(sum(infra)),
                     "selected_seed_counts": ";".join(
                         f"seed{seed:03d}={min(2, count)}" for seed, count in zip(gen.SEEDS, valid_counts))})
    return rows


def write_status() -> None:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    rows = status_rows()
    with (RESULTS_ROOT / "run_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    selection = []
    attempts = []
    for config in CONFIGS:
        for seed in gen.SEEDS:
            chosen = set(selected(config, seed))
            for index, (path, valid, reason) in enumerate(candidates(config, seed), 1):
                attempts.append({"config": config.slug, "seed": seed, "attempt_index": index,
                                 "run_dir": str(path.resolve()), "quality_status": "QUALIFIED" if valid else "REJECTED",
                                 "reason": reason, "selected": int(path in chosen)})
            for index, path in enumerate(selected(config, seed), 1):
                selection.append({"config": config.slug, "seed": seed, "selection_index": index,
                                  "run_dir": str(path.resolve()),
                                  "metadata_path": str((path / "metadata.json").resolve()),
                                  "request_results_path": str((path / "request_results.csv").resolve()),
                                  "trace_path": str((path / "trace_breakdown.csv").resolve())})
    for filename, records, columns in (
        ("latency_selection.csv", selection, ["config", "seed", "selection_index", "run_dir", "metadata_path", "request_results_path", "trace_path"]),
        ("attempt_manifest.csv", attempts, ["config", "seed", "attempt_index", "run_dir", "quality_status", "reason", "selected"]),
    ):
        with (RESULTS_ROOT / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(records)


def print_status() -> int:
    write_status()
    marker, reason = acceptance_marker_valid()
    print(f"campaign=r2 pilot_gate={'READY' if marker else 'BLOCKED'} ({reason}) "
          f"iid_requests={iid_request_count()}")
    for row in status_rows():
        print(f"{row['status']:10} {row['config']:48} selected={row['selected_runs']:>3}/20 attempts={row['attempts']}")
    return 0


def preflight_static() -> int:
    missing = [name for name in ("python3", "docker", "kathara", "git", "cmake", "tc")
               if shutil.which(name) is None]
    if missing:
        raise SystemExit(f"missing commands: {', '.join(missing)}")
    subprocess.run([sys.executable, str(Path(gen.__file__)), "--iid-requests", str(iid_request_count()), "--check"], check=True)
    subprocess.run([sys.executable, str(SCRIPT_DIR / "test_latency_v4_bf1024_h3_c512.py")], check=True)
    subprocess.run([sys.executable, str(SCRIPT_DIR / "test_latency_v4_analysis.py")], check=True)
    targets = ["test_packet_codec", "test_query_table_fallback", "test_node_staged_fallback", "rsync-node-dev", "oracle-direct-fetch"]
    subprocess.run(["cmake", "--build", "build", "--target", *targets, "--", "-j2"], cwd=REPO_ROOT, check=True)
    for target in targets[:3]:
        subprocess.run([str(REPO_ROOT / "build" / target)], cwd=REPO_ROOT, check=True)
    subprocess.run(["make", "test-core"], cwd=REPO_ROOT, check=True)
    subprocess.run(["kathara", "--version"], timeout=15, check=True)
    print("latency-v4 static preflight passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--phase", choices=["all", "controlled", "iid-zipf", "iid-uniform", "data-size"], default="all")
    sub.add_parser("pilot")
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--method", choices=["nc", "known-owner", "tcp-direct"],
                       default="nc")
    smoke.add_argument("--topology", type=int, choices=[16, 32], default=16)
    sub.add_parser("approve-pilot")
    sub.add_parser("status")
    sub.add_parser("validate")
    sub.add_parser("preflight-static")
    args = parser.parse_args()
    if args.command == "run":
        return run_phase(args.phase)
    if args.command == "pilot":
        return run_pilots()
    if args.command == "smoke":
        return run_smoke(args.method, args.topology)
    if args.command == "approve-pilot":
        return approve_pilots()
    if args.command == "status":
        return print_status()
    if args.command == "preflight-static":
        return preflight_static()
    write_status()
    print_status()
    if all(config_ready(config) for config in CONFIGS):
        subprocess.run([sys.executable, str(SCRIPT_DIR / "analyze_latency_v4_bf1024_h3_c512.py")],
                       cwd=REPO_ROOT, check=True)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
