#!/usr/bin/env python3
"""Import code-only working trees from the NetworkCache experiment hosts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SELECTOR = Path(__file__).with_name("select_paths.py").read_bytes()
SSH_COMMON = (
    "-o",
    "BatchMode=yes",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "GlobalKnownHostsFile=/dev/null",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "LogLevel=ERROR",
    "-o",
    "ConnectTimeout=20",
)

TRANSPORTS: dict[str, list[str]] = {
    "xiaoxin": ["xiaoxin-ubuntu"],
    "vc": ["vc"],
    "vc-1": ["vc-1"],
    "vc-2": ["vc-2"],
    "simple-station": [
        "-p",
        "6000",
        "-i",
        str(Path.home() / ".ssh" / "Simple_Station"),
        "simpleai@8.131.71.180",
    ],
}

VC2_PROJECTS = (
    "pilot-v1",
    "pilot-v2",
    "pilot-v3",
    "pilot-v4",
    "pilot-v5",
    "pilot-v6",
    "pilot-v7",
    "pilot-v8",
    "pilot-v9",
    "slides-v1",
    "slides-v2",
    "slides-v3",
    "slides-v4",
    "slides-v5",
    "supplement-v1",
    "supplement-v2",
    "pre48-v2",
    "pre64post96-v2",
)

SS_PROJECTS = (
    "slides-v5",
    "capacity-v1",
    "supplement-v1",
    "pre48-v1",
    "pre64post96-v1",
)

SS_FINITE_VERSIONS = (
    "source",
    "smoke-src/81c22fe9/source",
    "versions/02f4b116/source",
    "versions/03caf15c/source",
    "versions/3aac7b22/source",
)

SS_FINITE_CODE_DIRS = (
    (
        "a0469dea-experiments",
        "versions/a0469dea/experiments",
    ),
)


def source_specs() -> list[dict[str, str]]:
    specs = [
        {
            "id": "xiaoxin-ubuntu/rsync",
            "host": "xiaoxin-ubuntu",
            "transport": "xiaoxin",
            "remote_path": "/root/zzh/rsync",
            "target": "mainline",
        },
        {
            "id": "simple-station/rsync",
            "host": "simple-station",
            "transport": "simple-station",
            "remote_path": "/home/simpleai/zhengzihao/rsync",
            "target": "snapshots/simple-station/rsync",
        },
        {
            "id": "vc/networkcache-r2",
            "host": "vc",
            "transport": "vc",
            "remote_path": "/data/networkcache-r2/worktree",
            "target": "snapshots/vc/networkcache-r2",
        },
        {
            "id": "vc-1/networkcache-r2",
            "host": "vc-1",
            "transport": "vc-1",
            "remote_path": "/data/networkcache-r2/worktree",
            "target": "snapshots/vc-1/networkcache-r2",
        },
        {
            "id": "simple-station/pilot-bootstrap",
            "host": "simple-station",
            "transport": "simple-station",
            "remote_path": "/home/simpleai/zhengzihao/networkcache-pilot-bootstrap",
            "target": "snapshots/simple-station/pilot-bootstrap",
        },
    ]
    for project in VC2_PROJECTS:
        specs.append(
            {
                "id": f"vc-2/concurrency-{project}",
                "host": "vc-2",
                "transport": "vc-2",
                "remote_path": f"/data/networkcache-concurrency-{project}/source",
                "target": f"snapshots/vc-2/concurrency-{project}",
            }
        )
    for project in SS_PROJECTS:
        specs.append(
            {
                "id": f"simple-station/concurrency-{project}",
                "host": "simple-station",
                "transport": "simple-station",
                "remote_path": (
                    f"/home/simpleai/zhengzihao/networkcache-concurrency-{project}/source"
                ),
                "target": f"snapshots/simple-station/concurrency-{project}",
            }
        )
    for version in SS_FINITE_VERSIONS:
        label = "base" if version == "source" else version.removeprefix("versions/").removesuffix("/source")
        label = label.replace("smoke-src/81c22fe9", "smoke-81c22fe9")
        specs.append(
            {
                "id": f"simple-station/finite-cache-v1/{label}",
                "host": "simple-station",
                "transport": "simple-station",
                "remote_path": (
                    "/home/simpleai/zhengzihao/"
                    f"networkcache-concurrency-finite-cache-v1/{version}"
                ),
                "target": f"snapshots/simple-station/finite-cache-v1/{label}",
            }
        )
    for label, version in SS_FINITE_CODE_DIRS:
        specs.append(
            {
                "id": f"simple-station/finite-cache-v1/{label}",
                "host": "simple-station",
                "transport": "simple-station",
                "remote_path": (
                    "/home/simpleai/zhengzihao/"
                    f"networkcache-concurrency-finite-cache-v1/{version}"
                ),
                "target": f"snapshots/simple-station/finite-cache-v1/{label}",
            }
        )
    return specs


def ssh_command(spec: dict[str, str], remote_command: str) -> list[str]:
    return [
        "ssh",
        *SSH_COMMON,
        *TRANSPORTS[spec["transport"]],
        remote_command,
    ]


def run_ssh(
    spec: dict[str, str],
    remote_command: str,
    *,
    data: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ssh_command(spec, remote_command),
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def git_metadata(spec: dict[str, str]) -> dict[str, Any]:
    root = shlex.quote(spec["remote_path"])
    command = (
        f"if git -C {root} rev-parse --is-inside-work-tree >/dev/null 2>&1; then "
        f"printf 'repo=1\\n'; "
        f"printf 'head='; git -C {root} rev-parse HEAD; "
        f"printf 'branch='; git -C {root} symbolic-ref --short -q HEAD || printf 'DETACHED\\n'; "
        f"printf 'tree='; git -C {root} rev-parse 'HEAD^{{tree}}'; "
        f"printf 'origin='; git -C {root} remote get-url origin 2>/dev/null || true; "
        f"printf 'tracked='; git -C {root} ls-files | wc -l; "
        f"printf 'dirty='; git -C {root} status --porcelain | wc -l; "
        f"printf 'tracked_dirty='; git -C {root} status --porcelain --untracked-files=no | wc -l; "
        f"else printf 'repo=0\\n'; fi"
    )
    result = run_ssh(spec, command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace"))
    values: dict[str, Any] = {}
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"tracked", "dirty", "tracked_dirty"}:
            values[key] = int(value.strip() or 0)
        elif key == "repo":
            values[key] = value.strip() == "1"
        else:
            values[key] = value.strip()
    if values.get("repo"):
        values.pop("repo")
        return {
            key: values.get(key)
            for key in (
                "head",
                "branch",
                "tree",
                "origin",
                "tracked",
                "dirty",
                "tracked_dirty",
            )
        }
    return {"kind": "non-git-working-tree"}


def selected_paths(spec: dict[str, str]) -> list[str]:
    root = shlex.quote(spec["remote_path"])
    result = run_ssh(
        spec,
        f"cd {root} && python3 - --root . --null",
        data=SELECTOR,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace"))
    return [
        item.decode("utf-8", "surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]


def extract(
    spec: dict[str, str],
    paths: list[str],
    target: Path,
    allow_existing: bool = False,
) -> None:
    if target.exists() and not allow_existing:
        raise FileExistsError(f"refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=True)
    remote_list = b"\0".join(os.fsencode(path) for path in paths) + b"\0"
    root = shlex.quote(spec["remote_path"])
    ssh = subprocess.Popen(
        ssh_command(
            spec,
            f"cd {root} && tar --null --no-recursion --verbatim-files-from -T - -cf -",
        ),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert ssh.stdin is not None
    tar = subprocess.Popen(
        ["tar", "-xf", "-", "-C", str(target)],
        stdin=ssh.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if ssh.stdout is not None:
        ssh.stdout.close()
    _, ssh_stderr = ssh.communicate(remote_list)
    _, tar_stderr = tar.communicate()
    if ssh.returncode != 0:
        raise RuntimeError(
            "remote tar failed: " + ssh_stderr.decode("utf-8", "replace")
        )
    if tar.returncode != 0:
        raise RuntimeError("local tar failed: " + tar_stderr.decode("utf-8", "replace"))


def content_digest(target: Path) -> tuple[int, int, str]:
    entries: list[bytes] = []
    total = 0
    count = 0
    for directory, dirnames, filenames in os.walk(target):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(target).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append(f"{digest}  {rel}\n".encode())
            total += path.stat().st_size
            count += 1
    merged = hashlib.sha256()
    for entry in entries:
        merged.update(entry)
    return count, total, merged.hexdigest()


def import_one(spec: dict[str, str], allow_existing: bool = False) -> dict[str, Any]:
    print(f"Importing {spec['id']} ...", flush=True)
    metadata = git_metadata(spec)
    paths = selected_paths(spec)
    if not paths:
        raise RuntimeError(f"selector returned no files for {spec['id']}")
    target = ROOT / spec["target"]
    extract(spec, paths, target, allow_existing=allow_existing)
    count, total, digest = content_digest(target)
    if count != len(paths):
        raise RuntimeError(
            f"imported {count} files but selected {len(paths)} for {spec['id']}"
        )
    imported = {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if imported != set(paths):
        missing = sorted(set(paths) - imported)
        extra = sorted(imported - set(paths))
        raise RuntimeError(
            f"file-set mismatch for {spec['id']}: "
            f"missing={missing[:5]} extra={extra[:5]}"
        )
    return {
        **spec,
        "git": metadata,
        "code": {
            "file_count": count,
            "bytes": total,
            "content_sha256": digest,
        },
    }


def refresh_manifest() -> int:
    manifest_path = ROOT / "archive-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for source in manifest["sources"]:
        target = ROOT / source["target"]
        if not target.is_dir():
            raise FileNotFoundError(target)
        count, total, digest = content_digest(target)
        source["code"] = {
            "file_count": count,
            "bytes": total,
            "content_sha256": digest,
        }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n")
    files = sum(item["code"]["file_count"] for item in manifest["sources"])
    size = sum(item["code"]["bytes"] for item in manifest["sources"])
    print(f"Refreshed {len(manifest['sources'])} sources, {files} files, {size} bytes")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated source ids")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--update-existing", action="store_true")
    args = parser.parse_args()
    if args.refresh:
        return refresh_manifest()
    only = set(args.only.split(",")) if args.only else None
    sources = []
    for spec in source_specs():
        if only and spec["id"] not in only:
            continue
        sources.append(import_one(spec, allow_existing=args.update_existing))
    manifest_path = ROOT / "archive-manifest.json"
    existing = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    by_id = {item["id"]: item for item in existing.get("sources", [])}
    by_id.update({item["id"]: item for item in sources})
    manifest = {
        "schema_version": 1,
        "created_at": "2026-10-31T13:45:00+08:00",
        "archive": {
            "branch": "main",
            "remote_configured": False,
            "contains_paper": False,
            "contains_result_data": False,
        },
        "policy": {
            "mode": "code-only working-tree export",
            "included": [
                "source code",
                "build configuration",
                "test code",
                "experiment drivers",
                "topology configuration",
                "small manifests and documentation",
            ],
            "excluded": [
                "paper and vldb submodule",
                "CSV/TSV/JSONL/trace measurements",
                "raw, results, output, collected, derived, deliveries",
                "evidence, workload, state, build and dependency directories",
                "PDF, images, archives, logs, binaries and CMake caches",
            ],
            "working_tree": "included; commit and dirty counts recorded per source",
        },
        "sources": sorted(by_id.values(), key=lambda item: item["id"]),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n")
    total_files = sum(item["code"]["file_count"] for item in manifest["sources"])
    total_bytes = sum(item["code"]["bytes"] for item in manifest["sources"])
    print(f"Imported {len(manifest['sources'])} sources, {total_files} files, {total_bytes} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

