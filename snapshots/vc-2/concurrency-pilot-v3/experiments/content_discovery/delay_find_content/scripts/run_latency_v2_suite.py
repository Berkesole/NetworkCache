#!/usr/bin/env python3
"""Run, resume, collect, and validate the Kathara latency-v2 matrix."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import collect_success_runs
import curate_latency_v2
import generate_latency_v2


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_DIR = SCRIPT_DIR.parent
REPO_ROOT = EXP_DIR.parents[2]
RESULTS_ROOT = EXP_DIR / "results" / "latency-v2"
STATUS_PATH = RESULTS_ROOT / "run_status.csv"
SEEDS = generate_latency_v2.SEEDS
TARGET_RUNS = 20
MAX_ATTEMPTS = 40


class ClockValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    phase: str
    workload: str
    spacing_ms: int
    topology: int
    method: str

    @property
    def slug(self) -> str:
        return f"{self.workload}-s{self.spacing_ms}-{self.topology}-{self.method}"

    @property
    def scenarios(self) -> List[str]:
        topology = f"kua-dc-v3-{self.topology}"
        return [
            generate_latency_v2.scenario_name(topology, self.workload, self.spacing_ms, seed)
            for seed in SEEDS
        ]


CONFIGS = [
    *[
        Config("controlled", "controlled", 2000, topology, method)
        for topology in (16, 32)
        for method in ("nc", "known-owner")
    ],
    *[
        Config("iid-zipf", "iid-zipf", 2000, topology, method)
        for topology in (16, 32)
        for method in ("nc", "known-owner")
    ],
    *[
        Config("iid-uniform", "iid-uniform", 2000, topology, "nc")
        for topology in (16, 32)
    ],
    Config("load-sensitivity", "iid-zipf", 250, 32, "nc"),
    Config("load-sensitivity", "iid-zipf", 250, 32, "known-owner"),
]


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def attempt_dirs(config: Config) -> List[Path]:
    return sorted(RESULTS_ROOT.glob(f"{config.slug}-attempts-*"))


def curated_dir(config: Config) -> Path:
    return RESULTS_ROOT / f"{config.slug}-runs20"


def resolve(batch: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else batch / path


def complete_candidates(config: Config) -> Dict[str, List[Dict[str, str]]]:
    return collect_success_runs.load_ok_rows(attempt_dirs(config), config.scenarios)


def curated_valid(config: Config) -> bool:
    output = curated_dir(config)
    rows = read_csv(output / "manifest.csv")
    if len(rows) != TARGET_RUNS or any(row.get("status") != "ok" for row in rows):
        return False
    if not set(config.scenarios) <= {row.get("scenario", "") for row in rows}:
        return False
    merged = read_csv(output / "convergence_merged.csv")
    if not merged or any(row.get("trace_complete") != "1" for row in merged):
        return False
    expected = 0
    for row in rows:
        expected += collect_success_runs.expected_measurement_rows(resolve(output, row.get("metadata_path", "")))
    return len(merged) == expected


def try_collect(config: Config) -> bool:
    attempts = attempt_dirs(config)
    if not attempts:
        return False
    command = [
        sys.executable,
        str(SCRIPT_DIR / "collect_success_runs.py"),
        "--output",
        str(curated_dir(config)),
        "--target-total",
        str(TARGET_RUNS),
        "--preferred-per-scenario",
        "4",
        "--scenarios",
        ",".join(config.scenarios),
        "--overwrite",
        *[str(path) for path in attempts],
    ]
    result = subprocess.run(command, cwd=REPO_ROOT, check=False)
    return result.returncode == 0 and curated_valid(config)


def total_attempts(config: Config) -> int:
    return sum(len(read_csv(path / "manifest.csv")) for path in attempt_dirs(config))


def next_attempt_dir(config: Config) -> tuple[str, int]:
    index = 1
    while (RESULTS_ROOT / f"{config.slug}-attempts-{index:03d}").exists():
        index += 1
    return f"{config.slug}-attempts-{index:03d}", index


def wait_seconds(config: Config) -> int:
    metadata = EXP_DIR / "workloads" / "kua-dc-v3" / "generated" / f"{config.scenarios[0]}.metadata.json"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    return int((int(data["scenario_ticks"]) * 25 + 999) // 1000 + 30)


def run_attempt(config: Config, repeats: int) -> None:
    run_group, _index = next_attempt_dir(config)
    runner = (
        SCRIPT_DIR / "run_kathara_batch.sh"
        if config.method == "nc"
        else SCRIPT_DIR / "run_oracle_nc_unicast_batch.sh"
    )
    env = os.environ.copy()
    env.update(
        {
            "RESULTS_ROOT": str(RESULTS_ROOT),
            "RSYNC_EVENT_TRACE": "1",
            "RSYNC_EVENT_TRACE_FLUSH_EVERY": env.get("RSYNC_EVENT_TRACE_FLUSH_EVERY", "64"),
            "KATHARA_NODE_LOOP_SLEEP_MS": "25",
            "KATHARA_LINK_DELAY_MS": "2",
            "KATHARA_WAIT_SECONDS": str(wait_seconds(config)),
        }
    )
    command = [
        str(runner),
        "--scenarios",
        ",".join(config.scenarios),
        "--repeats",
        str(repeats),
        "--allow-incomplete",
        "--run-group-id",
        run_group,
    ]
    print(f"[{config.slug}] running {repeats} repeat(s) for each of five seeds")
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)
    batch = RESULTS_ROOT / run_group
    subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "diagnose_completion.py"), str(batch)],
        cwd=REPO_ROOT,
        check=True,
    )
    validate_batch_clocks(batch)


def validate_batch_clocks(batch: Path) -> None:
    for row in read_csv(batch / "manifest.csv"):
        if row.get("status") == "driver_failed":
            continue
        run_dir = resolve(batch, row.get("summary_path", "")).parent
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "validate_trace_clocks.py"), str(run_dir)],
            cwd=REPO_ROOT,
            check=False,
        )
        if result.returncode != 0:
            raise ClockValidationError(f"monotonic clock gate failed for {run_dir}")


def latency_ready(config: Config) -> bool:
    _candidates, _selected, status = curate_latency_v2.build_selection(
        RESULTS_ROOT, [config.slug], check_clock=False, write=False
    )
    return status[config.slug]["latency_status"] == "READY"


def run_config(config: Config) -> bool:
    if latency_ready(config):
        print(f"[{config.slug}] LATENCY READY; skipping")
        return True
    if total_attempts(config) == 0:
        run_attempt(config, 4)
        if latency_ready(config):
            return True
    while total_attempts(config) < MAX_ATTEMPTS:
        run_attempt(config, 1)
        if latency_ready(config):
            return True
    print(f"[{config.slug}] LATENCY NEEDS_MORE after {total_attempts(config)} attempts", file=sys.stderr)
    return False


def status_rows(
    configs: Sequence[Config] = CONFIGS,
    check_clocks: bool = False,
) -> List[Dict[str, str]]:
    _candidates, _selected, latency_status = curate_latency_v2.build_selection(
        RESULTS_ROOT,
        [config.slug for config in configs],
        check_clock=check_clocks,
        write=check_clocks,
    )
    rows: List[Dict[str, str]] = []
    for config in configs:
        by_scenario = complete_candidates(config) if attempt_dirs(config) else {scenario: [] for scenario in config.scenarios}
        seed_counts = [len(by_scenario.get(scenario, [])) for scenario in config.scenarios]
        curated_manifest = read_csv(curated_dir(config) / "manifest.csv")
        curated_merged = read_csv(curated_dir(config) / "convergence_merged.csv")
        expected_rows = sum(
            collect_success_runs.expected_measurement_rows(
                resolve(curated_dir(config), row.get("metadata_path", ""))
            )
            for row in curated_manifest
        )
        complete_rows = sum(row.get("trace_complete") == "1" for row in curated_merged)
        strict_status = "READY" if curated_valid(config) else "NEEDS_MORE"
        row = {
                "phase": config.phase,
                "config": config.slug,
                "topology": str(config.topology),
                "method": config.method,
                "workload": config.workload,
                "spacing_ms": str(config.spacing_ms),
                "attempts": str(total_attempts(config)),
                "trace_complete_candidates": str(sum(seed_counts)),
                "seed_counts": ";".join(f"seed{seed:03d}={count}" for seed, count in zip(SEEDS, seed_counts)),
                "curated_runs": str(len(curated_manifest)),
                "expected_measurement_rows": str(expected_rows),
                "actual_measurement_rows": str(len(curated_merged)),
                "trace_complete_rows": str(complete_rows),
                "status": strict_status,
                "strict_status": strict_status,
        }
        row.update({key: str(value) for key, value in latency_status[config.slug].items()})
        rows.append(row)
    return rows


def write_status(rows: Sequence[Dict[str, str]]) -> None:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    columns = [
        "phase", "config", "topology", "method", "workload", "spacing_ms",
        "attempts", "trace_complete_candidates", "seed_counts", "curated_runs",
        "expected_measurement_rows", "actual_measurement_rows", "trace_complete_rows", "status",
        "strict_status", "latency_status", "selected_runs", "selected_seed_counts",
        "usable_measurement_rows", "scheduled_measurement_rows", "min_usable_rows_per_bin",
    ]
    with STATUS_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def print_status(rows: Sequence[Dict[str, str]]) -> None:
    for row in rows:
        print(
            f"{row['latency_status']:10} {row['config']:48} "
            f"selected={row['selected_runs']:>2} usable={row['usable_measurement_rows']}/{row['scheduled_measurement_rows']} "
            f"{row['selected_seed_counts']} strict={row['strict_status']}"
        )


def selected_configs(phase: str) -> List[Config]:
    if phase == "all":
        return list(CONFIGS)
    selected = [config for config in CONFIGS if config.phase == phase]
    if not selected:
        raise SystemExit(f"unknown phase: {phase}")
    return selected


def preflight_static() -> int:
    required = ["python3", "kathara", "docker", "git", "make", "cmake", "g++"]
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        print(f"missing required commands: {', '.join(missing)}", file=sys.stderr)
        return 1
    subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "generate_latency_v2.py"), "--check"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run([sys.executable, str(SCRIPT_DIR / "test_latency_v2.py")], cwd=REPO_ROOT, check=True)
    subprocess.run(["make", "test-core"], cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["cmake", "--build", "build", "--target", "rsync-node-dev", "--", "-j"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(["kathara", "check"], cwd=REPO_ROOT, check=True)
    print("latency-v2 static preflight passed")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--phase", default="all", choices=["all", "controlled", "iid-zipf", "iid-uniform", "load-sensitivity"])
    subparsers.add_parser("status")
    subparsers.add_parser("validate")
    subparsers.add_parser("preflight-static")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "preflight-static":
        return preflight_static()
    if args.command == "run":
        ok = True
        for config in selected_configs(args.phase):
            try:
                ok = run_config(config) and ok
            except ClockValidationError as exc:
                print(f"latency-v2 stopped: {exc}", file=sys.stderr)
                return 1
            except subprocess.CalledProcessError as exc:
                print(f"[{config.slug}] infrastructure failure: {exc}", file=sys.stderr)
                ok = False
        rows = status_rows()
        write_status(rows)
        print_status(rows)
        return 0 if ok else 2
    rows = status_rows(check_clocks=args.command == "validate")
    write_status(rows)
    print_status(rows)
    if args.command == "validate":
        ready = all(row["latency_status"] == "READY" for row in rows)
        if ready:
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "aggregate_latency_v2.py"),
                    str(RESULTS_ROOT),
                    "--selection",
                    str(RESULTS_ROOT / "latency_selection.csv"),
                    "--candidates",
                    str(RESULTS_ROOT / "latency_candidates.csv"),
                    "--output",
                    str(RESULTS_ROOT / "analysis"),
                ],
                cwd=REPO_ROOT,
                check=True,
            )
        return 0 if ready else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
