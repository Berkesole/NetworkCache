"""Resolve manifest targets without writing paths back into imported trees."""

from __future__ import annotations

from pathlib import Path


def source_root(archive_root: Path, target: str) -> Path:
    if target in {"mainline", "."}:
        return archive_root.parent
    return archive_root / target

