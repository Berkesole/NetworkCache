#!/usr/bin/env python3
"""Generate deterministic dual-32-host federation-v2 workloads."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import subprocess
import tempfile
from collections import Counter, deque
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import generate_latency_v3 as latency
import generate_workloads_v3 as v3


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_DIR = SCRIPT_DIR.parent
ROOT = EXP_DIR / "workloads/federation-v2"
CANONICAL = ROOT / "canonical"
MAPPED = ROOT / "mapped"
SCENARIOS = ROOT / "scenarios"
SEEDS = range(1, 11)
SIZE = 65536
HOSTS = 64
CATALOG_SIZE = 130
SCHEDULE_COLUMNS = [
    "request_index", "phase", "round", "request_class", "direction", "key",
    "key_rank", "requester", "owner", "requester_cluster", "owner_cluster",
    "original_owner_overlay_hops", "overlay_edge_physical_signature",
    "value_size_bytes", "content_seed", "expected_fnv1a64", "schedule_sha256",
]


@lru_cache(maxsize=1)
def collision_free_catalog() -> Tuple[str, ...]:
    """Allocate every measured/probe object to a distinct production BF slot."""
    source = SCRIPT_DIR / "bloom_hash_slots.cpp"
    binary = Path(tempfile.gettempdir()) / "networkcache-federation-v2-bloom-hash-slots"
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-O2", str(source), "-o", str(binary)], check=True)
    candidates = [f"federation_v2_object_{index:05d}" for index in range(1, 16385)]
    result = subprocess.run([str(binary), *candidates], text=True, capture_output=True, check=True)
    selected: List[str] = []
    used: set[int] = set()
    for line in result.stdout.splitlines():
        key, slot_text = line.split("\t", 1)
        slot = int(slot_text)
        if slot in used:
            continue
        selected.append(key)
        used.add(slot)
        if len(selected) == CATALOG_SIZE:
            break
    if len(selected) != CATALOG_SIZE:
        raise RuntimeError(f"could not construct {CATALOG_SIZE} collision-free federation-v2 keys")
    return tuple(selected)


def local_key(cluster_name: str, rank: int) -> str:
    return collision_free_catalog()[(0 if cluster_name == "A" else 16) + rank - 1]


def probe_key(owner_cluster: str) -> str:
    return collision_free_catalog()[32 + (owner_cluster == "B")]


def repeated_key(owner_cluster: str, rank: int) -> str:
    return collision_free_catalog()[34 + (0 if owner_cluster == "A" else 16) + rank - 1]


def uniform_key(owner_cluster: str, rank: int) -> str:
    return collision_free_catalog()[66 + (0 if owner_cluster == "A" else 32) + rank - 1]


def sha(data: object) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def shifted_graph(offset: int) -> Dict[int, List[int]]:
    base = v3.overlay_graph("kua-dc-v3-32")
    return {node + offset: [peer + offset for peer in peers] for node, peers in base.items()}


def graph() -> Dict[int, List[int]]:
    return shifted_graph(0) | shifted_graph(32)


def shortest_overlay_path(start: int, target: int, federated: bool) -> List[int]:
    adjacency = {node: list(peers) for node, peers in graph().items()}
    if federated:
        adjacency[1].append(33)
        adjacency[33].append(1)
    queue = deque([(start, [start])])
    visited = {start}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        for peer in sorted(adjacency[node]):
            if peer not in visited:
                visited.add(peer)
                queue.append((peer, [*path, peer]))
    raise ValueError(f"no overlay path from {start} to {target}")


def rack(node: int) -> int:
    return ((node - 1) % 32) // 4


def physical_signature(path: List[int]) -> str:
    kinds = []
    for left, right in zip(path, path[1:]):
        if cluster(left) != cluster(right):
            kinds.append("cross_cluster")
        elif rack(left) == rack(right):
            kinds.append("same_rack")
        else:
            kinds.append("cross_rack")
    return ">".join(kinds)


def render_scenario() -> str:
    lines = ["# Generated federation-v2 dual-cluster scenario.", "protocol:",
             "  max_hop_count: 8", "", "nodes:"]
    for node, neighbors in sorted(graph().items()):
        lines += [f"- id: {node}", f"  neighbors: [{', '.join(map(str, neighbors))}]"]
    lines += ["", "scenario:", "  ticks: 100000000", "  data:", "  requests:"]
    return "\n".join(lines) + "\n"


def write_tsv(path: Path, columns: List[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in columns} for row in rows)


def cluster(node: int) -> str:
    return "A" if node <= 32 else "B"


def absolute_node(relative: int, cluster_name: str) -> int:
    return relative + (0 if cluster_name == "A" else 32)


def local_endpoints(rank: int, cluster_name: str, requester_shift: int = 0) -> tuple[int, int]:
    owner_rel = (rank * 2 + 5) % 32 + 1
    requester_rel = (rank * 5 + 9 + requester_shift) % 32 + 1
    if requester_rel == owner_rel:
        requester_rel = requester_rel % 32 + 1
    return absolute_node(requester_rel, cluster_name), absolute_node(owner_rel, cluster_name)


def append_row(rows: List[dict], *, phase: str, round_index: int, request_class: str,
               direction: str, key: str, key_rank: int, requester: int, owner: int) -> None:
    path = shortest_overlay_path(requester, owner, federated=phase == "post")
    rows.append({
        "request_index": len(rows) + 1,
        "phase": phase,
        "round": round_index,
        "request_class": request_class,
        "direction": direction,
        "key_rank": key_rank,
        "key": key,
        "requester": requester,
        "owner": owner,
        "original_owner_overlay_hops": len(path) - 1,
        "overlay_edge_physical_signature": physical_signature(path),
    })


def make(seed: int, workload: str) -> dict:
    if workload not in {"repeated", "uniform"}:
        raise ValueError(workload)
    endpoint_rng = random.Random(f"federation-v2:endpoints:{seed}")
    key_rng = random.Random(f"federation-v2:keys:{seed}:{workload}")
    rows: List[dict] = []

    # Pre-activation: two independently operating clusters.  Each local key
    # occurs four times, and the two clusters are interleaved to control drift.
    for round_index in range(1, 9):
        ranks = list(range(1 if round_index % 2 else 9, 9 if round_index % 2 else 17))
        endpoint_rng.shuffle(ranks)
        for rank in ranks:
            for cluster_name in ("A", "B"):
                requester, owner = local_endpoints(rank, cluster_name, round_index)
                append_row(rows, phase="pre", round_index=round_index,
                           request_class="local", direction=f"{cluster_name}_local",
                           key=local_key(cluster_name, rank), key_rank=rank,
                           requester=requester, owner=owner)

    # Eight matched endpoint/path strata per direction.  Round one uses a
    # requester near the boundary; later rounds use the boundary, where DATA
    # returned by the first access can be reused without changing the catalog.
    endpoint_slots: list[tuple[int, int]] = []
    for slot in range(8):
        owner_rel = (slot * 4 + 8) % 31 + 2
        requester_rel = 2 + slot % 3
        if requester_rel == owner_rel:
            requester_rel = requester_rel % 31 + 2
        endpoint_slots.append((requester_rel, owner_rel))

    for round_index in range(1, 9):
        slots = list(range(8))
        endpoint_rng.shuffle(slots)
        for slot in slots:
            for direction in ("A_to_B", "B_to_A"):
                requester_cluster = "A" if direction == "A_to_B" else "B"
                owner_cluster = "B" if direction == "A_to_B" else "A"
                requester_rel, owner_rel = endpoint_slots[slot]
                requester = absolute_node(requester_rel if round_index == 1 else 1, requester_cluster)
                owner = absolute_node(owner_rel, owner_cluster)
                if workload == "repeated":
                    rank = slot + 1 + (0 if round_index % 2 else 8)
                    key = repeated_key(owner_cluster, rank)
                else:
                    rank = slot * 4 + key_rng.randrange(1, 5)
                    key = uniform_key(owner_cluster, rank)
                append_row(rows, phase="post", round_index=round_index,
                           request_class="remote", direction=direction,
                           key=key, key_rank=rank, requester=requester, owner=owner)

        # Local controls stay separate from the remote trajectory but share
        # the same closed-loop and round timing.
        local_ranks = list(range((round_index - 1) % 4 * 4 + 1, (round_index - 1) % 4 * 4 + 5))
        for rank in local_ranks:
            for cluster_name in ("A", "B"):
                requester, owner = local_endpoints(rank, cluster_name, 20 + round_index)
                append_row(rows, phase="post", round_index=round_index,
                           request_class="local_control", direction=f"{cluster_name}_local",
                           key=local_key(cluster_name, rank), key_rank=rank,
                           requester=requester, owner=owner)

    probes = []
    for requester_cluster, owner_cluster in (("A", "B"), ("B", "A")):
        requester = absolute_node(2, requester_cluster)
        owner = absolute_node(8, owner_cluster)
        probes.append({"probe_index": len(probes) + 1, "requester_cluster": requester_cluster,
                       "owner_cluster": owner_cluster, "requester": requester, "owner": owner,
                       "direction": f"{requester_cluster}_to_{owner_cluster}",
                       "key": probe_key(owner_cluster)})
    return {"suite": "federation-v2", "seed": seed, "workload": workload,
            "pre_requests": 128, "post_remote_requests": 128,
            "post_local_control_requests": 64, "measurement_requests": 320,
            "rows": rows, "isolation_probes": probes}


def descriptor(key: str) -> dict:
    content_seed = int.from_bytes(hashlib.sha256(f"federation-v2:{key}".encode()).digest()[:8], "big")
    return {"value_size_bytes": SIZE, "content_seed": content_seed,
            "expected_fnv1a64": latency.content_hash(SIZE, content_seed)}


def generate(seed: int, workload: str) -> None:
    canonical = make(seed, workload)
    canonical_sha256 = sha(canonical)
    canonical["canonical_sha256"] = canonical_sha256
    name = f"kua-dc-v3-federation-v2-{workload}-seed{seed:03d}"
    schedule_hash = sha(canonical["rows"])
    output_rows = []
    for row in canonical["rows"]:
        item = dict(row)
        item.update({"requester_cluster": cluster(int(row["requester"])),
                     "owner_cluster": cluster(int(row["owner"])),
                     "schedule_sha256": schedule_hash, **descriptor(row["key"])})
        output_rows.append(item)
    probe_rows = []
    for probe in canonical["isolation_probes"]:
        probe_rows.append({**probe, **descriptor(probe["key"]), "schedule_sha256": schedule_hash})

    CANONICAL.mkdir(parents=True, exist_ok=True)
    (CANONICAL / f"{workload}-seed{seed:03d}.json").write_text(
        json.dumps(canonical, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    target = MAPPED / name
    write_tsv(target / "schedule.tsv", SCHEDULE_COLUMNS, output_rows)
    write_tsv(target / "isolation_probes.tsv",
              ["probe_index", "direction", "requester", "owner", "requester_cluster",
               "owner_cluster", "key", "value_size_bytes", "content_seed",
               "expected_fnv1a64", "schedule_sha256"], probe_rows)

    all_owned = [(int(row["owner"]), row["key"], descriptor(row["key"])) for row in output_rows]
    all_owned += [(int(row["owner"]), row["key"], descriptor(row["key"])) for row in probe_rows]
    unique = {(owner, key): values for owner, key, values in all_owned}
    for node in range(1, HOSTS + 1):
        data_rows = [{"key": key, **values} for (owner, key), values in sorted(unique.items()) if owner == node]
        write_tsv(target / "data" / f"node{node}.tsv",
                  ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"], data_rows)

    activation = {"boundary_nodes": [1, 33], "peers": {"1": 33, "33": 1},
                  "endpoints": {"1": "10.90.0.1:44001", "33": "10.90.0.33:44033"},
                  "semantics": "runtime-local-bootstrap", "schedule_sha256": schedule_hash}
    (target / "activation.json").write_text(json.dumps(activation, indent=2, sort_keys=True) + "\n")
    metadata = {"suite": "federation-v2", "scenario": name, "seed": seed,
                "workload": workload, "host_count": HOSTS, "cluster_size": 32,
                "value_size_bytes": SIZE, "wire_version": 2, "schedule_sha256": schedule_hash,
                "canonical_sha256": canonical_sha256, "measurement_requests": 320,
                "pre_requests": 128, "post_remote_requests": 128,
                "post_local_control_requests": 64, "isolation_probes": 2,
                "local_catalog_per_cluster": 16,
                "remote_catalog_per_cluster": 16 if workload == "repeated" else 32,
                "boundary_nodes": [1, 33]}
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    SCENARIOS.mkdir(parents=True, exist_ok=True)
    (SCENARIOS / f"{name}.yaml").write_text(render_scenario())


def validate() -> None:
    assert len(collision_free_catalog()) == len(set(collision_free_catalog())) == CATALOG_SIZE
    for seed in SEEDS:
        repeated = make(seed, "repeated")
        uniform = make(seed, "uniform")
        for data in (repeated, uniform):
            rows = data["rows"]
            assert len(rows) == 320
            assert sum(row["phase"] == "pre" for row in rows) == 128
            assert sum(row["phase"] == "post" and row["request_class"] == "remote" for row in rows) == 128
            assert sum(row["request_class"] == "local_control" for row in rows) == 64
            assert all(row["requester"] != row["owner"] for row in rows)
            for round_index in range(1, 9):
                assert sum(row["phase"] == "pre" and row["round"] == round_index for row in rows) == 16
                assert sum(row["phase"] == "post" and row["round"] == round_index and
                           row["request_class"] == "remote" for row in rows) == 16
                assert sum(row["phase"] == "post" and row["round"] == round_index and
                           row["request_class"] == "local_control" for row in rows) == 8
        repeated_remote = [row for row in repeated["rows"] if row["request_class"] == "remote"]
        counts = Counter((row["owner_cluster"] if "owner_cluster" in row else cluster(row["owner"]), row["key"])
                         for row in repeated_remote)
        assert len(counts) == 32 and set(counts.values()) == {4}
        repeated_slots = [(row["phase"], row["round"], row["request_class"], row["direction"],
                           row["requester"], row["owner"]) for row in repeated["rows"]]
        uniform_slots = [(row["phase"], row["round"], row["request_class"], row["direction"],
                          row["requester"], row["owner"]) for row in uniform["rows"]]
        assert repeated_slots == uniform_slots


def main() -> int:
    for seed in SEEDS:
        for workload in ("repeated", "uniform"):
            generate(seed, workload)
    validate()
    print(f"generated federation-v2 workloads under {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
