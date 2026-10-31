#!/usr/bin/env python3
"""Compare end-to-end latency from flush-every-1 and buffered trace smoke runs."""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path


def mean_latency(batch: Path) -> float:
    path = batch / "convergence_merged.csv"
    with path.open("r", newline="", encoding="utf-8") as handle:
        values = [
            float(row["miss_latency_ns"])
            for row in csv.DictReader(handle)
            if row.get("phase") == "measurement"
            and row.get("trace_complete") == "1"
            and row.get("miss_latency_ns")
        ]
    if not values:
        raise SystemExit(f"no complete measurement latency rows in {path}")
    return statistics.fmean(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("flush_one", type=Path)
    parser.add_argument("buffered", type=Path)
    parser.add_argument("--max-relative-difference", type=float, default=0.05)
    args = parser.parse_args()
    one = mean_latency(args.flush_one)
    buffered = mean_latency(args.buffered)
    relative = abs(buffered - one) / one
    print(
        f"trace flush comparison: flush1={one / 1e6:.3f} ms "
        f"buffered={buffered / 1e6:.3f} ms relative_difference={relative:.3%}"
    )
    if relative > args.max_relative_difference:
        raise SystemExit(
            f"trace buffering changes mean latency by {relative:.3%}, "
            f"limit is {args.max_relative_difference:.3%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
