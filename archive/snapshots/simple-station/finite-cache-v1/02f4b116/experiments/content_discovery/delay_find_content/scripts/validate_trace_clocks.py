#!/usr/bin/env python3
"""Validate that trace monotonic timestamps share a comparable clock origin."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Run directory or traces directory")
    parser.add_argument("--max-offset-spread-ms", type=float, default=5.0)
    args = parser.parse_args()
    traces = args.input / "traces" if (args.input / "traces").is_dir() else args.input
    offsets = {}
    for path in sorted(traces.glob("*.jsonl")):
        values = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                data = json.loads(raw)
                values.append(int(data["ts_epoch_ns"]) - int(data["ts_mono_ns"]))
                if len(values) >= 32:
                    break
        if values:
            offsets[path.name] = statistics.median(values)
    if len(offsets) < 2:
        raise SystemExit(f"need traces from at least two nodes, got {len(offsets)}")
    spread_ns = max(offsets.values()) - min(offsets.values())
    spread_ms = spread_ns / 1_000_000.0
    print(f"trace monotonic clock offset spread: {spread_ms:.6f} ms across {len(offsets)} nodes")
    if spread_ms > args.max_offset_spread_ms:
        raise SystemExit(
            f"monotonic clocks are not comparable: {spread_ms:.6f} ms > {args.max_offset_spread_ms:.6f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
