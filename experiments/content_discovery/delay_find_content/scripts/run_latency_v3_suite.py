#!/usr/bin/env python3
"""Run, resume, select, and validate the isolated latency-v3 matrix."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import generate_latency_v3 as gen


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_DIR = SCRIPT_DIR.parent
REPO_ROOT = EXP_DIR.parents[2]
RESULTS_ROOT = EXP_DIR / "results" / "latency-v3"
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
        return gen.scenario_name(f"kua-dc-v3-{self.topology}", self.workload, self.size, seed)


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


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists(): return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_dirs(config: Config, seed: int) -> List[Path]:
    root = RESULTS_ROOT / "attempts" / config.slug / f"seed{seed:03d}"
    return sorted(path for path in root.glob("attempt[0-9][0-9][0-9]") if path.is_dir()) if root.exists() else []


def is_build_failure(path: Path) -> bool:
    """A failed image build happened before an experimental run existed."""
    try:
        metadata = json.loads((path / "metadata.json").read_text())
        stderr = (path / "driver.stderr.log").read_text(errors="replace")
    except Exception:
        return False
    return metadata.get("status") == "driver_failed" and "FAIL: docker build" in stderr


def quota_run_dirs(config: Config, seed: int) -> List[Path]:
    # Immutable build diagnostics remain on disk but do not consume one of the
    # four protocol/runtime attempts allowed by the experimental design.
    return [path for path in run_dirs(config, seed) if not is_build_failure(path)]


def validate_network_state(path: Path, topology: int) -> bool:
    if not path.exists(): return False
    text = path.read_text(encoding="utf-8", errors="replace")
    expected_interfaces = 64 if topology == 16 else 128
    netem = [line for line in text.splitlines() if line.startswith("qdisc netem")]
    if len(netem) != expected_interfaces: return False
    shaped = all("delay 2ms" in line and ("rate 1Gbit" in line or "rate 1000Mbit" in line)
                 and "limit 1000" in line for line in netem)
    mtu_lines = [line for line in text.splitlines() if ": eth" in line and "mtu 1500" in line]
    return shaped and len(mtu_lines) >= expected_interfaces


def validate_clock_state(path: Path, topology: int) -> bool:
    rows = read_csv(path)
    if len(rows) != topology:
        return False
    try:
        values = [int(row["monotonic_ns"]) for row in rows]
        offsets = {row["timens_offsets_sha256"] for row in rows}
    except (KeyError, TypeError, ValueError):
        return False
    # Identical Linux time-namespace offsets are the decisive comparability
    # gate. The five-second envelope only rejects a stalled probe; launching
    # 32 concurrent `kathara exec` clients can itself take over one second.
    return len(offsets) == 1 and max(values) - min(values) <= 5_000_000_000


def trace_coverage_ok(trace: Sequence[Dict[str, str]], workload: str, measurement_count: int) -> bool:
    complete = [row for row in trace if row.get("phase") == "measurement" and row.get("trace_complete") == "1"]
    if len(complete) < math.ceil(measurement_count * 0.99):
        return False
    field = "round_index" if workload == "controlled" else "request_bin"
    minimum = 15 if workload == "controlled" else 19
    expected_bins = 8 if workload == "controlled" else 12
    counts: Dict[str, int] = {}
    for row in complete:
        counts[row.get(field, "")] = counts.get(row.get(field, ""), 0) + 1
    return all(counts.get(str(index), 0) >= minimum for index in range(1, expected_bins + 1))


def validate_run(path: Path, config: Config) -> Tuple[bool, str]:
    try:
        metadata = json.loads((path / "metadata.json").read_text())
        schedule = read_csv(path / "schedule.tsv") if False else None
        with (path / "schedule.tsv").open(newline="", encoding="utf-8") as handle:
            schedule_rows = list(csv.DictReader(handle, delimiter="\t"))
        results = read_csv(path / "request_results.csv")
    except Exception:
        return False, "missing_or_invalid_files"
    if is_build_failure(path):
        return False, "infrastructure_build"
    if metadata.get("status") != "ok" or metadata.get("wire_version") != 2:
        return False, "driver_or_wire"
    if metadata.get("query_table_timeout_ms") != 4500:
        return False, "query_timeout"
    if metadata.get("max_hop_count") != 8:
        return False, "max_hop_count"
    if metadata.get("cache_backend") != "redis" and config.method != "tcp-direct":
        return False, "cache_backend"
    if len(results) != len(schedule_rows):
        return False, "row_count"
    by_index = {row["request_index"]: row for row in results}
    measurement = [row for row in schedule_rows if row["phase"] == "measurement"]
    expected_stable_ms = 250 if config.size <= 1024 else 500 if config.size <= 65536 else 2000
    for expected in schedule_rows:
        actual = by_index.get(expected["request_index"])
        if not actual or actual.get("status") != "ok": return False, "request_status"
        if actual.get("actual_value_bytes") != expected["value_size_bytes"]:
            return False, "value_size"
        if actual.get("actual_fnv1a64") != expected["expected_fnv1a64"]:
            return False, "value_hash"
        if int(actual.get("end_to_end_latency_ns", "-1")) < 0:
            return False, "latency"
        if actual.get("quiescence_ack_count") != str(config.topology) or actual.get("quiescence_stable_ms") != str(expected_stable_ms):
            return False, "quiescence"
    intervals = sorted((int(row["request_start_mono_ns"]), int(row["app_receive_mono_ns"])) for row in results)
    if any(current[0] < previous[1] for previous, current in zip(intervals, intervals[1:])):
        return False, "overlap"
    if not validate_network_state(path / "network_state.txt", config.topology):
        return False, "network_state"
    if not validate_clock_state(path / "clock_state.tsv", config.topology):
        return False, "clock_gate"
    if config.method == "tcp-direct":
        measurement_results = [row for row in results if row.get("phase") == "measurement"]
        if any(not row.get("discovery_latency_ns") or not row.get("return_path_latency_ns") for row in measurement_results):
            return False, "tcp_breakdown"
        return True, "ok"
    trace = read_csv(path / "trace_breakdown.csv")
    if not trace_coverage_ok(trace, config.workload, len(measurement)):
        return False, "trace_coverage"
    logs = "\n".join(path.read_text(errors="replace") for path in sorted((path / "logs").glob("node*.log")))
    if logs.count("cache backend: redis") < config.topology:
        return False, "redis_not_ready"
    return True, "ok"


def candidates(config: Config, seed: int) -> List[Tuple[Path, bool, str]]:
    return [(path, *validate_run(path, config)) for path in run_dirs(config, seed)]


def select_earliest(items: Sequence[Tuple[Path, bool, str]], count: int = TARGET_PER_SEED) -> List[Path]:
    """Select only by immutable manifest order; latency values are not inputs."""
    return [path for path, valid, _ in items if valid][:count]


def next_attempt(config: Config, seed: int) -> Tuple[int, Path]:
    existing = run_dirs(config, seed)
    index = len(existing) + 1
    return index, RESULTS_ROOT / "attempts" / config.slug / f"seed{seed:03d}" / f"attempt{index:03d}"


def selected(config: Config, seed: int) -> List[Path]:
    return select_earliest(candidates(config, seed))


def run_one(config: Config, seed: int, skip_build: bool) -> bool:
    attempt_index, output = next_attempt(config, seed)
    if len(quota_run_dirs(config, seed)) >= MAX_ATTEMPTS_PER_SEED: return skip_build
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if skip_build: env["SKIP_BUILD"] = "1"
    command = [str(SCRIPT_DIR / "run_latency_v3_once.sh"), config.scenario(seed), config.method, str(output)]
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=False)
    if is_build_failure(output):
        raise RuntimeError(
            f"latency-v3 image build failed; diagnostic retained at {output}. "
            "The phase was stopped before another configuration could repeat the same failure."
        )
    try:
        metadata = json.loads((output / "metadata.json").read_text())
        return metadata.get("status") != "driver_failed"
    except Exception:
        return False


def phase_configs(phase: str) -> List[Config]:
    return list(CONFIGS) if phase == "all" else [config for config in CONFIGS if config.phase == phase]


def run_phase(phase: str) -> int:
    configs = phase_configs(phase)
    built = False
    for attempt_slot in range(1, MAX_ATTEMPTS_PER_SEED + 1):
        for seed in gen.SEEDS:
            block = list(configs)
            random.Random(f"latency-v3-block:{phase}:{seed}:{attempt_slot}").shuffle(block)
            for config in block:
                if len(selected(config, seed)) >= TARGET_PER_SEED: continue
                if len(quota_run_dirs(config, seed)) >= attempt_slot: continue
                try:
                    built = run_one(config, seed, built) or built
                except RuntimeError as exc:
                    write_status()
                    print(f"INFRASTRUCTURE_ERROR: {exc}", file=sys.stderr)
                    return 3
    write_status()
    return 0 if all(config_ready(config) for config in configs) else 2


def config_ready(config: Config) -> bool:
    return all(len(selected(config, seed)) == TARGET_PER_SEED for seed in gen.SEEDS)


def status_rows() -> List[Dict[str, str]]:
    rows = []
    for config in CONFIGS:
        valid_counts = [len([1 for _, valid, _ in candidates(config, seed) if valid]) for seed in gen.SEEDS]
        attempt_counts = [len(quota_run_dirs(config, seed)) for seed in gen.SEEDS]
        infrastructure_counts = [len(run_dirs(config, seed)) - count
                                 for seed, count in zip(gen.SEEDS, attempt_counts)]
        rows.append({"phase": config.phase, "config": config.slug, "workload": config.workload,
                     "topology": str(config.topology), "method": config.method,
                     "value_size_bytes": str(config.size), "status": "READY" if config_ready(config) else "NEEDS_MORE",
                     "selected_runs": str(sum(min(TARGET_PER_SEED, count) for count in valid_counts)),
                     "attempts": str(sum(attempt_counts)),
                     "infrastructure_failures": str(sum(infrastructure_counts)),
                     "selected_seed_counts": ";".join(f"seed{seed:03d}={min(2,count)}" for seed, count in zip(gen.SEEDS, valid_counts))})
    return rows


def write_status() -> None:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    rows = status_rows()
    columns = list(rows[0])
    with (RESULTS_ROOT / "run_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    selection = []
    qualified = []
    attempts = []
    for config in CONFIGS:
        for seed in gen.SEEDS:
            selected_paths = set(selected(config, seed))
            for attempt_index, (path, valid, reason) in enumerate(candidates(config, seed), 1):
                attempts.append({"config": config.slug, "seed": seed,
                                 "attempt_index": attempt_index, "run_dir": str(path.resolve()),
                                 "quality_status": "QUALIFIED" if valid else "REJECTED",
                                 "reason": reason, "selected": "1" if path in selected_paths else "0"})
                if valid:
                    qualified.append({"config": config.slug, "seed": seed,
                                      "attempt_index": attempt_index, "run_dir": str(path.resolve()),
                                      "metadata_path": str((path / "metadata.json").resolve()),
                                      "request_results_path": str((path / "request_results.csv").resolve()),
                                      "trace_path": str((path / "trace_breakdown.csv").resolve())})
            for index, path in enumerate(selected(config, seed), 1):
                selection.append({"config": config.slug, "seed": seed, "selection_index": index,
                                  "run_dir": str(path.resolve()), "metadata_path": str((path / "metadata.json").resolve()),
                                  "request_results_path": str((path / "request_results.csv").resolve()),
                                  "trace_path": str((path / "trace_breakdown.csv").resolve())})
    with (RESULTS_ROOT / "latency_selection.csv").open("w", newline="", encoding="utf-8") as handle:
        columns = ["config", "seed", "selection_index", "run_dir", "metadata_path", "request_results_path", "trace_path"]
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(selection)
    for filename, records, columns in (
        ("qualified_attempts.csv", qualified,
         ["config", "seed", "attempt_index", "run_dir", "metadata_path", "request_results_path", "trace_path"]),
        ("attempt_manifest.csv", attempts,
         ["config", "seed", "attempt_index", "run_dir", "quality_status", "reason", "selected"]),
    ):
        with (RESULTS_ROOT / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(records)


def print_status() -> int:
    write_status()
    for row in status_rows():
        print(f"{row['status']:10} {row['config']:48} selected={row['selected_runs']:>3}/20 "
              f"attempts={row['attempts']} infra={row['infrastructure_failures']}")
    return 0


def preflight_static() -> int:
    required = ("python3", "docker", "kathara", "git", "cmake", "tc")
    missing = [name for name in required if shutil.which(name) is None]
    if missing: raise SystemExit(f"missing commands: {', '.join(missing)}")
    for image in ("rsync-router:phase5c-redis-default-cache", "rsync-router:ruikekkk-kathara"):
        subprocess.run(["docker", "image", "inspect", image], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run([sys.executable, str(SCRIPT_DIR / "generate_latency_v3.py"), "--check"], check=True, cwd=REPO_ROOT)
    subprocess.run([sys.executable, str(SCRIPT_DIR / "test_latency_v3.py")], check=True, cwd=REPO_ROOT)
    subprocess.run(["cmake", "--build", "build", "--target", "test_packet_codec", "rsync-node-dev", "oracle-direct-fetch", "--", "-j"], check=True, cwd=REPO_ROOT)
    subprocess.run([str(REPO_ROOT / "build" / "test_packet_codec")], check=True, cwd=REPO_ROOT)
    subprocess.run(["make", "test-core"], check=True, cwd=REPO_ROOT)
    # `kathara check` walks every pre-existing lab and can block indefinitely
    # on unrelated, long-lived labs.  Static preflight checks the CLI here;
    # the smoke stage below is the authoritative backend/network check.
    kathara_timeout = int(os.environ.get("LATENCY_V3_KATHARA_CHECK_TIMEOUT_SECONDS", "15"))
    try:
        subprocess.run(["kathara", "--version"], check=True, cwd=REPO_ROOT,
                       timeout=kathara_timeout)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(
            f"kathara --version did not finish within {kathara_timeout}s; "
            "inspect the Kathara installation before running experiments"
        ) from exc
    print("latency-v3 static preflight passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run"); run.add_argument("--phase", choices=["all", "controlled", "iid-zipf", "iid-uniform", "data-size"], default="all")
    sub.add_parser("status"); sub.add_parser("validate"); sub.add_parser("preflight-static")
    args = parser.parse_args()
    if args.command == "run": return run_phase(args.phase)
    if args.command == "status": return print_status()
    if args.command == "preflight-static": return preflight_static()
    write_status(); print_status()
    if all(config_ready(config) for config in CONFIGS):
        subprocess.run([sys.executable, str(SCRIPT_DIR / "analyze_latency_v3.py")], cwd=REPO_ROOT, check=True)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
