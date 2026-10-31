#!/usr/bin/env python3
"""Create a reduced, non-formal federation-v2 workload under an ignored result root."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import generate_federation_v2 as gen


def build(root: Path, workload: str) -> str:
    data = gen.make(1, workload)
    chosen = []
    for row in data["rows"]:
        if row["phase"] == "pre" and row["round"] in (1, 2):
            chosen.append(dict(row))
        elif row["phase"] == "post" and row["round"] in (1, 3):
            item = dict(row); item["round"] = 1 if row["round"] == 1 else 2; chosen.append(item)
    for index, row in enumerate(chosen, 1): row["request_index"] = index
    schedule_hash = gen.sha(chosen)
    name = f"kua-dc-v3-federation-v2-smoke-{workload}-seed001"
    target = root / "mapped" / name
    output_rows = []
    for row in chosen:
        output_rows.append({**row, "requester_cluster": gen.cluster(row["requester"]),
                            "owner_cluster": gen.cluster(row["owner"]),
                            **gen.descriptor(row["key"]), "schedule_sha256": schedule_hash})
    probe_rows = [{**row, **gen.descriptor(row["key"]), "schedule_sha256": schedule_hash}
                  for row in data["isolation_probes"]]
    gen.write_tsv(target / "schedule.tsv", gen.SCHEDULE_COLUMNS, output_rows)
    gen.write_tsv(target / "isolation_probes.tsv",
                  ["probe_index", "direction", "requester", "owner", "requester_cluster", "owner_cluster",
                   "key", "value_size_bytes", "content_seed", "expected_fnv1a64", "schedule_sha256"], probe_rows)
    unique = {(int(row["owner"]), row["key"]): gen.descriptor(row["key"]) for row in output_rows + probe_rows}
    for node in range(1, 65):
        rows = [{"key": key, **values} for (owner, key), values in sorted(unique.items()) if owner == node]
        gen.write_tsv(target / "data" / f"node{node}.tsv",
                      ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"], rows)
    activation = {"boundary_nodes": [1, 33], "peers": {"1": 33, "33": 1},
                  "endpoints": {"1": "10.90.0.1:44001", "33": "10.90.0.33:44033"},
                  "semantics": "runtime-local-bootstrap", "schedule_sha256": schedule_hash}
    (target / "activation.json").write_text(json.dumps(activation, indent=2, sort_keys=True) + "\n")
    metadata = {"suite": "federation-v2-smoke", "scenario": name, "seed": 1, "workload": workload,
                "measurement_requests": len(output_rows), "schedule_sha256": schedule_hash, "wire_version": 2}
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    scenarios = root / "scenarios"; scenarios.mkdir(parents=True, exist_ok=True)
    (scenarios / f"{name}.yaml").write_text(gen.render_scenario())
    return name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    for workload in ("repeated", "uniform"): print(build(args.output_root, workload))
    return 0


if __name__ == "__main__": raise SystemExit(main())
