#!/usr/bin/env python3
"""Prepare per-node owner hints for the Oracle NetworkCache Unicast baseline."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List


SCHEDULE_COLUMNS = [
    "tick",
    "key",
    "provider",
    "request_index",
    "phase",
    "request_pair_id",
    "distance_class",
    "expected_cold_provider_hop",
    "expected_warm_cache_hop",
]
ALL_COLUMNS = [
    "topology",
    "phase",
    "request_index",
    "request_pair_id",
    "distance_class",
    "expected_cold_provider_hop",
    "expected_warm_cache_hop",
    "requester",
    "provider",
    "key",
    "tick",
]


def write_tsv(path: Path, columns: List[str], rows: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_csv(path: Path, columns: List[str], rows: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    host_count = int(metadata["host_count"])
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    rows_by_node: Dict[int, List[Dict[str, object]]] = {node: [] for node in range(1, host_count + 1)}
    all_rows: List[Dict[str, object]] = []
    for request in metadata["requests"]:
        requester = int(request["node"])
        provider = int(request["provider"])
        if requester == provider:
            raise SystemExit(
                f"oracle-unicast requires requester != provider, got node {requester} for {request['key']}"
            )
        row = {
            "topology": metadata["topology"],
            "phase": request.get("phase", ""),
            "request_index": request.get("request_index", ""),
            "request_pair_id": request.get("request_pair_id", ""),
            "distance_class": request.get("distance_class", ""),
            "expected_cold_provider_hop": request.get("expected_cold_provider_hop", ""),
            "expected_warm_cache_hop": request.get("expected_warm_cache_hop", ""),
            "requester": requester,
            "provider": provider,
            "key": request["key"],
            "tick": request["tick"],
        }
        rows_by_node[requester].append(row)
        all_rows.append(row)

    for node in range(1, host_count + 1):
        write_tsv(output / f"node{node}.oracle_unicast.tsv", SCHEDULE_COLUMNS, rows_by_node[node])

    write_csv(output / "oracle_nc_unicast_schedule.csv", ALL_COLUMNS, all_rows)
    (output / "oracle_nc_unicast_metadata.json").write_text(
        json.dumps(
            {
                "scenario": metadata["scenario"],
                "topology": metadata["topology"],
                "workload_id": metadata["workload_id"],
                "workload_type": metadata["workload_type"],
                "seed": metadata["seed"],
                "host_count": host_count,
                "scenario_ticks": metadata["scenario_ticks"],
                "request_count": len(all_rows),
                "measurement_count": sum(1 for row in all_rows if row["phase"] == "measurement"),
                "key_count": metadata["key_count"],
                "provider_count": metadata["provider_count"],
                "requester_count": metadata["requester_count"],
                "phase_policy": metadata["phase_policy"],
                "baseline": "oracle-nc-unicast",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"oracle_nc_unicast_input_dir={output}")
    print(f"host_count={host_count}")
    print(f"requests={len(all_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
