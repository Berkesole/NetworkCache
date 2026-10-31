#!/usr/bin/env python3
"""Resume an identical frozen attempt queue on one federation domain."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import generate_workloads
import spec


HERE = Path(__file__).resolve().parent
SMOKE_MAX_ATTEMPTS = 10


def captured(path: Path) -> bool:
    result = path / "domain_result.json"
    pair = path / "pair_result.json"
    if not result.is_file() or not pair.is_file():
        return False
    try:
        return (
            json.loads(result.read_text(encoding="utf-8")).get("status") == "CAPTURED"
            and json.loads(pair.read_text(encoding="utf-8")).get("status") == "ok"
        )
    except (OSError, json.JSONDecodeError):
        return False


def attempt_index(path: Path) -> int:
    match = re.fullmatch(r"attempt([0-9]{3})", path.name)
    if match is None:
        raise ValueError(f"invalid attempt directory: {path}")
    return int(match.group(1))


def next_attempt_index(attempts: list[Path], floor: int) -> int:
    return max([floor - 1, *(attempt_index(path) for path in attempts)]) + 1


def execution_budget(campaign: dict, slot: str) -> tuple[int, int]:
    floor = int(campaign.get('execution_attempt_floors', {}).get(slot, 1))
    if floor < 1:
        raise ValueError('invalid execution attempt floor')
    return floor, floor + spec.MAX_ATTEMPTS - 1


def first_captured(attempts: list[Path], floor: int = 1) -> Path | None:
    return next(
        (path for path in sorted(attempts)
         if attempt_index(path) >= floor and captured(path)),
        None,
    )


def queue(stage: str, seeds: list[int]) -> list[tuple[str, int, int]]:
    if stage == "formal":
        allowed = set(seeds)
        return [(slot.method, slot.seed, slot.repetition)
                for slot in spec.formal_slots() if slot.seed in allowed]
    return [("nc", seed, 0) for seed in seeds]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=("domain-a", "domain-b"))
    parser.add_argument("--stage", required=True, choices=("smoke", "calibration", "formal"))
    parser.add_argument("--post-batches", required=True, type=int)
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--workload-root", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--assignment", type=Path)
    parser.add_argument("--attempt-floor", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("queue_runner requires --execute")
    if args.attempt_floor < 1:
        raise SystemExit("attempt floor must be positive")
    seeds = [int(value) for value in args.seeds.split(",") if value]
    if len(set(seeds)) != len(seeds) or any(seed not in spec.SEEDS for seed in seeds):
        raise SystemExit("invalid seed allowlist")
    spec.requests_per_run(args.post_batches)
    campaign = json.loads(args.campaign.read_text())
    for method, seed, repetition in queue(args.stage, seeds):
        scenario = generate_workloads.scenario_name(seed, args.post_batches)
        slot = f"{method}-seed{seed:03d}" + (
            f"-rep{repetition}" if repetition else f"-post{args.post_batches:03d}"
        )
        root = args.results_root / args.stage / args.role / slot
        attempts = sorted(root.glob("attempt*"))
        # A smoke recovery floor deliberately invalidates earlier captured
        # diagnostics after observer/validator changes.  Formal and calibration
        # retain captured data; a declared execution repair gets a frozen,
        # separate four-attempt budget without overwriting old attempts.
        successful = first_captured(attempts, args.attempt_floor)
        if successful:
            continue
        # Smoke is non-evidence infrastructure diagnosis.  Keep the frozen
        # four-attempt limit for calibration/formal slots, but allow recovery
        # from versioned orchestrator fixes without deleting earlier smoke raw.
        budget_floor, attempt_limit = execution_budget(campaign, slot)
        if args.stage == "smoke":
            budget_floor, attempt_limit = 1, SMOKE_MAX_ATTEMPTS
        resume_floor = int(campaign.get("resume_attempt_floors", {}).get(slot, args.attempt_floor))
        if not 1 <= resume_floor <= attempt_limit:
            raise ValueError(f"invalid resume floor for {slot}")
        next_index = next_attempt_index(attempts, max(resume_floor, budget_floor))
        while next_index <= attempt_limit:
            attempt = root / f"attempt{next_index:03d}"
            completed = subprocess.run([
                sys.executable, str(HERE / "domain_runner.py"), "--role", args.role,
                "--method", method, "--scenario", scenario,
                "--workload-root", str(args.workload_root), "--run-dir", str(attempt),
                "--token-file", str(args.token_file), "--execute",
                "--campaign", str(args.campaign),
                *(["--assignment", str(args.assignment)] if args.assignment else []),
            ], check=False)
            attempts = sorted(root.glob("attempt*"))
            if completed.returncode == 0 and captured(attempt):
                break
            next_index = next_attempt_index(attempts, args.attempt_floor)
        else:
            raise RuntimeError(
                f"attempt budget exhausted for {slot}: next={next_index} limit={attempt_limit}"
            )
        if first_captured(attempts, args.attempt_floor) is None:
            raise RuntimeError(f"no captured attempt for {slot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
