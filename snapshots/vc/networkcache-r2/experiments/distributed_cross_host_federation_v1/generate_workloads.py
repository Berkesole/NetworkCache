#!/usr/bin/env python3
"""Generate deterministic Cross-host Federation-v1 workloads."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPTS = REPO / "experiments/content_discovery/delay_find_content/scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_federation_v3_pilot as timeline  # noqa: E402
import generate_federation_v4_bf1024_h3_formal as formal  # noqa: E402

import spec  # noqa: E402


DEFAULT_ROOT = (
    REPO / "experiments/content_discovery/delay_find_content/workloads/"
    "cross-host-federation-v1"
)
CATALOG_SIZE = 512


def scenario_name(seed: int, post_batches: int) -> str:
    return (
        "kua-dc-v5-cross-host-federation-v1-c512-zipf-"
        f"seed{seed:03d}-post{post_batches:03d}"
    )


def write_tsv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def generate_one(root: Path, seed: int, post_batches: int, evidence_level: str) -> None:
    name = scenario_name(seed, post_batches)
    canonical = timeline.canonical(seed, post_bins=post_batches)
    keys = [
        formal.key_name(domain, rank)
        for domain in ("A", "B")
        for rank in range(1, CATALOG_SIZE + 1)
    ]
    probe_keys = [f"cross_host_v1_probe_{domain}" for domain in ("A", "B")]
    timeline.prepare_payload_hashes(keys + probe_keys)
    schedule = formal.map_schedule(canonical, CATALOG_SIZE, "zipf")
    target = root / "mapped" / name
    write_tsv(target / "schedule.tsv", formal.SCHEDULE_COLUMNS, schedule)

    probes = []
    for index, (source, destination, requester, owner) in enumerate(
        (("A", "B", 2, 40), ("B", "A", 34, 8)), 1
    ):
        key = f"cross_host_v1_probe_{destination}"
        probes.append(
            {
                "probe_index": index,
                "direction": f"{source}_to_{destination}",
                "requester": requester,
                "owner": owner,
                "requester_cluster": source,
                "owner_cluster": destination,
                "key": key,
                "bloom_positions": formal.signature(key),
                **timeline.descriptor(key),
                "schedule_sha256": schedule[0]["schedule_sha256"],
            }
        )
    write_tsv(
        target / "isolation_probes.tsv",
        [
            "probe_index", "direction", "requester", "owner", "requester_cluster",
            "owner_cluster", "key", "bloom_positions", "value_size_bytes",
            "content_seed", "expected_fnv1a64", "schedule_sha256",
        ],
        probes,
    )

    catalog = []
    for domain in ("A", "B"):
        for rank in range(1, CATALOG_SIZE + 1):
            key = formal.key_name(domain, rank)
            catalog.append(
                {
                    "cluster": domain,
                    "key_rank": rank,
                    "owner_local_rank": (rank - 1) // 32 + 1,
                    "key": key,
                    "owner": formal.owner_absolute(domain, rank),
                    "bloom_positions": formal.signature(key),
                    **timeline.descriptor(key),
                }
            )
    write_tsv(
        target / "catalog.tsv",
        [
            "cluster", "key_rank", "owner_local_rank", "key", "owner",
            "bloom_positions", "value_size_bytes", "content_seed", "expected_fnv1a64",
        ],
        catalog,
    )
    for node in range(1, 65):
        rows = [row for row in catalog if row["owner"] == node]
        rows += [row for row in probes if int(row["owner"]) == node]
        write_tsv(
            target / "data" / f"node{node}.tsv",
            ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"],
            rows,
        )

    summary = formal.bloom_summary(catalog, schedule)
    (target / "bloom_collision_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    activation = {
        "boundary_nodes": [1, 33],
        "peers": {"1": 33, "33": 1},
        "endpoints": {"1": "10.90.0.1:44001", "33": "10.90.0.33:44033"},
        "physical_activation": "border-route-barrier",
        "protocol_activation": "logical-only",
        "schedule_sha256": schedule[0]["schedule_sha256"],
    }
    (target / "activation.json").write_text(
        json.dumps(activation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    counts = Counter((row["phase"], row["request_class"]) for row in schedule)
    metadata = {
        "suite": "cross-host-federation-v1",
        "scenario": name,
        "evidence_level": evidence_level,
        "seed": seed,
        "catalog_size_per_domain": CATALOG_SIZE,
        "distribution": "zipf",
        "zipf_alpha": timeline.ZIPF_ALPHA,
        "pre_batches": 8,
        "post_batches": post_batches,
        "measurement_requests": len(schedule),
        "pre_local_requests": counts[("pre", "local")],
        "post_remote_requests": counts[("post", "remote")],
        "post_local_control_requests": counts[("post", "local_control")],
        "value_size_bytes": formal.SIZE,
        "wire_version": formal.WIRE_VERSION,
        "staged_fallback": True,
        "fallback_delay_ms": 1500,
        "query_table_timeout_ms": 7000,
        "controller_timeout_ms": 8000,
        "max_hop_count": 12,
        "physical_topology": "leaf-spine-border-wireguard-border-spine-leaf",
        "schedule_sha256": schedule[0]["schedule_sha256"],
        **summary,
    }
    (target / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    scenario_root = root / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    yaml = formal.topology.render_scenario().replace("federation-v2", name, 1)
    yaml = yaml.replace("max_hop_count: 8", "max_hop_count: 12")
    (scenario_root / f"{name}.yaml").write_text(yaml, encoding="utf-8")


def validate_one(root: Path, seed: int, post_batches: int) -> None:
    name = scenario_name(seed, post_batches)
    target = root / "mapped" / name
    schedule = read_tsv(target / "schedule.tsv")
    expected = spec.requests_per_run(post_batches)
    if len(schedule) != expected:
        raise RuntimeError(f"{name}: expected {expected} requests, found {len(schedule)}")
    if sum(row["phase"] == "pre" for row in schedule) != spec.PRE_REQUESTS:
        raise RuntimeError(f"{name}: PRE shape changed")
    for batch in range(1, post_batches + 1):
        selected = [
            row for row in schedule
            if row["phase"] == "post" and int(row["bin"]) == batch
        ]
        if Counter(row["direction"] for row in selected) != {
            "A_to_B": 64, "B_to_A": 64, "A_local": 16, "B_local": 16
        }:
            raise RuntimeError(f"{name}: invalid POST batch {batch}")
    metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
    if metadata["schedule_sha256"] != schedule[0]["schedule_sha256"]:
        raise RuntimeError(f"{name}: metadata hash mismatch")
    if {row["schedule_sha256"] for row in schedule} != {metadata["schedule_sha256"]}:
        raise RuntimeError(f"{name}: schedule rows do not share one hash")
    scenario = (root / "scenarios" / f"{name}.yaml").read_text(encoding="utf-8")
    def neighbors(node: int) -> set[int]:
        match = re.search(
            rf"^- id: {node}\n\s+neighbors: \[([^\]]*)\]", scenario, re.MULTILINE
        )
        if not match:
            raise RuntimeError(f"{name}: missing logical node {node}")
        return {int(value.strip()) for value in match.group(1).split(",") if value.strip()}

    if 33 in neighbors(1) or 1 in neighbors(33):
        raise RuntimeError(f"{name}: logical cross-domain neighbor must be activated at t0")


def parse_seeds(value: str) -> list[int]:
    result = [int(item) for item in value.split(",") if item]
    if not result or any(seed not in spec.SEEDS for seed in result) or len(set(result)) != len(result):
        raise argparse.ArgumentTypeError("seeds must be unique values in 1..10")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--post-batches", type=int, required=True)
    parser.add_argument("--seeds", type=parse_seeds, default=list(spec.SEEDS))
    parser.add_argument("--evidence-level", choices=("CALIBRATION", "FORMAL"), required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    spec.requests_per_run(args.post_batches)
    if args.write:
        for seed in args.seeds:
            generate_one(args.output_root, seed, args.post_batches, args.evidence_level)
    for seed in args.seeds:
        validate_one(args.output_root, seed, args.post_batches)
    print(
        f"validated cross-host-federation-v1 seeds={','.join(map(str, args.seeds))} "
        f"post={args.post_batches} requests/run={spec.requests_per_run(args.post_batches)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
