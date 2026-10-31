#!/usr/bin/env python3
"""Read-only Redis occupancy probe used between federation-v2 request rounds."""

from __future__ import annotations

import csv
import subprocess
import sys
import time
from pathlib import Path


def redis(*arguments: str) -> str:
    completed = subprocess.run(
        ["redis-cli", "--raw", "-s", "/run/redis/redis.sock", *arguments],
        check=True, text=True, capture_output=True, timeout=10,
    )
    return completed.stdout.rstrip("\n")


def scan_keys() -> list[str]:
    cursor = "0"
    keys: list[str] = []
    while True:
        output = redis("SCAN", cursor, "COUNT", "1000").splitlines()
        if not output:
            raise RuntimeError("empty SCAN response")
        cursor, *page = output
        keys.extend(page)
        if cursor == "0":
            return sorted(set(keys))


def original_keys(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["key"] for row in csv.DictReader(handle, delimiter="\t")}


def snapshot(node: int, originals: set[str], marker: Path, output: Path) -> None:
    fields = marker.read_text(encoding="utf-8").strip().split("\t")
    if len(fields) != 3:
        raise ValueError(f"invalid snapshot marker {marker}")
    snapshot_id, phase, round_index = fields
    keys = scan_keys()
    logical = {key: int(redis("STRLEN", key) or "0") for key in keys}
    memory = {key: int(redis("MEMORY", "USAGE", key) or "0") for key in keys}
    original_present = [key for key in keys if key in originals]
    cached = [key for key in keys if key not in originals]
    info = dict(line.split(":", 1) for line in redis("INFO", "memory").splitlines()
                if ":" in line and not line.startswith("#"))
    values = [snapshot_id, phase, round_index, str(node), "A" if node <= 32 else "B",
              str(time.monotonic_ns()), str(len(keys)), str(sum(logical.values())),
              str(len(original_present)), str(sum(logical[key] for key in original_present)),
              str(len(cached)), str(sum(logical[key] for key in cached)),
              str(sum(memory.values())), info.get("used_memory", "")]
    temporary = output.with_name("." + output.name + ".tmp")
    temporary.write_text("\t".join(values) + "\n", encoding="utf-8")
    temporary.replace(output)


def main() -> int:
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} NODE DATA_TSV CONTROL_DIR", file=sys.stderr)
        return 2
    node = int(sys.argv[1])
    originals = original_keys(Path(sys.argv[2]))
    control = Path(sys.argv[3])
    markers = control / "occupancy-markers"
    outputs = control / "occupancy"
    outputs.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    while not (control / "stop").exists():
        for marker in sorted(markers.glob("*.tsv")):
            if marker.name in seen:
                continue
            try:
                snapshot(node, originals, marker, outputs / f"{marker.stem}-node{node}.tsv")
                seen.add(marker.name)
            except Exception as exc:
                (outputs / f"{marker.stem}-node{node}.error").write_text(repr(exc) + "\n")
                return 1
        time.sleep(.02)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
