#!/usr/bin/env python3
"""Fail when the archive contains data payloads or manifest drift."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRS = {
    "results",
    "raw",
    "output",
    "collected",
    "derived",
    "deliveries",
    "delivery",
    "evidence",
    "traces",
    "workloads",
    "slots",
    "build",
    "vldb",
}
FORBIDDEN_SUFFIXES = {
    ".csv",
    ".gz",
    ".jpeg",
    ".jpg",
    ".jsonl",
    ".log",
    ".pdf",
    ".png",
    ".tgz",
    ".trace",
    ".tsv",
    ".zip",
}


def digest_tree(target: Path) -> tuple[int, int, str]:
    entries = []
    total = 0
    count = 0
    for directory, dirnames, filenames in os.walk(target):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(target).as_posix()
            data = path.read_bytes()
            entries.append(f"{hashlib.sha256(data).hexdigest()}  {rel}\n".encode())
            total += len(data)
            count += 1
    merged = hashlib.sha256()
    for entry in entries:
        merged.update(entry)
    return count, total, merged.hexdigest()


def main() -> int:
    manifest = json.loads((ROOT / "archive-manifest.json").read_text())
    failures = []
    for source in manifest["sources"]:
        target = ROOT / source["target"]
        if not target.is_dir():
            failures.append(f"missing source directory: {source['target']}")
            continue
        count, total, digest = digest_tree(target)
        expected = source["code"]
        if count != expected["file_count"] or total != expected["bytes"]:
            failures.append(f"size/count drift: {source['id']}")
        if digest != expected["content_sha256"]:
            failures.append(f"content digest drift: {source['id']}")

    forbidden = []
    nested_git = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if ".git" in rel.parts:
            continue
        if path.name == ".git":
            nested_git.append(str(rel))
            continue
        if any(part in FORBIDDEN_DIRS for part in rel.parts):
            forbidden.append(str(rel))
        elif path.suffix.lower() in FORBIDDEN_SUFFIXES:
            forbidden.append(str(rel))

    if nested_git:
        failures.append(f"nested Git metadata: {len(nested_git)}")
    if forbidden:
        failures.append(f"forbidden data paths: {len(forbidden)}")
        failures.extend(forbidden[:10])

    if failures:
        print("Archive verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    files = sum(item["code"]["file_count"] for item in manifest["sources"])
    size = sum(item["code"]["bytes"] for item in manifest["sources"])
    print(
        f"OK: {len(manifest['sources'])} sources, {files} files, "
        f"{size} bytes; no data payloads or nested Git metadata"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

