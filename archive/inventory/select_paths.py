#!/usr/bin/env python3
"""Select source-like paths without reading experiment result payloads."""

from __future__ import annotations

import argparse
import os
import sys


CODE_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".cmake",
    ".conf",
    ".cpp",
    ".cxx",
    ".dep",
    ".h",
    ".hpp",
    ".ini",
    ".ipynb",
    ".lab",
    ".md",
    ".proto",
    ".py",
    ".rst",
    ".service",
    ".sh",
    ".sql",
    ".startup",
    ".toml",
    ".tmpl",
    ".txt",
    ".yaml",
    ".yml",
}

DATA_SUFFIXES = {
    ".csv",
    ".gz",
    ".jpeg",
    ".jpg",
    ".jsonl",
    ".log",
    ".pdf",
    ".png",
    ".svg",
    ".tgz",
    ".trace",
    ".tsv",
    ".zip",
}

EXCLUDED_DIRS = {
    ".agents",
    ".codex",
    ".tmp",
    "__pycache__",
    ".git",
    ".venv",
    "raw",
    "results",
    "output",
    "collected",
    "checks",
    "derived",
    "deliveries",
    "delivery",
    "evidence",
    "traces",
    "workloads",
    "paper_assets",
    "clock-probe",
    "execution-revisions",
    "rootfs",
    "generated",
    "state",
    "slots",
    "build",
    "node_modules",
    "presentations",
    "reports",
    "target",
    "tmp",
    "vldb",
    "_deps",
    "CMakeFiles",
}

RUN_DIR_PREFIXES = (
    "acceptance-",
    "final-",
    "offline-acceptance",
    "round",
    "stage-",
    "supplementary-",
)
EXCLUDED_PREFIXES = ("build-",)
DATA_NAME_PREFIXES = (
    "backend_snapshot",
    "evidence-manifest",
    "offload_snapshot",
    "PROVENANCE",
    "process_fault_chain",
    "redis_acceptance_summary",
    "active-scenario",
    "workload_manifest",
)

EXACT_NAMES = {
    ".dockerignore",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "CMakeLists.txt",
    "Dockerfile",
    "LICENSE",
    "Makefile",
    "README",
}

NAME_PREFIXES = ("Dockerfile.", "README.")


def select(path: str, size: int, max_size: int) -> bool:
    parts = path.strip("/").split("/")
    if parts[0] in {"archive", "release"}:
        return False
    if any(
        part in EXCLUDED_DIRS
        or part.startswith(EXCLUDED_PREFIXES)
        or part.startswith(RUN_DIR_PREFIXES)
        for part in parts
    ):
        return False
    name = parts[-1]
    if name.startswith(DATA_NAME_PREFIXES):
        return False
    if name.endswith("~") or (name.startswith(".") and name not in EXACT_NAMES):
        return False
    if name in EXACT_NAMES or name.startswith(NAME_PREFIXES):
        return size <= max_size
    suffix = os.path.splitext(name)[1].lower()
    if suffix in DATA_SUFFIXES:
        return False
    if suffix == ".json":
        if "/experiments/" not in "/" + path:
            return size <= 16 * 1024 or name in {
                "package.json",
                "pyproject.json",
                "settings.json",
                "tsconfig.json",
            }
        return (
            size <= max_size
            and (
                size <= 16 * 1024
                or
                name
                in {
                    "base_manifest.json",
                    "capacity_matrix.json",
                    "manifest.json",
                    "package.json",
                    "pyproject.json",
                    "settings.json",
                    "tsconfig.json",
                }
                or name.endswith("_manifest.json")
                or name.endswith("-manifest.json")
                or name.endswith("_config.json")
                or name.endswith("-config.json")
            )
        )
    return suffix in CODE_SUFFIXES and size <= max_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--max-size", type=int, default=512 * 1024)
    parser.add_argument("--null", action="store_true")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    selected = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in EXCLUDED_DIRS and not name.startswith(EXCLUDED_PREFIXES)
        )
        for name in sorted(filenames):
            full = os.path.join(directory, name)
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if select(rel, os.path.getsize(full), args.max_size):
                selected.append(rel)
    separator = "\0" if args.null else "\n"
    sys.stdout.write(separator.join(selected))
    if selected and args.null:
        sys.stdout.write("\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

