#!/usr/bin/env python3
"""Prepare per-node inputs for the Oracle Direct Fetch baseline."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List


SCHEDULE_COLUMNS = [
    "tick",
    "request_index",
    "phase",
    "request_pair_id",
    "distance_class",
    "expected_cold_provider_hop",
    "expected_warm_cache_hop",
    "key",
    "provider",
    "provider_ip",
    "provider_port",
]
ALL_REQUEST_COLUMNS = [
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
    "provider_ip",
    "provider_port",
    "value_bytes",
]


def parse_scenario_data(path: Path) -> Dict[int, Dict[str, str]]:
    data: Dict[int, Dict[str, str]] = {}
    in_data = False
    item_re = re.compile(r"^\s*-\s*(?P<node>\d+):(?P<key>[^=\s]+)=(?P<value>.*)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*data:\s*$", line):
            in_data = True
            continue
        if in_data and re.match(r"^\s*requests:\s*$", line):
            break
        if not in_data:
            continue
        match = item_re.match(line)
        if not match:
            continue
        node = int(match.group("node"))
        data.setdefault(node, {})[match.group("key")] = match.group("value")
    return data


def loopback_ip(prefix: str, node: int) -> str:
    return f"{prefix}.{node}"


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
    parser.add_argument("--scenario-file", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--loopback-prefix", default="10.90.0")
    parser.add_argument("--oracle-port-base", type=int, default=47000)
    parser.add_argument("--loop-sleep-ms", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    scenario_data = parse_scenario_data(args.scenario_file)
    host_count = int(metadata["host_count"])
    scenario_ticks = int(metadata["scenario_ticks"])
    metadata_loop_sleep_ms = int(metadata.get("spacing_loop_sleep_ms", 25))
    loop_sleep_ms = args.loop_sleep_ms if args.loop_sleep_ms is not None else metadata_loop_sleep_ms

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    all_rows: List[Dict[str, object]] = []
    schedules_by_node: Dict[int, List[Dict[str, object]]] = {node: [] for node in range(1, host_count + 1)}

    for request in metadata["requests"]:
        requester = int(request["node"])
        provider = int(request["provider"])
        key = str(request["key"])
        value = scenario_data.get(provider, {}).get(key, "")
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
            "key": key,
            "tick": request.get("tick", ""),
            "provider_ip": loopback_ip(args.loopback_prefix, provider),
            "provider_port": args.oracle_port_base + provider,
            "value_bytes": len(value.encode("utf-8")),
        }
        all_rows.append(row)
        schedules_by_node[requester].append(row)

    for node in range(1, host_count + 1):
        data_rows = [
            {"key": key, "value": value}
            for key, value in sorted(scenario_data.get(node, {}).items())
        ]
        write_tsv(output / f"node{node}.data.tsv", ["key", "value"], data_rows)
        write_tsv(output / f"node{node}.schedule.tsv", SCHEDULE_COLUMNS, schedules_by_node[node])
        (output / f"node{node}.run.sh").write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    "set -eu",
                    "exec /app/build/oracle-direct-fetch \\",
                    f"  --node-id {node} \\",
                    f"  --listen-port {args.oracle_port_base + node} \\",
                    f"  --ticks {scenario_ticks} \\",
                    f"  --loop-sleep-ms {loop_sleep_ms} \\",
                    f"  --data-file /shared/oracle/node{node}.data.tsv \\",
                    f"  --schedule-file /shared/oracle/node{node}.schedule.tsv \\",
                    f"  --log-file /shared/logs/node{node}.oracle.log",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    write_csv(output / "oracle_schedule.csv", ALL_REQUEST_COLUMNS, all_rows)
    (output / "oracle_metadata.json").write_text(
        json.dumps(
            {
                "scenario": metadata["scenario"],
                "topology": metadata["topology"],
                "workload_id": metadata["workload_id"],
                "workload_type": metadata["workload_type"],
                "seed": metadata["seed"],
                "host_count": host_count,
                "scenario_ticks": scenario_ticks,
                "loop_sleep_ms": loop_sleep_ms,
                "metadata_loop_sleep_ms": metadata_loop_sleep_ms,
                "oracle_port_base": args.oracle_port_base,
                "loopback_prefix": args.loopback_prefix,
                "request_count": len(all_rows),
                "measurement_count": sum(1 for row in all_rows if row["phase"] == "measurement"),
                "key_count": metadata["key_count"],
                "provider_count": metadata["provider_count"],
                "requester_count": metadata["requester_count"],
                "phase_policy": metadata["phase_policy"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"oracle_input_dir={output}")
    print(f"host_count={host_count}")
    print(f"scenario_ticks={scenario_ticks}")
    print(f"loop_sleep_ms={loop_sleep_ms}")
    print(f"requests={len(all_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
