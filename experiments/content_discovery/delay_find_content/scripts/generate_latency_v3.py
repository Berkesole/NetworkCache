#!/usr/bin/env python3
"""Generate strictly paired latency-v3 schedules without touching latency-v2."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter, deque
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import generate_workloads_v3 as v3


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_DIR = SCRIPT_DIR.parent
ROOT = EXP_DIR / "workloads" / "latency-v3"
CANONICAL_DIR = ROOT / "canonical"
MAPPED_DIR = ROOT / "mapped"
SCENARIO_DIR = ROOT / "scenarios"
SEEDS = tuple(range(1, 11))
TOPOLOGIES = ("kua-dc-v3-16", "kua-dc-v3-32")
DEFAULT_SIZE = 65536
SIZES = (1024, 65536, 1048576)
ZIPF_ALPHA = 1.1
SCENARIO_TICKS = 100_000_000

# requester role, owner role, overlay hops, per-overlay-edge physical class.
CONTROLLED_TEMPLATE = (
    (("G", "G", 1, "C"), 1), (("G", "O", 1, "S"), 1),
    (("O", "G", 1, "S"), 2), (("G", "O", 2, "CS"), 1),
    (("O", "G", 2, "SC"), 1), (("O", "O", 2, "SS"), 2),
    (("G", "O", 3, "CCS"), 1), (("O", "O", 3, "SCS"), 3),
    (("O", "O", 4, "SCCS"), 4),
)
IID_TEMPLATE = (
    (("G", "G", 1, "C"), 1), (("G", "O", 1, "S"), 2),
    (("O", "G", 1, "S"), 2), (("G", "G", 2, "CC"), 1),
    (("G", "O", 2, "CS"), 1), (("O", "G", 2, "SC"), 1),
    (("O", "O", 2, "SS"), 2), (("O", "O", 3, "SCS"), 5),
    (("O", "O", 4, "SCCS"), 5),
)


def expand(template: Sequence[Tuple[Tuple[str, str, int, str], int]]) -> List[Tuple[str, str, int, str]]:
    return [signature for signature, count in template for _ in range(count)]


def rack(node: int) -> int:
    return (node - 1) // 4


def role(topology: str, node: int) -> str:
    return "G" if node in set(v3.gateways(topology)) else "O"


def shortest_path(graph: Dict[int, List[int]], source: int, target: int) -> List[int]:
    queue = deque([source])
    parent = {source: None}
    while queue:
        current = queue.popleft()
        if current == target:
            break
        for neighbor in sorted(graph[current]):
            if neighbor not in parent:
                parent[neighbor] = current
                queue.append(neighbor)
    if target not in parent:
        raise ValueError(f"no overlay path {source}->{target}")
    result: List[int] = []
    current = target
    while current is not None:
        result.append(current)
        current = parent[current]
    return list(reversed(result))


def pair_signature(topology: str, requester: int, owner: int) -> Tuple[str, str, int, str]:
    path = shortest_path(v3.overlay_graph(topology), requester, owner)
    edges = "".join("S" if rack(left) == rack(right) else "C" for left, right in zip(path, path[1:]))
    return role(topology, requester), role(topology, owner), len(path) - 1, edges


def canonical_path(workload: str, seed: int) -> Path:
    return CANONICAL_DIR / f"{workload}-seed{seed:03d}.json"


def gateway_ranks() -> set[int]:
    # Exactly one quarter of the catalog. Keeping rank 1 outside this set avoids
    # making a gateway-only endpoint slot dominate the Zipf sequence.
    return set(range(17, 33))


@lru_cache(maxsize=1)
def collision_free_catalog() -> Tuple[str, ...]:
    """Use the production std::hash implementation to allocate unique BF slots."""
    source = SCRIPT_DIR / "bloom_hash_slots.cpp"
    binary = Path(tempfile.gettempdir()) / "networkcache-latency-v3-bloom-hash-slots"
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-O2", str(source), "-o", str(binary)], check=True)
    candidates = [f"latency_v3_object_{index:05d}" for index in range(1, 2049)]
    result = subprocess.run([str(binary), *candidates], text=True, capture_output=True, check=True)
    selected, used = [], set()
    for line in result.stdout.splitlines():
        key, slot_text = line.split("\t", 1)
        if int(slot_text) in used:
            continue
        selected.append(key); used.add(int(slot_text))
        if len(selected) == 80:
            break
    if len(selected) != 80:
        raise RuntimeError("could not construct 80 collision-free latency-v3 keys")
    return tuple(selected)


def measured_keys() -> Tuple[str, ...]:
    return collision_free_catalog()[:64]


def warmup_keys() -> Tuple[str, ...]:
    return collision_free_catalog()[64:]


def measured_rank(key: str) -> int:
    return measured_keys().index(key) + 1


def weighted_choice(rng: random.Random, ranks: Sequence[int], workload: str) -> int:
    if workload == "iid-uniform":
        return rng.choice(list(ranks))
    weights = [1.0 / (rank ** ZIPF_ALPHA) for rank in ranks]
    return rng.choices(list(ranks), weights=weights, k=1)[0]


def controlled_canonical(seed: int) -> Dict[str, object]:
    rng = random.Random(f"latency-v3-controlled:{seed}")
    signatures = expand(CONTROLLED_TEMPLATE)
    keys_by_role = {
        "G": list(measured_keys()[:4]),
        "O": list(measured_keys()[4:16]),
    }
    requests: List[Dict[str, object]] = []
    index = 0
    # Connection/cache-neutral warm-up keys use the same common signature set.
    for slot, signature in enumerate(signatures, 1):
        index += 1
        requests.append({"request_index": index, "phase": "warmup", "round": 0,
                         "bin": 0, "key": warmup_keys()[slot - 1],
                         "key_rank": slot, "signature": signature})
    for round_index in range(1, 9):
        round_signatures = list(signatures)
        rng.shuffle(round_signatures)
        role_queues = {name: list(values) for name, values in keys_by_role.items()}
        for values in role_queues.values():
            shift = (round_index - 1) % len(values)
            values[:] = values[shift:] + values[:shift]
        for signature in round_signatures:
            owner_role = signature[1]
            key = role_queues[owner_role].pop(0)
            index += 1
            requests.append({"request_index": index, "phase": "measurement",
                             "round": round_index, "bin": round_index, "key": key,
                             "key_rank": measured_rank(key), "signature": signature})
    return {"suite": "latency-v3", "workload": "controlled", "seed": seed,
            "measurement_requests": 128, "warmup_requests": 16, "requests": requests}


def iid_canonical(workload: str, seed: int) -> Dict[str, object]:
    endpoint_rng = random.Random(f"latency-v3-iid-endpoints:{seed}")
    key_rng = random.Random(f"latency-v3-iid-keys:{workload}:{seed}")
    template = expand(IID_TEMPLATE)
    gateway = sorted(gateway_ranks())
    ordinary = [rank for rank in range(1, 65) if rank not in gateway_ranks()]
    requests: List[Dict[str, object]] = []
    index = 0
    warm_signatures = expand(CONTROLLED_TEMPLATE)
    # Connection warm-up is deliberately near-to-far. Randomly putting a
    # four-hop broadcast first measures cold TCP setup against QueryTable's
    # legacy 500 ms lifetime and can abort before the measured IID workload.
    for slot, signature in enumerate(warm_signatures, 1):
        index += 1
        requests.append({"request_index": index, "phase": "warmup", "round": 0,
                         "bin": 0, "key": warmup_keys()[slot - 1],
                         "key_rank": slot, "signature": signature})
    for bin_index in range(1, 13):
        signatures = list(template)
        endpoint_rng.shuffle(signatures)
        for signature in signatures:
            ranks = gateway if signature[1] == "G" else ordinary
            key_rank = weighted_choice(key_rng, ranks, workload)
            index += 1
            requests.append({"request_index": index, "phase": "measurement",
                             "round": 0, "bin": bin_index,
                             "key": measured_keys()[key_rank - 1],
                             "key_rank": key_rank, "signature": signature})
    return {"suite": "latency-v3", "workload": workload, "seed": seed,
            "zipf_alpha": ZIPF_ALPHA if workload == "iid-zipf" else None,
            "measurement_requests": 240, "warmup_requests": 16, "requests": requests}


def canonical(workload: str, seed: int) -> Dict[str, object]:
    return controlled_canonical(seed) if workload == "controlled" else iid_canonical(workload, seed)


def json_hash(data: object) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def assign_owners(topology: str, canonical_data: Dict[str, object], seed: int) -> Dict[str, int]:
    rng = random.Random(f"latency-v3-owners:{topology}:{seed}")
    nodes = {name: [node for node in v3.hosts(topology) if role(topology, node) == name]
             for name in ("G", "O")}
    for values in nodes.values():
        rng.shuffle(values)
    key_roles: Dict[str, str] = {}
    for request in canonical_data["requests"]:
        key_roles.setdefault(str(request["key"]), str(request["signature"][1]))
        if key_roles[str(request["key"])] != str(request["signature"][1]):
            raise ValueError(f"owner role changed for {request['key']}")
    counters = Counter()
    owners: Dict[str, int] = {}
    for key in sorted(key_roles):
        owner_role = key_roles[key]
        pool = nodes[owner_role]
        owners[key] = pool[counters[owner_role] % len(pool)]
        counters[owner_role] += 1
    return owners


def map_schedule(topology: str, canonical_data: Dict[str, object], seed: int) -> Dict[str, object]:
    rng = random.Random(f"latency-v3-mapping:{topology}:{seed}")
    owners = assign_owners(topology, canonical_data, seed)
    hosts = v3.hosts(topology)
    usage: Counter[Tuple[str, int]] = Counter()
    mapped: List[Dict[str, object]] = []
    for request in canonical_data["requests"]:
        signature = tuple(request["signature"])
        owner = owners[str(request["key"])]
        candidates = [node for node in hosts if node != owner and pair_signature(topology, node, owner) == signature]
        if not candidates:
            raise ValueError(f"no {topology} mapping for {signature} owner={owner}")
        rng.shuffle(candidates)
        requester = min(candidates, key=lambda node: (usage[(str(request["key"]), node)], node))
        usage[(str(request["key"]), requester)] += 1
        row = dict(request)
        row.update({"requester": requester, "owner": owner,
                    "node": requester, "provider": owner,
                    "requester_role": signature[0], "owner_role": signature[1],
                    "original_owner_overlay_hops": signature[2],
                    "overlay_edge_physical_signature": signature[3],
                    "measurement_position": (int(request["request_index"]) - 16)
                        if request["phase"] == "measurement" else "",
                    "round_index": request["round"], "request_bin": request["bin"],
                    "workload_family": "latency_v3"})
        row.pop("signature", None)
        mapped.append(row)
    result = dict(canonical_data)
    result.update({"topology": topology, "host_count": len(hosts), "requests": mapped,
                   "canonical_sha256": canonical_data["canonical_sha256"]})
    return result


def fnv1a64(data: bytes) -> int:
    value = 14695981039346656037
    for byte in data:
        value ^= byte
        value = (value * 1099511628211) & ((1 << 64) - 1)
    return value


@lru_cache(maxsize=None)
def content_hash(size: int, seed: int) -> str:
    state = seed or 0x9E3779B97F4A7C15
    output = bytearray(size)
    mask = (1 << 64) - 1
    for index in range(size):
        state ^= (state << 13) & mask
        state ^= state >> 7
        state ^= (state << 17) & mask
        state &= mask
        output[index] = state & 0xFF
    return f"{fnv1a64(output):016x}"


def scenario_name(topology: str, workload: str, size: int, seed: int) -> str:
    return f"{topology}-latency-v3-{workload}-b{size}-seed{seed:03d}"


def render_scenario(topology: str) -> str:
    lines = ["# Generated latency-v3 control-driven scenario.", "protocol:", "  max_hop_count: 8", "", "nodes:"]
    graph = v3.overlay_graph(topology)
    for node in v3.hosts(topology):
        lines.extend([f"- id: {node}", f"  neighbors: [{', '.join(map(str, graph[node]))}]"])
    lines.extend(["", "scenario:", f"  ticks: {SCENARIO_TICKS}", "  data:", "  requests:"])
    return "\n".join(lines) + "\n"


def write_tsv(path: Path, columns: Sequence[str], rows: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)


def generate_one(topology: str, workload: str, size: int, seed: int,
                 canonical_override: Dict[str, object] | None = None) -> None:
    canonical_data = canonical_override or canonical(workload, seed)
    canonical_data["canonical_sha256"] = json_hash(canonical_data)
    mapped = map_schedule(topology, canonical_data, seed)
    name = scenario_name(topology, workload, size, seed)
    mapped.update({"scenario": name, "value_size_bytes": size, "wire_version": 2,
                   "workload_id": f"latency-v3-{workload}", "workload_type": f"latency-v3-{workload}",
                   "seed": seed, "request_count": len(mapped["requests"]),
                   "key_count": len({row["key"] for row in mapped["requests"]}),
                   "provider_count": len({row["owner"] for row in mapped["requests"]}),
                   "requester_count": len({row["requester"] for row in mapped["requests"]}),
                   "phase_policy": "single engineering pilot request" if canonical_override else
                                   "16 control warmups followed by globally closed-loop measurements",
                   "engineering_pilot": bool(canonical_override),
                   "scenario_ticks": SCENARIO_TICKS, "spacing_loop_sleep_ms": 25,
                   "max_hop_count": 8,
                   "link_delay_ms": 2, "link_rate_mbit": 1000, "link_queue_packets": 1000,
                   "mtu": 1500, "cache_backend": "redis", "request_timeout_ms": 5000,
                   "think_time_ms": 100, "mapping_sha256": ""})
    mapped["mapping_sha256"] = json_hash(mapped)
    CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
    if canonical_override is None:
        canonical_path(workload, seed).write_text(json.dumps(canonical_data, indent=2, sort_keys=True) + "\n")
    MAPPED_DIR.mkdir(parents=True, exist_ok=True)
    (MAPPED_DIR / f"{name}.metadata.json").write_text(json.dumps(mapped, indent=2, sort_keys=True) + "\n")
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    (SCENARIO_DIR / f"{name}.yaml").write_text(render_scenario(topology))

    rows = []
    for request in mapped["requests"]:
        row = dict(request)
        content_seed = int.from_bytes(hashlib.sha256(f"latency-v3:{request['key']}".encode()).digest()[:8], "big")
        row.update({"value_size_bytes": size, "content_seed": content_seed,
                    "expected_fnv1a64": content_hash(size, content_seed),
                    "schedule_sha256": mapped["mapping_sha256"]})
        rows.append(row)
    write_tsv(MAPPED_DIR / name / "schedule.tsv",
              ["request_index", "phase", "round", "bin", "key", "key_rank", "requester", "owner",
               "requester_role", "owner_role", "original_owner_overlay_hops",
               "overlay_edge_physical_signature", "value_size_bytes", "content_seed",
               "expected_fnv1a64", "schedule_sha256"], rows)
    unique = {(row["owner"], row["key"]): row for row in rows}
    for node in v3.hosts(topology):
        data_rows = [row for (owner, _), row in sorted(unique.items()) if owner == node]
        write_tsv(MAPPED_DIR / name / "data" / f"node{node}.tsv",
                  ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"], data_rows)


def configs() -> List[Tuple[str, str, int]]:
    result = [(topology, workload, DEFAULT_SIZE) for topology in TOPOLOGIES
              for workload in ("controlled", "iid-zipf", "iid-uniform")]
    result.extend([("kua-dc-v3-32", "controlled", size) for size in (1024, 1048576)])
    return result


def validate_generated() -> None:
    for seed in SEEDS:
        for workload in ("controlled", "iid-zipf", "iid-uniform"):
            data = json.loads(canonical_path(workload, seed).read_text())
            counts = Counter(tuple(row["signature"]) for row in data["requests"] if row["phase"] == "measurement")
            expected = 128 if workload == "controlled" else 240
            assert sum(counts.values()) == expected
        for workload in ("controlled", "iid-zipf", "iid-uniform"):
            left_name = scenario_name("kua-dc-v3-16", workload, DEFAULT_SIZE, seed)
            right_name = scenario_name("kua-dc-v3-32", workload, DEFAULT_SIZE, seed)
            left = json.loads((MAPPED_DIR / f"{left_name}.metadata.json").read_text())
            right = json.loads((MAPPED_DIR / f"{right_name}.metadata.json").read_text())
            for a, b in zip(left["requests"], right["requests"]):
                keys = ("request_index", "phase", "round", "bin", "key", "key_rank",
                        "requester_role", "owner_role", "original_owner_overlay_hops",
                        "overlay_edge_physical_signature")
                assert all(a[key] == b[key] for key in keys)
                assert a["requester"] != a["owner"] and b["requester"] != b["owner"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for topology, workload, size in configs():
        for seed in SEEDS:
            generate_one(topology, workload, size, seed)
    pilot_request = dict(next(row for row in controlled_canonical(1)["requests"] if row["phase"] == "measurement"))
    pilot_request.update({"request_index": 1, "round": 1, "bin": 1})
    pilot = {"suite": "latency-v3", "workload": "controlled", "seed": 1,
             "measurement_requests": 1, "warmup_requests": 0, "requests": [pilot_request]}
    generate_one("kua-dc-v3-32", "controlled", 4194304, 1, pilot)
    validate_generated()
    if args.check:
        print("latency-v3 generator check passed")
    else:
        print(f"generated latency-v3 schedules under {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
