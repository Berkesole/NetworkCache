#!/usr/bin/env python3
"""Run the immutable, non-formal latency-v3 smoke matrix in the foreground."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import run_latency_v3_suite as suite


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_DIR = SCRIPT_DIR.parent
ROOT = EXP_DIR.parents[2]
SMOKE_ROOT = EXP_DIR / "results/latency-v3-smoke/verified-current"


@dataclass(frozen=True)
class Smoke:
    name: str
    scenario: str
    method: str
    config_slug: Optional[str]


SMOKES = (
    Smoke("controlled16-nc-64k", "kua-dc-v3-16-latency-v3-controlled-b65536-seed001", "nc", "controlled-16-nc-b65536"),
    Smoke("controlled32-known-64k", "kua-dc-v3-32-latency-v3-controlled-b65536-seed001", "known-owner", "controlled-32-known-owner-b65536"),
    Smoke("zipf16-nc-64k", "kua-dc-v3-16-latency-v3-iid-zipf-b65536-seed001", "nc", "iid-zipf-16-nc-b65536"),
    Smoke("uniform32-nc-64k", "kua-dc-v3-32-latency-v3-iid-uniform-b65536-seed001", "nc", "iid-uniform-32-nc-b65536"),
    Smoke("controlled32-nc-1m", "kua-dc-v3-32-latency-v3-controlled-b1048576-seed001", "nc", "controlled-32-nc-b1048576"),
    Smoke("controlled32-known-1m", "kua-dc-v3-32-latency-v3-controlled-b1048576-seed001", "known-owner", "controlled-32-known-owner-b1048576"),
    Smoke("tcp32-1k", "kua-dc-v3-32-latency-v3-controlled-b1024-seed001", "tcp-direct", "controlled-32-tcp-direct-b1024"),
    Smoke("tcp32-64k", "kua-dc-v3-32-latency-v3-controlled-b65536-seed001", "tcp-direct", "controlled-32-tcp-direct-b65536"),
    Smoke("tcp32-1m", "kua-dc-v3-32-latency-v3-controlled-b1048576-seed001", "tcp-direct", "controlled-32-tcp-direct-b1048576"),
    Smoke("pilot32-nc-4m-single", "kua-dc-v3-32-latency-v3-controlled-b4194304-seed001", "nc", None),
)


def validate(smoke: Smoke, path: Path):
    if smoke.config_slug:
        config = next(config for config in suite.CONFIGS if config.slug == smoke.config_slug)
        return suite.validate_run(path, config)
    try:
        metadata = json.loads((path / "metadata.json").read_text())
        rows = suite.read_csv(path / "request_results.csv")
        okay = (metadata.get("status") == "ok" and metadata.get("value_size_bytes") == 4194304 and
                len(rows) == 1 and rows[0].get("status") == "ok" and
                rows[0].get("actual_value_bytes") == "4194304" and
                suite.validate_network_state(path / "network_state.txt", 32) and
                suite.validate_clock_state(path / "clock_state.tsv", 32))
        return okay, "ok" if okay else "pilot_gate"
    except Exception:
        return False, "pilot_files"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[smoke.name for smoke in SMOKES])
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--offline-build", action="store_true")
    parser.add_argument("--root", type=Path, default=SMOKE_ROOT)
    args = parser.parse_args()
    selected = [smoke for smoke in SMOKES if not args.case or smoke.name == args.case]
    built = args.skip_build
    rows = []
    for smoke in selected:
        path = args.root / smoke.name
        valid, reason = validate(smoke, path) if path.exists() else (False, "missing")
        if valid:
            print(f"SKIP {smoke.name}: already verified")
            rows.append({"smoke": smoke.name, "status": "READY", "reason": "resume_skip", "run_dir": str(path)})
            continue
        if path.exists():
            print(f"REJECT {smoke.name}: immutable prior smoke is {reason}; use a new root", flush=True)
            rows.append({"smoke": smoke.name, "status": "REJECTED", "reason": reason, "run_dir": str(path)})
            continue
        env = os.environ.copy()
        if built:
            env["SKIP_BUILD"] = "1"
        if args.offline_build and not built:
            env["LATENCY_V3_DOCKERFILE_PATH"] = str(ROOT / "Dockerfile.latency-v3")
        command = [str(SCRIPT_DIR / "run_latency_v3_once.sh"), smoke.scenario, smoke.method, str(path)]
        subprocess.run(command, cwd=ROOT, env=env, check=False)
        try:
            metadata = json.loads((path / "metadata.json").read_text())
            built = built or metadata.get("status") != "driver_failed"
        except Exception:
            built = False
        valid, reason = validate(smoke, path)
        print(f"{'READY' if valid else 'REJECTED'} {smoke.name}: {reason}", flush=True)
        rows.append({"smoke": smoke.name, "status": "READY" if valid else "REJECTED", "reason": reason, "run_dir": str(path)})
    args.root.mkdir(parents=True, exist_ok=True)
    with (args.root / "smoke_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["smoke", "status", "reason", "run_dir"])
        writer.writeheader(); writer.writerows(rows)
    return 0 if len(rows) == len(selected) and all(row["status"] == "READY" for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
