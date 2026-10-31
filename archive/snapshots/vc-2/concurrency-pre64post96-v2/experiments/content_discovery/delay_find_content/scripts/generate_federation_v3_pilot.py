#!/usr/bin/env python3
"""Generate the three deterministic federation-v3 large-catalog pilots."""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import os
import random
import subprocess
import tempfile
from collections import Counter, defaultdict
from functools import lru_cache
import os
from pathlib import Path

import generate_federation_v2 as v2


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
DEFAULT_ROOT = EXP / "workloads/federation-v3-pilot"
SIZE = 65536
SEED = 1
ZIPF_ALPHA = 1.1
PRE_BINS = int(os.environ.get("FEDERATION_PRE_BINS", "8"))
POST_BINS = int(os.environ.get("FEDERATION_POST_BINS", "16"))
CONFIGS = (
    ("catalog1000-zipf-nc", 1000, "zipf"),
    ("catalog10000-zipf-nc", 10000, "zipf"),
    ("catalog10000-uniform-nc", 10000, "uniform"),
)
SCHEDULE_COLUMNS = [
    "request_index", "phase", "bin", "round", "request_class", "direction",
    "key", "key_rank", "owner_local_rank", "key_u", "bloom_slot", "requester",
    "owner", "requester_cluster", "owner_cluster", "original_owner_overlay_hops",
    "overlay_edge_physical_signature", "value_size_bytes", "content_seed",
    "expected_fnv1a64", "schedule_sha256",
]


def sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_tsv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)


def key_name(cluster: str, rank: int) -> str:
    return f"federation_v3_{cluster}_{rank:05d}"


def owner_relative(rank: int) -> int:
    return (rank - 1) % 32 + 1


def owner_absolute(cluster: str, rank: int) -> int:
    return owner_relative(rank) + (0 if cluster == "A" else 32)


def ranks_for_owner(catalog_size: int, owner_rel: int) -> list[int]:
    return list(range(owner_rel, catalog_size + 1, 32))


@lru_cache(maxsize=None)
def cdf(length: int, distribution: str) -> tuple[float, ...]:
    weights = [1.0] * length if distribution == "uniform" else [1.0 / (index ** ZIPF_ALPHA) for index in range(1, length + 1)]
    total = sum(weights)
    running = 0.0
    output = []
    for weight in weights:
        running += weight / total
        output.append(running)
    output[-1] = 1.0
    return tuple(output)


def map_key(owner_rel: int, u: float, catalog_size: int, distribution: str) -> tuple[int, int]:
    ranks = ranks_for_owner(catalog_size, owner_rel)
    local_index = bisect.bisect_left(cdf(len(ranks), distribution), u)
    return ranks[local_index], local_index + 1


def balanced_nodes(rng: random.Random, count_each: int) -> list[int]:
    nodes = [node for node in range(1, 33) for _ in range(count_each)]
    rng.shuffle(nodes)
    return nodes


