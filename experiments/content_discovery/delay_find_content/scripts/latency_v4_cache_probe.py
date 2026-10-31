#!/usr/bin/env python3
"""Read-only Redis occupancy snapshots for latency-v4 experiments."""

from __future__ import annotations

import csv
import subprocess
import sys
import time
from pathlib import Path


COLUMNS = ["snapshot_id", "phase", "bin", "node", "ts_mono_ns", "db_keys",
           "logical_value_bytes", "original_keys", "original_logical_bytes",
           "cached_copy_keys", "cached_copy_logical_bytes", "redis_used_memory_bytes"]


def redis(*arguments: str) -> str:
    return subprocess.run(
        ["redis-cli", "--raw", "-s", "/run/redis/redis.sock", *arguments],
        check=True, text=True, capture_output=True, timeout=20,
    ).stdout.rstrip("\n")


def scan_keys() -> set[str]:
    cursor = "0"
    keys: set[str] = set()
    while True:
        lines = redis("SCAN", cursor, "COUNT", "2000").splitlines()
        if not lines:
            raise RuntimeError("empty SCAN response")
        cursor, *page = lines
        keys.update(page)
        if cursor == "0":
            return keys


def descriptors(path: Path) -> tuple[set[str], dict[str, int]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    owned = {row["key"] for row in rows}
    sizes = {row["key"]: int(row["value_size_bytes"]) for row in rows}
    return owned, sizes


def snapshot(node: int, owned: set[str], sizes: dict[str, int], default_size: int,
             marker: Path, output: Path) -> None:
    snapshot_id, phase, bin_index = marker.read_text(encoding="utf-8").strip().split("\t")
    keys = scan_keys()
    originals = keys & owned
    cached = keys - owned
    original_bytes = sum(sizes[key] for key in originals)
    cached_bytes = sum(sizes.get(key, default_size) for key in cached)
    info = dict(line.split(":", 1) for line in redis("INFO", "memory").splitlines()
                if ":" in line and not line.startswith("#"))
    values = [snapshot_id, phase, bin_index, str(node), str(time.monotonic_ns()),
              str(len(keys)), str(original_bytes + cached_bytes), str(len(originals)),
              str(original_bytes), str(len(cached)), str(cached_bytes),
              info.get("used_memory", "")]
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text("\t".join(COLUMNS) + "\n" + "\t".join(values) + "\n", encoding="utf-8")
    temporary.replace(output)


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    node = int(sys.argv[1])
    owned, sizes = descriptors(Path(sys.argv[2]))
    control = Path(sys.argv[3])
    default_size = int(sys.argv[4])
    markers = control / "occupancy-markers"
    outputs = control / "occupancy"
    outputs.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    while not (control / "stop").exists():
        for marker in sorted(markers.glob("*.tsv")):
            if marker.name in seen:
                continue
            try:
                output = outputs / f"{marker.stem}-node{node}.tsv"
                snapshot(node, owned, sizes, default_size, marker, output)
                seen.add(marker.name)
            except Exception as exc:  # diagnostic must survive node teardown
                (outputs / f"{marker.stem}-node{node}.error").write_text(
                    repr(exc) + "\n", encoding="utf-8")
                return 1
        time.sleep(0.02)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
