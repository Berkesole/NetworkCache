#!/usr/bin/env python3
"""Create and verify the immutable source/workload lock for formal experiments."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[2]
ROOT = EXP / "results" / "formal-bf1024-h3-campaign-r2"
LOCK = ROOT / "campaign_lock.json"
SOURCE_MANIFEST = ROOT / "source_manifest.csv"
WORKLOAD_MANIFEST = ROOT / "workload_manifest.csv"

SOURCE_PREFIXES = (
    "src/", "app/", "config/", "cmake/", "scripts/kathara/",
    "experiments/content_discovery/delay_find_content/scripts/",
)
SOURCE_FILES = {"CMakeLists.txt", "Makefile", "Dockerfile.latency-v3", "main.cpp"}
WORKLOAD_ROOTS = (
    EXP / "workloads" / "latency-v4-bf1024-h3-c512",
    EXP / "workloads" / "federation-v4-bf1024-h3-formal",
)
RUNTIME_IMAGE_TAG = "rsync-router:ruikekkk-kathara"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_paths() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO,
        text=True, capture_output=True, check=True,
    )
    paths = []
    for name in completed.stdout.splitlines():
        if name in SOURCE_FILES or name.startswith(SOURCE_PREFIXES):
            path = REPO / name
            if path.is_file() and not any(part in {"__pycache__", "results", "paper_assets"}
                                          for part in path.parts):
                paths.append(path)
    return sorted(set(paths))


def workload_paths() -> list[Path]:
    paths = []
    for root in WORKLOAD_ROOTS:
        if not root.exists():
            raise RuntimeError(f"missing generated workload root: {root}")
        paths.extend(path for path in root.rglob("*") if path.is_file())
    return sorted(paths)


def rows(paths: list[Path]) -> list[dict[str, object]]:
    return [{"path": str(path.relative_to(REPO)), "bytes": path.stat().st_size,
             "sha256": sha256(path)} for path in paths]


def fingerprint(records: list[dict[str, object]]) -> str:
    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def runtime_image_id() -> str:
    completed = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", RUNTIME_IMAGE_TAG],
        cwd=REPO, text=True, capture_output=True, check=False,
    )
    image_id = completed.stdout.strip()
    if completed.returncode or not image_id:
        raise RuntimeError(
            f"missing runtime image {RUNTIME_IMAGE_TAG}; run the smoke suite before creating the lock"
        )
    return image_id


def write_csv(path: Path, records: list[dict[str, object]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("path", "bytes", "sha256"))
        writer.writeheader()
        writer.writerows(records)
    temporary.replace(path)


def current() -> tuple[dict, list[dict[str, object]], list[dict[str, object]]]:
    sources = rows(source_paths())
    workloads = rows(workload_paths())
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, capture_output=True, check=True
    ).stdout.strip()
    lock = {
        "campaign": "networkcache-bf1024-h3-formal-r2",
        "git_commit": commit,
        "source_fingerprint": fingerprint(sources),
        "workload_fingerprint": fingerprint(workloads),
        "wire_version": 3,
        "bloom_bits": 1024,
        "bloom_hash_count": 3,
        "staged_fallback": True,
        "known_owner_exact_origin_state": True,
        "fallback_delay_ms": 1500,
        "query_table_timeout_ms": 7000,
        "controller_timeout_ms": 8000,
        "max_hop_count": 12,
        "latency_iid_requests": 4096,
        "federation_measurement_requests": 3584,
        "runtime_image_id": runtime_image_id(),
    }
    lock["campaign_fingerprint"] = hashlib.sha256(
        json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return lock, sources, workloads


def create() -> int:
    candidate, sources, workloads = current()
    ROOT.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        existing = json.loads(LOCK.read_text(encoding="utf-8"))
        for field in ("campaign", "source_fingerprint", "workload_fingerprint",
                      "wire_version", "bloom_bits", "bloom_hash_count",
                      "known_owner_exact_origin_state",
                      "latency_iid_requests", "federation_measurement_requests",
                      "runtime_image_id"):
            if existing.get(field) != candidate.get(field):
                raise SystemExit(f"campaign lock mismatch: {field}")
        print(existing["campaign_fingerprint"])
        return 0
    write_csv(SOURCE_MANIFEST, sources)
    write_csv(WORKLOAD_MANIFEST, workloads)
    temporary = LOCK.with_name(".campaign_lock.json.tmp")
    temporary.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(LOCK)
    print(candidate["campaign_fingerprint"])
    return 0


def verify() -> int:
    if not LOCK.exists():
        raise SystemExit("missing campaign lock; run formal_campaign.py create")
    existing = json.loads(LOCK.read_text(encoding="utf-8"))
    candidate, _, _ = current()
    for field in ("source_fingerprint", "workload_fingerprint", "wire_version",
                  "bloom_bits", "bloom_hash_count", "latency_iid_requests",
                  "known_owner_exact_origin_state",
                  "federation_measurement_requests", "runtime_image_id"):
        if existing.get(field) != candidate.get(field):
            raise SystemExit(f"campaign lock mismatch: {field}")
    print(existing["campaign_fingerprint"])
    return 0


def attach(run_dir: Path) -> int:
    verify()
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / "campaign_lock.json"
    if target.exists() and sha256(target) != sha256(LOCK):
        raise SystemExit(f"run already carries another campaign lock: {run_dir}")
    shutil.copyfile(LOCK, target)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "verify", "attach", "fingerprint"))
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        return create()
    if args.command == "verify":
        return verify()
    if args.command == "attach":
        if args.run_dir is None:
            parser.error("attach requires --run-dir")
        return attach(args.run_dir)
    if not LOCK.exists():
        raise SystemExit("missing campaign lock")
    print(json.loads(LOCK.read_text(encoding="utf-8"))["campaign_fingerprint"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
