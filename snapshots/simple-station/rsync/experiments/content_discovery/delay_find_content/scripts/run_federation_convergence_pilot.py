#!/usr/bin/env python3
"""Preflight, run, validate, or inspect the one-run Federation convergence pilot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import analyze_federation_convergence_pilot as analysis
import generate_federation_convergence_pilot as gen


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
DEFAULT_RESULTS = gen.EXP / "results" / gen.SUITE
RUN_STEM = "catalog512-zipf-nc-seed001"
MAX_ATTEMPTS = 4


def attempts(results: Path) -> list[Path]:
    return sorted((results / "attempts").glob(f"{RUN_STEM}-attempt[0-9][0-9][0-9]"))


def next_attempt(results: Path) -> Path:
    existing = attempts(results)
    if len(existing) >= MAX_ATTEMPTS:
        raise RuntimeError(f"pilot attempt budget exhausted: {len(existing)}/{MAX_ATTEMPTS}")
    return results / "attempts" / f"{RUN_STEM}-attempt{len(existing) + 1:03d}"


def command(args: list[str], *, check: bool = True) -> str:
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def docker_image() -> str:
    return command(
        ["docker", "image", "inspect", "rsync-router:ruikekkk-kathara", "--format", "{{.Id}}"]
    )


def preflight(results: Path) -> dict:
    gen.validate(gen.DEFAULT_ROOT)
    if shutil.which("docker") is None or shutil.which("kathara") is None:
        raise RuntimeError("docker and kathara must be installed")
    image = docker_image()
    if image != analysis.EXPECTED_IMAGE:
        raise RuntimeError(f"frozen runtime image mismatch: {image}")
    plugin = command(
        ["docker", "plugin", "inspect", "kathara/katharanp_vde:amd64", "--format", "{{.Enabled}}"]
    )
    if plugin != "true":
        raise RuntimeError("Kathara VDE Docker plugin is not enabled")
    active = command(["docker", "ps", "--format", "{{.Names}}"])
    kathara = [name for name in active.splitlines() if "kathara" in name.lower()]
    if kathara:
        raise RuntimeError(f"another Kathara lab is active: {kathara[:3]}")
    memory = int(Path("/proc/meminfo").read_text().split("MemAvailable:", 1)[1].split()[0]) * 1024
    disk = shutil.disk_usage(results.parent).free
    if memory < 32 * 1024**3:
        raise RuntimeError("pilot requires at least 32 GiB available memory")
    if disk < 100 * 1024**3:
        raise RuntimeError("pilot requires at least 100 GiB free disk")
    return {
        "status": "READY",
        "suite": gen.SUITE,
        "scenario": gen.SCENARIO,
        "runtime_image_id": image,
        "post_batches": gen.POST_BINS,
        "requests": gen.EXPECTED_REQUESTS,
        "available_memory_bytes": memory,
        "available_disk_bytes": disk,
        "results_root": str(results),
    }


def run(results: Path) -> int:
    state = preflight(results)
    output = next_attempt(results)
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "FEDERATION_FALLBACK_SUITE": gen.SUITE,
            "FEDERATION_V3_FALLBACK_WORKLOAD_ROOT": str(gen.DEFAULT_ROOT),
            "FEDERATION_FALLBACK_DATASET_LABEL": "one-seed 64-POST-batch convergence pilot",
            "FEDERATION_V3_FALLBACK_BUILD_NO_CACHE": "0",
            "SKIP_BUILD": "1",
            # The application still exits promptly on the controller's stop
            # marker.  INT_MAX removes the one-hour fixed-loop lifetime that
            # invalidated attempt001 while preserving the frozen image.
            "RSYNC_LOOP_ITERATIONS": "2147483647",
        }
    )
    print(json.dumps(state, indent=2, sort_keys=True), flush=True)
    completed = subprocess.run(
        [
            str(HERE / "run_federation_v3_bf1024_h3_fallback_once.sh"),
            gen.SCENARIO,
            "nc",
            str(output),
        ],
        cwd=REPO,
        env=env,
        check=False,
    )
    if completed.returncode:
        return completed.returncode
    return validate(output)


def validate(output: Path) -> int:
    requests, trace = analysis.validate_run(output)
    batches = analysis.batch_rows(requests, trace)
    windows = analysis.window_rows(batches)
    report = analysis.convergence(windows, batches)
    analysis_dir = output / "convergence_analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    analysis.write_csv(analysis_dir / "batch_means.csv", batches)
    analysis.write_csv(analysis_dir / "window_means.csv", windows)
    (analysis_dir / "convergence_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def status(results: Path) -> int:
    existing = attempts(results)
    if not existing:
        print(json.dumps({"status": "NOT_STARTED", "attempts": 0}, indent=2))
        return 0
    output = existing[-1]
    summary = {}
    for name in ("completion_summary.json", "metadata.json"):
        path = output / name
        if path.is_file():
            summary[name.removesuffix(".json")] = json.loads(path.read_text(encoding="utf-8"))
    report = output / "convergence_analysis" / "convergence_report.json"
    if report.is_file():
        summary["convergence"] = json.loads(report.read_text(encoding="utf-8"))
    print(json.dumps({"status": "PRESENT", "attempts": len(existing),
                      "run_dir": str(output), **summary}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--execute", action="store_true")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--run-dir", type=Path)
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(preflight(args.results_root), indent=2, sort_keys=True))
        return 0
    if args.command == "run":
        if not args.execute:
            raise SystemExit("run requires --execute")
        gen.generate(gen.DEFAULT_ROOT)
        gen.validate(gen.DEFAULT_ROOT)
        return run(args.results_root)
    if args.command == "validate":
        existing = attempts(args.results_root)
        if args.run_dir is None and not existing:
            raise RuntimeError("no pilot attempt exists")
        return validate(args.run_dir or existing[-1])
    return status(args.results_root)


if __name__ == "__main__":
    raise SystemExit(main())
