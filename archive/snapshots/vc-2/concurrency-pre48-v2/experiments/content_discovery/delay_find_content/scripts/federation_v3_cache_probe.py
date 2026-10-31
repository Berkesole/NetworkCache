#!/usr/bin/env python3
"""Fast read-only Redis occupancy snapshots for large federation catalogs."""

from __future__ import annotations

import csv
import subprocess
import sys
import time
from pathlib import Path


VALUE_SIZE = 65536
COLUMNS = ["snapshot_id", "phase", "bin", "node", "cluster", "ts_mono_ns",
           "db_keys", "logical_value_bytes", "original_keys", "original_logical_bytes",
           "cached_copy_keys", "cached_copy_logical_bytes", "redis_used_memory_bytes"]


def redis(*arguments: str) -> str:
    return subprocess.run(["redis-cli", "--raw", "-s", "/run/redis/redis.sock", *arguments],
                          check=True, text=True, capture_output=True, timeout=20).stdout.rstrip("\n")


def scan_keys() -> set[str]:
    cursor = "0"; keys: set[str] = set()
    while True:
        lines = redis("SCAN", cursor, "COUNT", "2000").splitlines()
        if not lines: raise RuntimeError("empty SCAN response")
        cursor, *page = lines; keys.update(page)
        if cursor == "0": return keys


def originals(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["key"] for row in csv.DictReader(handle, delimiter="\t")}


def snapshot(node: int, owned: set[str], marker: Path, output: Path) -> None:
    snapshot_id, phase, bin_index = marker.read_text().strip().split("\t")
    keys = scan_keys(); original_count = len(keys & owned); cached_count = len(keys - owned)
    info = dict(line.split(":", 1) for line in redis("INFO", "memory").splitlines()
                if ":" in line and not line.startswith("#"))
    values = [snapshot_id, phase, bin_index, str(node), "A" if node <= 32 else "B",
              str(time.monotonic_ns()), str(len(keys)), str(len(keys) * VALUE_SIZE),
              str(original_count), str(original_count * VALUE_SIZE), str(cached_count),
              str(cached_count * VALUE_SIZE), info.get("used_memory", "")]
    temporary = output.with_name("." + output.name + ".tmp")
    temporary.write_text("\t".join(values) + "\n"); temporary.replace(output)


def main() -> int:
    if len(sys.argv) != 4: return 2
    node = int(sys.argv[1]); owned = originals(Path(sys.argv[2])); control = Path(sys.argv[3])
    markers = control / "occupancy-markers"; outputs = control / "occupancy"; outputs.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    while not (control / "stop").exists():
        for marker in sorted(markers.glob("*.tsv")):
            if marker.name in seen: continue
            try:
                snapshot(node, owned, marker, outputs / f"{marker.stem}-node{node}.tsv")
                seen.add(marker.name)
            except Exception as exc:
                (outputs / f"{marker.stem}-node{node}.error").write_text(repr(exc) + "\n")
                return 1
        time.sleep(.02)
    return 0


if __name__ == "__main__": raise SystemExit(main())
