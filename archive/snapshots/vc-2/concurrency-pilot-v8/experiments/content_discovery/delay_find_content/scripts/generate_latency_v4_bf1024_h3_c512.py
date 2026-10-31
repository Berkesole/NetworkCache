#!/usr/bin/env python3
"""Generate the isolated latency-v4 512-key, wire-v3 staged-fallback suite."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import subprocess
import tempfile
from collections import Counter, deque
from pathlib import Path
from typing import Iterable, Sequence

import generate_workloads_v3 as topology


HERE = Path(__file__).resolve().parent
EXP = HERE.parent
SUITE = "latency-v4-bf1024-h3-c512"
ROOT = EXP / "workloads" / SUITE
CANONICAL = ROOT / "canonical"
MAPPED = ROOT / "mapped"
SCENARIOS = ROOT / "scenarios"
SEEDS = tuple(range(1, 11))
TOPOLOGIES = ("kua-dc-v4-16", "kua-dc-v4-32")
CATALOG_SIZE = 512
SETUP_KEY_COUNT = 32
CONTROLLED_FOCUS_COUNT = 32
CONTROLLED_ROUNDS = 8
DEFAULT_IID_REQUESTS = int(os.environ.get("LATENCY_V4_IID_REQUESTS", "2048"))
ZIPF_ALPHA = 1.1
DEFAULT_SIZE = 65536
SIZES = (1024, DEFAULT_SIZE, 1048576)
WIRE_VERSION = 3
BLOOM_BITS = 1024
BLOOM_HASH_COUNT = 3
MAX_HOP_COUNT = 12
HASH_SALTS = (0x9E3779B97F4A7C15, 0xBF58476D1CE4E5B9, 0x94D049BB133111EB)

# 16 slots: four distances, four gateway requesters/owners and 12 ordinary.
SLOT_TEMPLATE = (
    (("G", "G", 1, "C"), 1), (("G", "O", 1, "S"), 1),
    (("O", "G", 1, "S"), 2), (("G", "O", 2, "CS"), 1),
    (("O", "G", 2, "SC"), 1), (("O", "O", 2, "SS"), 2),
    (("G", "O", 3, "CCS"), 1), (("O", "O", 3, "SCS"), 3),
    (("O", "O", 4, "SCCS"), 4),
)


def expand(multiplier: int = 1) -> list[tuple[str, str, int, str]]:
    return [signature for signature, count in SLOT_TEMPLATE
            for _ in range(count * multiplier)]


def v3_name(v4_name: str) -> str:
    return v4_name.replace("kua-dc-v4-", "kua-dc-v3-")


def hosts(name: str) -> list[int]:
    return topology.hosts(v3_name(name))


def gateways(name: str) -> list[int]:
    return topology.gateways(v3_name(name))


def graph(name: str) -> dict[int, list[int]]:
    return topology.overlay_graph(v3_name(name))


def rack(node: int) -> int:
    return (node - 1) // 4


def role(name: str, node: int) -> str:
    return "G" if node in set(gateways(name)) else "O"


def shortest_path(edges: dict[int, list[int]], source: int, target: int) -> list[int]:
    pending = deque([source]); parent: dict[int, int | None] = {source: None}
    while pending:
        current = pending.popleft()
        if current == target: break
        for neighbor in sorted(edges[current]):
            if neighbor not in parent:
                parent[neighbor] = current; pending.append(neighbor)
    if target not in parent: raise ValueError(f"no path {source}->{target}")
    path, current = [], target
    while current is not None:
        path.append(current); current = parent[current]
    return list(reversed(path))


def pair_signature(name: str, requester: int, owner: int) -> tuple[str, str, int, str]:
    path = shortest_path(graph(name), requester, owner)
    physical = "".join("S" if rack(a) == rack(b) else "C" for a, b in zip(path, path[1:]))
    return role(name, requester), role(name, owner), len(path) - 1, physical


def catalog_keys() -> tuple[str, ...]:
    return tuple(f"latency_v4_object_{rank:04d}" for rank in range(1, CATALOG_SIZE + 1))


def setup_keys() -> tuple[str, ...]:
    return tuple(f"latency_v4_setup_{rank:03d}" for rank in range(1, SETUP_KEY_COUNT + 1))


def gateway_ranks() -> set[int]:
    return set(range(4, CATALOG_SIZE + 1, 4))


def key_role(key: str) -> str:
    if key.startswith("latency_v4_object_"):
        return "G" if int(key.rsplit("_", 1)[1]) in gateway_ranks() else "O"
    if key.startswith("latency_v4_setup_"):
        return "G" if int(key.rsplit("_", 1)[1]) % 4 == 0 else "O"
    raise ValueError(key)


def controlled_focus() -> dict[str, list[str]]:
    gateway = [catalog_keys()[rank - 1] for rank in sorted(gateway_ranks())[:8]]
    ordinary = [key for key in catalog_keys() if key_role(key) == "O"][:24]
    return {"G": gateway, "O": ordinary}


def setup_canonical(seed: int) -> list[dict]:
    slots = expand(2)
    random.Random(f"{SUITE}:setup:{seed}").shuffle(slots)
    by_role = {r: [key for key in setup_keys() if key_role(key) == r] for r in ("G", "O")}
    rows = []
    for index, signature in enumerate(slots, 1):
        rows.append({"request_index": index, "phase": "setup", "round": 0, "bin": 0,
                     "key": by_role[signature[1]].pop(0), "key_rank": 0,
                     "key_u": "", "signature": signature})
    return rows


def controlled(seed: int) -> dict:
    rows = setup_canonical(seed); focus = controlled_focus()
    rng = random.Random(f"{SUITE}:controlled:{seed}")
    index = len(rows)
    for round_index in range(1, CONTROLLED_ROUNDS + 1):
        slots = expand(2); rng.shuffle(slots)
        queues = {name: list(values) for name, values in focus.items()}
        for name, values in queues.items():
            shift = (round_index - 1) % len(values)
            queues[name] = values[shift:] + values[:shift]
        for signature in slots:
            key = queues[signature[1]].pop(0); index += 1
            rows.append({"request_index": index, "phase": "measurement", "round": round_index,
                         "bin": round_index, "key": key,
                         "key_rank": catalog_keys().index(key) + 1, "key_u": "",
                         "signature": signature})
    return canonical_document("controlled", seed, rows)


def inverse_rank(u: float, workload: str) -> int:
    if workload == "iid-uniform": return min(CATALOG_SIZE, int(u * CATALOG_SIZE) + 1)
    weights = [1.0 / (rank ** ZIPF_ALPHA) for rank in range(1, CATALOG_SIZE + 1)]
    target, running, total = u * sum(weights), 0.0, sum(weights)
    for rank, weight in enumerate(weights, 1):
        running += weight
        if running >= target: return rank
    return CATALOG_SIZE


def rank_for_role(u: float, workload: str, owner_role: str) -> int:
    ranks = [rank for rank in range(1, CATALOG_SIZE + 1)
             if ("G" if rank in gateway_ranks() else "O") == owner_role]
    if workload == "iid-uniform": return ranks[min(len(ranks) - 1, int(u * len(ranks)))]
    weights = [1.0 / (rank ** ZIPF_ALPHA) for rank in ranks]
    target, running = u * sum(weights), 0.0
    for rank, weight in zip(ranks, weights):
        running += weight
        if running >= target: return rank
    return ranks[-1]


def iid(workload: str, seed: int, requests: int) -> dict:
    if requests not in (2048, 4096) or requests % 128:
        raise ValueError("IID requests must be 2048 or 4096")
    rows = setup_canonical(seed); index = len(rows)
    endpoint_rng = random.Random(f"{SUITE}:iid-endpoints:{seed}")
    quantile_rng = random.Random(f"{SUITE}:iid-quantiles:{seed}")
    bins = requests // 128
    for bin_index in range(1, bins + 1):
        slots = expand(8); endpoint_rng.shuffle(slots)
        for signature in slots:
            u = quantile_rng.random()
            rank = rank_for_role(u, workload, signature[1]); index += 1
            rows.append({"request_index": index, "phase": "measurement", "round": 0,
                         "bin": bin_index, "key": catalog_keys()[rank - 1],
                         "key_rank": rank, "key_u": f"{u:.17g}", "signature": signature})
    return canonical_document(workload, seed, rows)


def canonical_document(workload: str, seed: int, rows: list[dict]) -> dict:
    document = {"suite": SUITE, "workload": workload, "seed": seed,
                "catalog_size": CATALOG_SIZE, "setup_key_count": SETUP_KEY_COUNT,
                "iid_requests": sum(row["phase"] == "measurement" for row in rows)
                    if workload != "controlled" else None,
                "zipf_alpha": ZIPF_ALPHA if workload == "iid-zipf" else None,
                "requests": rows}
    document["canonical_sha256"] = object_hash(document)
    return document


def canonical_for(workload: str, seed: int, iid_requests: int) -> dict:
    return controlled(seed) if workload == "controlled" else iid(workload, seed, iid_requests)


def object_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assign_owners(name: str, seed: int) -> dict[str, int]:
    pools = {r: [node for node in hosts(name) if role(name, node) == r] for r in ("G", "O")}
    rng = random.Random(f"{SUITE}:owners:{name}:{seed}")
    for pool in pools.values(): rng.shuffle(pool)
    counters = Counter(); output = {}
    for key in (*catalog_keys(), *setup_keys()):
        owner_role = key_role(key); pool = pools[owner_role]
        output[key] = pool[counters[owner_role] % len(pool)]; counters[owner_role] += 1
    return output


def map_document(name: str, source: dict, seed: int) -> dict:
    owners = assign_owners(name, seed); rng = random.Random(f"{SUITE}:mapping:{name}:{seed}")
    usage: Counter[tuple[str, int]] = Counter(); rows = []
    for request in source["requests"]:
        signature = tuple(request["signature"]); owner = owners[request["key"]]
        choices = [node for node in hosts(name) if node != owner and pair_signature(name, node, owner) == signature]
        if not choices: raise ValueError(f"no mapping {name} {signature} owner={owner}")
        rng.shuffle(choices)
        requester = min(choices, key=lambda node: (usage[(request["key"], node)], node))
        usage[(request["key"], requester)] += 1
        row = dict(request); row.pop("signature")
        row.update({"requester": requester, "owner": owner,
                    "requester_role": signature[0], "owner_role": signature[1],
                    "original_owner_overlay_hops": signature[2],
                    "overlay_edge_physical_signature": signature[3],
                    "round_index": request["round"], "request_bin": request["bin"]})
        rows.append(row)
    return {**source, "topology": name, "host_count": len(hosts(name)),
            "owners": owners, "requests": rows}


def content_seed(key: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{SUITE}:{key}".encode()).digest()[:8], "big")


def bloom_positions(key: str) -> tuple[int, int, int]:
    output = []
    for salt in HASH_SALTS:
        value = 14695981039346656037 ^ salt
        for byte in key.encode():
            value ^= byte; value = (value * 1099511628211) & ((1 << 64) - 1)
        output.append(value % BLOOM_BITS)
    return tuple(output)  # type: ignore[return-value]


HASH_CACHE: dict[tuple[int, int], str] = {}


def prepare_hashes(size: int, keys: Sequence[str]) -> None:
    seeds = sorted({content_seed(key) for key in keys if (size, content_seed(key)) not in HASH_CACHE})
    if not seeds: return
    source = HERE / "payload_hashes_sized.cpp"
    binary = Path(tempfile.gettempdir()) / "networkcache-payload-hashes-sized"
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-O3", str(source), "-o", str(binary)], check=True)
    for start in range(0, len(seeds), 256):
        result = subprocess.run([str(binary), str(size), *map(str, seeds[start:start + 256])],
                                text=True, capture_output=True, check=True)
        for line in result.stdout.splitlines():
            seed, digest = line.split("\t"); HASH_CACHE[(size, int(seed))] = digest


def descriptor(key: str, size: int) -> dict:
    seed = content_seed(key)
    return {"key": key, "value_size_bytes": size, "content_seed": seed,
            "expected_fnv1a64": HASH_CACHE[(size, seed)]}


def write_tsv(path: Path, fields: Sequence[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader(); writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def scenario_name(name: str, workload: str, size: int, seed: int) -> str:
    return f"{name}-{SUITE}-{workload}-b{size}-seed{seed:03d}"


def render_scenario(name: str) -> str:
    lines = ["# Generated latency-v4 staged-fallback scenario.", "protocol:",
             f"  max_hop_count: {MAX_HOP_COUNT}", "", "nodes:"]
    edges = graph(name)
    for node in hosts(name):
        lines += [f"- id: {node}", f"  neighbors: [{', '.join(map(str, edges[node]))}]"]
    return "\n".join(lines + ["", "scenario:", "  ticks: 100000000", "  data:", "  requests:", ""])


def generate_one(name: str, workload: str, size: int, seed: int, iid_requests: int) -> None:
    source = canonical_for(workload, seed, iid_requests)
    mapped = map_document(name, source, seed)
    scenario = scenario_name(name, workload, size, seed)
    prepare_hashes(size, (*catalog_keys(), *setup_keys()))
    mapped.update({"scenario": scenario, "value_size_bytes": size, "wire_version": WIRE_VERSION,
                   "bloom_bits": BLOOM_BITS, "bloom_hash_count": BLOOM_HASH_COUNT,
                   "staged_fallback": True, "fallback_delay_ms": 1500,
                   "query_table_timeout_ms": 7000, "controller_timeout_ms": 8000,
                   "max_hop_count": MAX_HOP_COUNT, "formal_catalog_size": CATALOG_SIZE,
                   "setup_key_count": SETUP_KEY_COUNT, "mapping_sha256": ""})
    mapped["mapping_sha256"] = object_hash(mapped)
    path = MAPPED / scenario; path.mkdir(parents=True, exist_ok=True)
    # The immutable schedule/catalog TSVs are canonical run inputs. Avoid
    # duplicating thousands of request rows inside every metadata sidecar.
    metadata = {key: value for key, value in mapped.items() if key not in ("requests", "owners")}
    (MAPPED / f"{scenario}.metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    (SCENARIOS / f"{scenario}.yaml").parent.mkdir(parents=True, exist_ok=True)
    (SCENARIOS / f"{scenario}.yaml").write_text(render_scenario(name))
    schedule = []
    for request in mapped["requests"]:
        row = {**request, **descriptor(request["key"], size),
               "schedule_sha256": mapped["mapping_sha256"]}; schedule.append(row)
    write_tsv(path / "schedule.tsv",
              ["request_index", "phase", "round", "bin", "key", "key_rank", "key_u",
               "requester", "owner", "requester_role", "owner_role",
               "original_owner_overlay_hops", "overlay_edge_physical_signature",
               "value_size_bytes", "content_seed", "expected_fnv1a64", "schedule_sha256"], schedule)
    catalog = [{**descriptor(key, size), "owner": mapped["owners"][key],
                "bloom_positions": ";".join(map(str, bloom_positions(key))),
                "catalog_class": "formal" if key in set(catalog_keys()) else "setup"}
               for key in (*catalog_keys(), *setup_keys())]
    write_tsv(path / "catalog.tsv", ["key", "owner", "catalog_class", "bloom_positions", "value_size_bytes",
                                      "content_seed", "expected_fnv1a64"], catalog)
    for node in hosts(name):
        write_tsv(path / "data" / f"node{node}.tsv",
                  ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"],
                  [row for row in catalog if row["owner"] == node])


def configs() -> list[tuple[str, str, int]]:
    rows = [(name, workload, DEFAULT_SIZE) for name in TOPOLOGIES
            for workload in ("controlled", "iid-zipf", "iid-uniform")]
    rows += [("kua-dc-v4-32", "controlled", size) for size in (1024, 1048576)]
    return rows


def validate(iid_requests: int) -> None:
    assert len(catalog_keys()) == 512 and len(gateway_ranks()) == 128
    assert not set(catalog_keys()) & set(setup_keys())
    for seed in SEEDS:
        for workload in ("controlled", "iid-zipf", "iid-uniform"):
            source = canonical_for(workload, seed, iid_requests)
            left = map_document("kua-dc-v4-16", source, seed)
            right = map_document("kua-dc-v4-32", source, seed)
            expected = 256 if workload == "controlled" else iid_requests
            assert sum(row["phase"] == "measurement" for row in left["requests"]) == expected
            for a, b in zip(left["requests"], right["requests"]):
                paired = ("request_index", "phase", "round", "bin", "key", "key_rank", "key_u",
                          "requester_role", "owner_role", "original_owner_overlay_hops",
                          "overlay_edge_physical_signature")
                assert all(a[field] == b[field] for field in paired)
            for document in (left, right):
                formal_counts = Counter(document["owners"][key] for key in catalog_keys())
                setup_counts = Counter(document["owners"][key] for key in setup_keys())
                assert set(formal_counts.values()) == ({32} if document["host_count"] == 16 else {16})
                assert set(setup_counts.values()) == ({2} if document["host_count"] == 16 else {1})
                measurements = [row for row in document["requests"] if row["phase"] == "measurement"]
                for group in range(1, 9 if workload == "controlled" else iid_requests // 128 + 1):
                    rows = [row for row in measurements if row["bin"] == group]
                    assert Counter(row["original_owner_overlay_hops"] for row in rows) == {1: len(rows)//4, 2: len(rows)//4, 3: len(rows)//4, 4: len(rows)//4}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iid-requests", type=int, choices=(2048, 4096), default=DEFAULT_IID_REQUESTS)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for seed in SEEDS:
        for workload in ("controlled", "iid-zipf", "iid-uniform"):
            source = canonical_for(workload, seed, args.iid_requests)
            CANONICAL.mkdir(parents=True, exist_ok=True)
            (CANONICAL / f"{workload}-seed{seed:03d}.json").write_text(json.dumps(source, indent=2, sort_keys=True) + "\n")
    for name, workload, size in configs():
        for seed in SEEDS: generate_one(name, workload, size, seed, args.iid_requests)
    validate(args.iid_requests)
    print(f"latency-v4 generator check passed ({args.iid_requests} IID requests)" if args.check else f"generated {SUITE} under {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
