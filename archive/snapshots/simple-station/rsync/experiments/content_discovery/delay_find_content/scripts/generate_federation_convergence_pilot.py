#!/usr/bin/env python3
"""Generate one long-horizon Federation workload for choosing the POST length."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import generate_federation_v3_pilot as timeline
import generate_federation_v4_bf1024_h3_formal as formal


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
SUITE = "federation-convergence-pilot"
SCENARIO = (
    "kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot-"
    "convergence-catalog512-zipf-nc-seed001"
)
DEFAULT_ROOT = EXP / "workloads" / SUITE
SEED = 1
POST_BINS = 64
PRE_REQUESTS = 1024
POST_REQUESTS_PER_BIN = 160
EXPECTED_REQUESTS = PRE_REQUESTS + POST_BINS * POST_REQUESTS_PER_BIN
CATALOG_SIZE = 512
SCHEDULE_COLUMNS = formal.SCHEDULE_COLUMNS


def write_tsv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)


def generate(root: Path) -> None:
    canonical = timeline.canonical(SEED, post_bins=POST_BINS)
    keys = [
        formal.key_name(cluster, rank)
        for cluster in ("A", "B")
        for rank in range(1, CATALOG_SIZE + 1)
    ]
    probe_keys = [f"federation_v4h3_probe_{cluster}" for cluster in ("A", "B")]
    timeline.prepare_payload_hashes(keys + probe_keys)
    schedule = formal.map_schedule(canonical, CATALOG_SIZE, "zipf")
    target = root / "mapped" / SCENARIO
    write_tsv(target / "schedule.tsv", SCHEDULE_COLUMNS, schedule)

    probes = []
    for index, (source, target_cluster, requester, owner) in enumerate(
        (("A", "B", 2, 40), ("B", "A", 34, 8)), 1
    ):
        key = f"federation_v4h3_probe_{target_cluster}"
        probes.append(
            {
                "probe_index": index,
                "direction": f"{source}_to_{target_cluster}",
                "requester": requester,
                "owner": owner,
                "requester_cluster": source,
                "owner_cluster": target_cluster,
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
    for cluster in ("A", "B"):
        for rank in range(1, CATALOG_SIZE + 1):
            key = formal.key_name(cluster, rank)
            catalog.append(
                {
                    "cluster": cluster,
                    "key_rank": rank,
                    "owner_local_rank": (rank - 1) // 32 + 1,
                    "key": key,
                    "owner": formal.owner_absolute(cluster, rank),
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
        "semantics": "runtime-local-bootstrap",
        "schedule_sha256": schedule[0]["schedule_sha256"],
    }
    (target / "activation.json").write_text(
        json.dumps(activation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    counts = Counter((row["phase"], row["request_class"]) for row in schedule)
    metadata = {
        "suite": SUITE,
        "scenario": SCENARIO,
        "purpose": "descriptive pilot for selecting the formal POST horizon",
        "evidence_level": "PILOT_NOT_FORMAL",
        "seed": SEED,
        "catalog_size_per_cluster": CATALOG_SIZE,
        "distribution": "zipf",
        "zipf_alpha": timeline.ZIPF_ALPHA,
        "pre_bins": 8,
        "post_bins": POST_BINS,
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
        "schedule_sha256": schedule[0]["schedule_sha256"],
        **summary,
    }
    (target / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    scenarios = root / "scenarios"
    scenarios.mkdir(parents=True, exist_ok=True)
    yaml = formal.topology.render_scenario().replace("federation-v2", SUITE, 1)
    yaml = yaml.replace("max_hop_count: 8", "max_hop_count: 12")
    (scenarios / f"{SCENARIO}.yaml").write_text(yaml, encoding="utf-8")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def validate(root: Path) -> None:
    target = root / "mapped" / SCENARIO
    schedule = read_rows(target / "schedule.tsv")
    if len(schedule) != EXPECTED_REQUESTS:
        raise RuntimeError(f"expected {EXPECTED_REQUESTS} requests, found {len(schedule)}")
    if sum(row["phase"] == "pre" for row in schedule) != PRE_REQUESTS:
        raise RuntimeError("PRE shape changed")
    if sum(row["request_class"] == "remote" for row in schedule) != POST_BINS * 128:
        raise RuntimeError("remote POST shape changed")
    if sum(row["request_class"] == "local_control" for row in schedule) != POST_BINS * 32:
        raise RuntimeError("local-control POST shape changed")
    for batch in range(1, POST_BINS + 1):
        selected = [
            row for row in schedule
            if row["phase"] == "post" and int(row["bin"]) == batch
        ]
        if Counter(row["direction"] for row in selected) != {
            "A_to_B": 64, "B_to_A": 64, "A_local": 16, "B_local": 16
        }:
            raise RuntimeError(f"invalid POST batch {batch}")
    metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
    if metadata["schedule_sha256"] != schedule[0]["schedule_sha256"]:
        raise RuntimeError("schedule fingerprint mismatch")
    if {row["schedule_sha256"] for row in schedule} != {metadata["schedule_sha256"]}:
        raise RuntimeError("schedule rows do not share one fingerprint")

    # The original formal schedule is an exact prefix in endpoint/key space.
    formal_path = (
        formal.DEFAULT_ROOT / "mapped" /
        formal.scenario_name("catalog512-zipf", SEED) / "schedule.tsv"
    )
    if formal_path.is_file():
        old = read_rows(formal_path)
        fields = [field for field in SCHEDULE_COLUMNS if field != "schedule_sha256"]
        left = [tuple(row[field] for field in fields) for row in schedule[: len(old)]]
        right = [tuple(row[field] for field in fields) for row in old]
        if left != right:
            raise RuntimeError("extended workload does not preserve the formal 16-bin prefix")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        generate(args.output_root)
    validate(args.output_root)
    print(
        f"validated {SUITE}: pre=8 post={POST_BINS} "
        f"requests={EXPECTED_REQUESTS} root={args.output_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