def deranged_requesters(owners: list[int], rng: random.Random) -> list[int]:
    candidates = balanced_nodes(rng, len(owners) // 32)
    for shift in range(len(candidates)):
        rotated = candidates[shift:] + candidates[:shift]
        if all(requester != owner for requester, owner in zip(rotated, owners)):
            return rotated
    raise RuntimeError("unable to derange local requester slots")


def canonical(seed: int = SEED) -> list[dict]:
    endpoint_rng = random.Random(f"federation-v3-pilot:endpoints:{seed}")
    key_rng = random.Random(f"federation-v3-pilot:key-quantiles:{seed}")
    rows: list[dict] = []

    def add(phase: str, bin_index: int, request_class: str, direction: str,
            requester_cluster: str, owner_cluster: str, requester_rel: int,
            owner_rel: int) -> None:
        rows.append({"request_index": len(rows) + 1, "phase": phase, "bin": bin_index,
                     "round": bin_index, "request_class": request_class, "direction": direction,
                     "requester_cluster": requester_cluster, "owner_cluster": owner_cluster,
                     "requester_rel": requester_rel, "owner_rel": owner_rel,
                     "key_u": key_rng.random()})

    for bin_index in range(1, PRE_BINS + 1):
        endpoints = {}
        for cluster in ("A", "B"):
            owners = balanced_nodes(endpoint_rng, 2)
            endpoints[cluster] = (deranged_requesters(owners, endpoint_rng), owners)
        for slot in range(64):
            for cluster in ("A", "B"):
                requesters, owners = endpoints[cluster]
                add("pre", bin_index, "local", f"{cluster}_local", cluster, cluster,
                    requesters[slot], owners[slot])

    for bin_index in range(1, POST_BINS + 1):
        remote = {}
        for direction in ("A_to_B", "B_to_A"):
            source = "A" if direction == "A_to_B" else "B"
            target = "B" if direction == "A_to_B" else "A"
            remote[direction] = (balanced_nodes(endpoint_rng, 2), balanced_nodes(endpoint_rng, 2), source, target)
        local = {}
        for cluster in ("A", "B"):
            owner_pool = list(range(1, 33))
            endpoint_rng.shuffle(owner_pool)
            owners = owner_pool[:16]
            requester_pool = list(range(1, 33))
            endpoint_rng.shuffle(requester_pool)
            for shift in range(32):
                requesters = requester_pool[shift:] + requester_pool[:shift]
                if all(requesters[index] != owners[index] for index in range(16)):
                    local[cluster] = (requesters[:16], owners)
                    break
            else:
                raise RuntimeError("unable to build local-control endpoints")
        local_index = {"A": 0, "B": 0}
        for slot in range(64):
            for direction in ("A_to_B", "B_to_A"):
                requesters, owners, source, target = remote[direction]
                add("post", bin_index, "remote", direction, source, target,
                    requesters[slot], owners[slot])
            if slot % 2 == 1:
                cluster = "A" if (slot // 2) % 2 == 0 else "B"
                requesters, owners = local[cluster]
                index = local_index[cluster]
                add("post", bin_index, "local_control", f"{cluster}_local", cluster, cluster,
                    requesters[index], owners[index])
                local_index[cluster] += 1
        assert local_index == {"A": 16, "B": 16}
    assert len(rows) == PRE_BINS * 128 + POST_BINS * 160
    return rows


def content_seed(key: str) -> int:
    return int.from_bytes(hashlib.sha256(f"federation-v3:{key}".encode()).digest()[:8], "big")


PAYLOAD_HASHES: dict[int, str] = {}


def prepare_payload_hashes(keys: list[str]) -> None:
    missing = sorted({content_seed(key) for key in keys} - PAYLOAD_HASHES.keys())
    if not missing: return
    source = HERE / "payload_hashes.cpp"
    binary = Path(tempfile.gettempdir()) / "networkcache-federation-v3-payload-hashes"
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-O3", str(source), "-o", str(binary)], check=True)
    for start in range(0, len(missing), 500):
        completed = subprocess.run([str(binary), *map(str, missing[start:start + 500])],
                                   text=True, capture_output=True, check=True)
        for line in completed.stdout.splitlines():
            seed, value = line.split("\t")
            PAYLOAD_HASHES[int(seed)] = value


def descriptor(key: str) -> dict:
    seed = content_seed(key)
    return {"value_size_bytes": SIZE, "content_seed": seed,
            "expected_fnv1a64": PAYLOAD_HASHES[seed]}


def bloom_slots(keys: list[str]) -> dict[str, int]:
    source = HERE / "bloom_hash_slots.cpp"
    binary = Path(tempfile.gettempdir()) / "networkcache-federation-v3-bloom-hash-slots"
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-O2", str(source), "-o", str(binary)], check=True)
    output: dict[str, int] = {}
    for start in range(0, len(keys), 1000):
        completed = subprocess.run([str(binary), *keys[start:start + 1000]], text=True, capture_output=True, check=True)
        for line in completed.stdout.splitlines():
            key, slot = line.split("\t")
            output[key] = int(slot)
    return output


def map_schedule(rows: list[dict], catalog_size: int, distribution: str,
                 slots: dict[str, int]) -> list[dict]:
    output = []
    for source in rows:
        item = dict(source)
        rank, local_rank = map_key(int(item["owner_rel"]), float(item["key_u"]), catalog_size, distribution)
        owner_cluster = str(item["owner_cluster"])
        key = key_name(owner_cluster, rank)
        requester = int(item["requester_rel"]) + (0 if item["requester_cluster"] == "A" else 32)
        owner = int(item["owner_rel"]) + (0 if owner_cluster == "A" else 32)
        path = v2.shortest_overlay_path(requester, owner, federated=item["phase"] == "post")
        item.update({"key": key, "key_rank": rank, "owner_local_rank": local_rank,
                     "bloom_slot": slots[key], "requester": requester, "owner": owner,
                     "original_owner_overlay_hops": len(path) - 1,
                     "overlay_edge_physical_signature": v2.physical_signature(path), **descriptor(key)})
        item.pop("requester_rel"); item.pop("owner_rel")
        output.append(item)
    schedule_hash = sha(output)
    for item in output: item["schedule_sha256"] = schedule_hash
    return output


def generate(root: Path, slug: str, catalog_size: int, distribution: str) -> None:
    scenario = f"kua-dc-v3-federation-v3-pilot-{slug}-seed001"
    keys = [key_name(cluster, rank) for cluster in ("A", "B") for rank in range(1, catalog_size + 1)]
    prepare_payload_hashes(keys + ["federation_v3_probe_A", "federation_v3_probe_B"])
    slots = bloom_slots(keys + ["federation_v3_probe_A", "federation_v3_probe_B"])
    schedule = map_schedule(canonical(), catalog_size, distribution, slots)
    target = root / "mapped" / scenario
    write_tsv(target / "schedule.tsv", SCHEDULE_COLUMNS, schedule)

    probes = []
    for index, (source, target_cluster, requester, owner) in enumerate((("A", "B", 2, 40), ("B", "A", 34, 8)), 1):
        key = f"federation_v3_probe_{target_cluster}"
        probes.append({"probe_index": index, "direction": f"{source}_to_{target_cluster}",
                       "requester": requester, "owner": owner, "requester_cluster": source,
                       "owner_cluster": target_cluster, "key": key, "bloom_slot": slots[key],
                       **descriptor(key), "schedule_sha256": schedule[0]["schedule_sha256"]})
    write_tsv(target / "isolation_probes.tsv",
              ["probe_index", "direction", "requester", "owner", "requester_cluster", "owner_cluster",
               "key", "bloom_slot", "value_size_bytes", "content_seed", "expected_fnv1a64", "schedule_sha256"], probes)

    catalog_rows = []
    for cluster in ("A", "B"):
        for rank in range(1, catalog_size + 1):
            key = key_name(cluster, rank)
            catalog_rows.append({"cluster": cluster, "key_rank": rank, "key": key,
                                 "owner": owner_absolute(cluster, rank), "owner_local_rank": (rank - 1) // 32 + 1,
                                 "bloom_slot": slots[key], **descriptor(key)})
    write_tsv(target / "catalog.tsv",
              ["cluster", "key_rank", "owner_local_rank", "key", "owner", "bloom_slot",
               "value_size_bytes", "content_seed", "expected_fnv1a64"], catalog_rows)
    for node in range(1, 65):
        node_rows = [row for row in catalog_rows if row["owner"] == node]
        probe_rows = [row for row in probes if int(row["owner"]) == node]
        write_tsv(target / "data" / f"node{node}.tsv",
                  ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"], node_rows + probe_rows)

    per_owner = defaultdict(list)
    for row in catalog_rows: per_owner[int(row["owner"])].append(int(row["bloom_slot"]))
    requested = [int(row["bloom_slot"]) for row in schedule]
    collision = {"catalog_keys": len(catalog_rows), "catalog_distinct_slots": len({row["bloom_slot"] for row in catalog_rows}),
                 "catalog_collision_count": len(catalog_rows) - len({row["bloom_slot"] for row in catalog_rows}),
                 "requested_rows": len(requested), "requested_distinct_slots": len(set(requested)),
                 "per_owner_min_distinct_slots": min(len(set(values)) for values in per_owner.values()),
                 "per_owner_max_distinct_slots": max(len(set(values)) for values in per_owner.values())}
    (target / "bloom_collision_summary.json").write_text(json.dumps(collision, indent=2, sort_keys=True) + "\n")
    activation = {"boundary_nodes": [1, 33], "peers": {"1": 33, "33": 1},
                  "endpoints": {"1": "10.90.0.1:44001", "33": "10.90.0.33:44033"},
                  "semantics": "runtime-local-bootstrap", "schedule_sha256": schedule[0]["schedule_sha256"]}
    (target / "activation.json").write_text(json.dumps(activation, indent=2, sort_keys=True) + "\n")
    phase_counts = Counter((row["phase"], row["request_class"]) for row in schedule)
    metadata = {"suite": "federation-v3-pilot", "scenario": scenario, "slug": slug, "seed": SEED,
                "catalog_size_per_cluster": catalog_size, "distribution": distribution,
                "zipf_alpha": ZIPF_ALPHA if distribution == "zipf" else None,
                "measurement_requests": len(schedule), "pre_local_requests": phase_counts[("pre", "local")],
                "post_remote_requests": phase_counts[("post", "remote")],
                "post_local_control_requests": phase_counts[("post", "local_control")],
                "value_size_bytes": SIZE, "wire_version": 2,
                "schedule_sha256": schedule[0]["schedule_sha256"], **collision}
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    scenarios = root / "scenarios"; scenarios.mkdir(parents=True, exist_ok=True)
    (scenarios / f"{scenario}.yaml").write_text(v2.render_scenario().replace("federation-v2", "federation-v3-pilot", 1))


def validate(root: Path) -> None:
    schedules = {}
    for slug, catalog_size, distribution in CONFIGS:
        scenario = f"kua-dc-v3-federation-v3-pilot-{slug}-seed001"
        with (root / "mapped" / scenario / "schedule.tsv").open(newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        schedules[slug] = rows
        assert len(rows) == PRE_BINS * 128 + POST_BINS * 160
        assert sum(row["phase"] == "pre" for row in rows) == PRE_BINS * 128
        assert sum(row["request_class"] == "remote" for row in rows) == POST_BINS * 128
        assert sum(row["request_class"] == "local_control" for row in rows) == POST_BINS * 32
        for bin_index in range(1, POST_BINS + 1):
            subset = [row for row in rows if row["phase"] == "post" and row["bin"] == str(bin_index)]
            assert Counter(row["direction"] for row in subset) == {"A_to_B": 64, "B_to_A": 64, "A_local": 16, "B_local": 16}
        counts = Counter(int(row["owner"]) for row in csv.DictReader(
            open(root / "mapped" / scenario / "catalog.tsv"), delimiter="\t"))
        assert max(counts.values()) - min(counts.values()) <= 1
    endpoint_fields = ("request_index", "phase", "bin", "request_class", "direction", "requester", "owner")
    base = [tuple(row[field] for field in endpoint_fields) for row in schedules["catalog10000-zipf-nc"]]
    assert base == [tuple(row[field] for field in endpoint_fields) for row in schedules["catalog10000-uniform-nc"]]
    for cluster in ("A", "B"):
        small = {key_name(cluster, rank) for rank in range(1, 1001)}
        large = {key_name(cluster, rank) for rank in range(1, 10001)}
        assert small < large


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    for config in CONFIGS: generate(args.output_root, *config)
    validate(args.output_root)
    print(f"generated federation-v3 pilot workloads under {args.output_root}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
