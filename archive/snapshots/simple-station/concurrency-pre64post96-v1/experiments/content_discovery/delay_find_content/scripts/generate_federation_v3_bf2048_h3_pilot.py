#!/usr/bin/env python3
"""Generate the four matched 2048-bit/3-hash federation pilots."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import generate_federation_v2 as topology
import generate_federation_v3_pilot as base


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
DEFAULT_ROOT = EXP / "workloads/federation-v3-bf2048-h3-pilot"
BLOOM_BITS = 2048
BLOOM_HASH_COUNT = 3
SALTS = (0x9E3779B97F4A7C15, 0xBF58476D1CE4E5B9, 0x94D049BB133111EB)
CONFIGS = (
    ("catalog512-zipf", 512, "zipf"),
    ("catalog512-uniform", 512, "uniform"),
    ("catalog1000-zipf", 1000, "zipf"),
    ("catalog1000-uniform", 1000, "uniform"),
)


def suite_name() -> str:
    return f"federation-v3-bf{BLOOM_BITS}-h3-pilot"
SCHEDULE_COLUMNS = [
    "request_index", "phase", "bin", "round", "request_class", "direction",
    "key", "key_rank", "owner_local_rank", "key_u", "bloom_positions",
    "requester", "owner", "requester_cluster", "owner_cluster",
    "original_owner_overlay_hops", "overlay_edge_physical_signature",
    "value_size_bytes", "content_seed", "expected_fnv1a64", "schedule_sha256",
]


def key_name(cluster: str, rank: int) -> str:
    return f"federation_v3h3_{cluster}_{rank:05d}"


def seeded_fnv1a64(key: str, salt: int) -> int:
    value = 14695981039346656037 ^ salt
    for byte in key.encode():
        value ^= byte
        value = (value * 1099511628211) & ((1 << 64) - 1)
    return value


def positions(key: str) -> tuple[int, int, int]:
    return tuple(seeded_fnv1a64(key, salt) % BLOOM_BITS for salt in SALTS)


def signature(key: str) -> str:
    return ";".join(map(str, positions(key)))


def owner_absolute(cluster: str, rank: int) -> int:
    return (rank - 1) % 32 + 1 + (0 if cluster == "A" else 32)


def sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def map_schedule(canonical: list[dict], catalog_size: int, distribution: str) -> list[dict]:
    output = []
    for source in canonical:
        row = dict(source)
        rank, local_rank = base.map_key(int(row["owner_rel"]), float(row["key_u"]), catalog_size, distribution)
        owner_cluster = str(row["owner_cluster"])
        key = key_name(owner_cluster, rank)
        requester = int(row["requester_rel"]) + (0 if row["requester_cluster"] == "A" else 32)
        owner = int(row["owner_rel"]) + (0 if owner_cluster == "A" else 32)
        path = topology.shortest_overlay_path(requester, owner, federated=row["phase"] == "post")
        row.update({"key": key, "key_rank": rank, "owner_local_rank": local_rank,
                    "bloom_positions": signature(key), "requester": requester, "owner": owner,
                    "original_owner_overlay_hops": len(path) - 1,
                    "overlay_edge_physical_signature": topology.physical_signature(path),
                    **base.descriptor(key)})
        row.pop("requester_rel"); row.pop("owner_rel")
        output.append(row)
    schedule_hash = sha(output)
    for row in output: row["schedule_sha256"] = schedule_hash
    return output


def bloom_summary(catalog: list[dict], schedule: list[dict]) -> dict:
    by_owner: dict[int, list[str]] = defaultdict(list)
    for row in catalog: by_owner[int(row["owner"])].append(row["key"])
    owner_set_bits = []
    owner_signature_collisions = []
    for keys in by_owner.values():
        all_positions = {position for key in keys for position in positions(key)}
        owner_set_bits.append(len(all_positions))
        owner_signature_collisions.append(len(keys) - len({positions(key) for key in keys}))
    requested = [row["key"] for row in schedule]
    return {
        "bloom_bits": BLOOM_BITS,
        "bloom_hash_count": BLOOM_HASH_COUNT,
        "catalog_keys": len(catalog),
        "catalog_distinct_signatures": len({positions(row["key"]) for row in catalog}),
        "catalog_signature_collisions": len(catalog) - len({positions(row["key"]) for row in catalog}),
        "per_owner_min_set_bits": min(owner_set_bits),
        "per_owner_max_set_bits": max(owner_set_bits),
        "per_owner_max_signature_collisions": max(owner_signature_collisions),
        "requested_rows": len(requested),
        "requested_unique_keys": len(set(requested)),
        "requested_distinct_signatures": len({positions(key) for key in requested}),
    }


def generate(root: Path, slug: str, catalog_size: int, distribution: str) -> None:
    suite = suite_name()
    scenario = f"kua-dc-v3-{suite}-{slug}-nc-seed001"
    keys = [key_name(cluster, rank) for cluster in ("A", "B") for rank in range(1, catalog_size + 1)]
    probe_keys = ["federation_v3h3_probe_A", "federation_v3h3_probe_B"]
    base.prepare_payload_hashes(keys + probe_keys)
    schedule = map_schedule(base.canonical(), catalog_size, distribution)
    target = root / "mapped" / scenario
    base.write_tsv(target / "schedule.tsv", SCHEDULE_COLUMNS, schedule)

    probes = []
    for index, (source, target_cluster, requester, owner) in enumerate((
            ("A", "B", 2, 40), ("B", "A", 34, 8)), 1):
        key = f"federation_v3h3_probe_{target_cluster}"
        probes.append({"probe_index": index, "direction": f"{source}_to_{target_cluster}",
                       "requester": requester, "owner": owner, "requester_cluster": source,
                       "owner_cluster": target_cluster, "key": key,
                       "bloom_positions": signature(key), **base.descriptor(key),
                       "schedule_sha256": schedule[0]["schedule_sha256"]})
    base.write_tsv(target / "isolation_probes.tsv",
                   ["probe_index", "direction", "requester", "owner", "requester_cluster",
                    "owner_cluster", "key", "bloom_positions", "value_size_bytes", "content_seed",
                    "expected_fnv1a64", "schedule_sha256"], probes)

    catalog = []
    for cluster in ("A", "B"):
        for rank in range(1, catalog_size + 1):
            key = key_name(cluster, rank)
            catalog.append({"cluster": cluster, "key_rank": rank,
                            "owner_local_rank": (rank - 1) // 32 + 1, "key": key,
                            "owner": owner_absolute(cluster, rank),
                            "bloom_positions": signature(key), **base.descriptor(key)})
    base.write_tsv(target / "catalog.tsv",
                   ["cluster", "key_rank", "owner_local_rank", "key", "owner", "bloom_positions",
                    "value_size_bytes", "content_seed", "expected_fnv1a64"], catalog)
    for node in range(1, 65):
        rows = [row for row in catalog if row["owner"] == node]
        rows += [row for row in probes if int(row["owner"]) == node]
        base.write_tsv(target / "data" / f"node{node}.tsv",
                       ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"], rows)

    summary = bloom_summary(catalog, schedule)
    (target / "bloom_collision_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    activation = {"boundary_nodes": [1, 33], "peers": {"1": 33, "33": 1},
                  "endpoints": {"1": "10.90.0.1:44001", "33": "10.90.0.33:44033"},
                  "semantics": "runtime-local-bootstrap",
                  "schedule_sha256": schedule[0]["schedule_sha256"]}
    (target / "activation.json").write_text(json.dumps(activation, indent=2, sort_keys=True) + "\n")
    counts = Counter((row["phase"], row["request_class"]) for row in schedule)
    metadata = {"suite": suite, "scenario": scenario, "slug": slug,
                "seed": 1, "catalog_size_per_cluster": catalog_size, "distribution": distribution,
                "zipf_alpha": base.ZIPF_ALPHA if distribution == "zipf" else None,
                "measurement_requests": len(schedule), "pre_local_requests": counts[("pre", "local")],
                "post_remote_requests": counts[("post", "remote")],
                "post_local_control_requests": counts[("post", "local_control")],
                "value_size_bytes": base.SIZE, "wire_version": 2, "max_hop_count": 12,
                "schedule_sha256": schedule[0]["schedule_sha256"], **summary}
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    scenarios = root / "scenarios"; scenarios.mkdir(parents=True, exist_ok=True)
    yaml = topology.render_scenario().replace("federation-v2", suite, 1)
    yaml = yaml.replace("max_hop_count: 8", "max_hop_count: 12")
    (scenarios / f"{scenario}.yaml").write_text(yaml)


def validate(root: Path) -> None:
    schedules = {}
    for slug, catalog_size, distribution in CONFIGS:
        scenario = f"kua-dc-v3-{suite_name()}-{slug}-nc-seed001"
        with (root / "mapped" / scenario / "schedule.tsv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        schedules[slug] = rows
        assert len(rows) == base.PRE_BINS * 128 + base.POST_BINS * 160
        assert sum(row["phase"] == "pre" for row in rows) == base.PRE_BINS * 128
        assert sum(row["request_class"] == "remote" for row in rows) == base.POST_BINS * 128
        assert sum(row["request_class"] == "local_control" for row in rows) == base.POST_BINS * 32
        assert max(int(row["original_owner_overlay_hops"]) for row in rows) <= 11
        assert all(len(row["bloom_positions"].split(";")) == 3 for row in rows)
    endpoint_fields = ("request_index", "phase", "bin", "request_class", "direction", "requester", "owner")
    baseline = [tuple(row[field] for field in endpoint_fields) for row in schedules[CONFIGS[0][0]]]
    assert all(baseline == [tuple(row[field] for field in endpoint_fields) for row in schedules[slug]]
               for slug, _, _ in CONFIGS[1:])
    for cluster in ("A", "B"):
        assert {key_name(cluster, rank) for rank in range(1, 513)} < {
            key_name(cluster, rank) for rank in range(1, 1001)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    for config in CONFIGS: generate(args.output_root, *config)
    validate(args.output_root)
    print(f"generated {suite_name()} workloads under {args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
