#!/usr/bin/env python3
"""Generate deterministic dual-32-host federation-v1 workloads."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import generate_latency_v3 as latency
import generate_workloads_v3 as v3


SCRIPT_DIR = Path(__file__).resolve().parent
EXP_DIR = SCRIPT_DIR.parent
ROOT = EXP_DIR / "workloads/federation-v1"
CANONICAL = ROOT / "canonical"
MAPPED = ROOT / "mapped"
SCENARIOS = ROOT / "scenarios"
SEEDS = range(1, 11)
SIZE = 65536
HOSTS_A = tuple(range(1, 33))
HOSTS_B = tuple(range(33, 65))


@lru_cache(maxsize=1)
def collision_free_catalog() -> Tuple[str, ...]:
    """Allocate all warm-up and remote objects to distinct production BF slots."""
    source = SCRIPT_DIR / "bloom_hash_slots.cpp"
    binary = Path(tempfile.gettempdir()) / "networkcache-federation-v1-bloom-hash-slots"
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++20", "-O2", str(source), "-o", str(binary)], check=True)
    candidates = [f"federation_v1_object_{index:05d}" for index in range(1, 4097)]
    result = subprocess.run([str(binary), *candidates], text=True, capture_output=True, check=True)
    selected, used = [], set()
    for line in result.stdout.splitlines():
        key, slot_text = line.split("\t", 1)
        if int(slot_text) in used: continue
        selected.append(key); used.add(int(slot_text))
        if len(selected) == 96: break
    if len(selected) != 96: raise RuntimeError("could not construct 96 collision-free federation-v1 keys")
    return tuple(selected)


def warm_key(slot: int, direction: str) -> str:
    return collision_free_catalog()[slot * 2 + (direction == "B_to_A")]


def remote_key(rank: int) -> str:
    return collision_free_catalog()[32 + rank - 1]


def sha(data: object) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def shifted_graph(offset: int) -> Dict[int, List[int]]:
    base = v3.overlay_graph("kua-dc-v3-32")
    return {node + offset: [peer + offset for peer in peers] for node, peers in base.items()}


def graph() -> Dict[int, List[int]]:
    return shifted_graph(0) | shifted_graph(32)


def render_scenario() -> str:
    lines = ["# Generated federation-v1 dual-cluster scenario.", "protocol:",
             "  max_hop_count: 8", "", "nodes:"]
    for node, neighbors in sorted(graph().items()):
        lines += [f"- id: {node}", f"  neighbors: [{', '.join(map(str, neighbors))}]" ]
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


def local_node(relative: int, direction: str) -> int:
    return relative if direction == "A_to_B" else relative + 32


def remote_node(relative: int, direction: str) -> int:
    return relative + 32 if direction == "A_to_B" else relative


def make(seed: int, workload: str) -> dict:
    endpoint_rng = random.Random(f"federation-v1:endpoints:{seed}")
    key_rng = random.Random(f"federation-v1:keys:{seed}:{workload}")
    rows: List[dict] = []
    index = 0
    # Distinct local warm-up objects establish two independently operating systems.
    for slot in range(16):
        for direction in ("A_to_B", "B_to_A"):
            owner_rel = (slot * 2 + 5) % 32 + 1
            requester_rel = (slot * 5 + 9) % 32 + 1
            if requester_rel == owner_rel: requester_rel = requester_rel % 32 + 1
            owner = local_node(owner_rel, direction)
            requester = local_node(requester_rel, direction)
            for repeat in range(2):
                index += 1
                rows.append({"request_index": index, "phase": "warmup", "round": 0,
                             "direction": cluster(requester) + "_local", "key_rank": slot + 1,
                             "key": warm_key(slot, direction),
                             "requester": requester, "owner": owner})
    endpoint_slots = []
    for slot in range(8):
        # Round-one requesters sit one overlay hop from the boundary.  This
        # keeps the end-to-end overlay path within max_hop_count=8 even when
        # the remote owner is on the farthest rack.
        owner_rel = (slot * 4 + 8) % 31 + 2
        requester_rel = 2 + slot % 3
        if requester_rel == owner_rel: requester_rel = requester_rel % 31 + 2
        endpoint_slots.append((requester_rel, owner_rel))
    for round_index in range(1, 9):
        slots = list(range(8)); endpoint_rng.shuffle(slots)
        for slot in slots:
            for direction in ("A_to_B", "B_to_A"):
                requester_rel, owner_rel = endpoint_slots[slot]
                requester = local_node(requester_rel, direction) if round_index == 1 else local_node(1, direction)
                if workload == "repeated":
                    rank = slot + (33 if direction == "A_to_B" else 1)
                else:
                    # Four catalog keys are assigned to every fixed owner/path
                    # stratum.  Sampling within the stratum gives a globally
                    # uniform 64-key catalog while leaving requester, owner,
                    # direction, and physical/overlay path quotas unchanged.
                    cluster_offset = 32 if direction == "A_to_B" else 0
                    rank = cluster_offset + slot * 4 + key_rng.randrange(1, 5)
                owner = remote_node(owner_rel, direction)
                index += 1
                rows.append({"request_index": index, "phase": "measurement",
                             "round": round_index, "direction": direction,
                             "key_rank": rank, "key": remote_key(rank),
                             "requester": requester, "owner": owner})
    return {"suite": "federation-v1", "seed": seed, "workload": workload,
            "warmup_requests": 64, "measurement_requests": 128, "rows": rows}


def generate(seed: int, workload: str) -> None:
    canonical = make(seed, workload)
    canonical_sha256 = sha(canonical)
    canonical["canonical_sha256"] = canonical_sha256
    name = f"kua-dc-v3-federation-v1-{workload}-seed{seed:03d}"
    schedule_hash = sha(canonical["rows"])
    columns = ["request_index", "phase", "round", "direction", "key", "key_rank",
               "requester", "owner", "requester_cluster", "owner_cluster",
               "value_size_bytes", "content_seed", "expected_fnv1a64", "schedule_sha256"]
    output_rows = []
    for row in canonical["rows"]:
        item = dict(row)
        content_seed = int.from_bytes(hashlib.sha256(f"federation-v1:{row['key']}".encode()).digest()[:8], "big")
        item.update({"requester_cluster": cluster(int(row["requester"])),
                     "owner_cluster": cluster(int(row["owner"])),
                     "value_size_bytes": SIZE, "content_seed": content_seed,
                     "expected_fnv1a64": latency.content_hash(SIZE, content_seed),
                     "schedule_sha256": schedule_hash})
        output_rows.append(item)
    CANONICAL.mkdir(parents=True, exist_ok=True)
    (CANONICAL / f"{workload}-seed{seed:03d}.json").write_text(json.dumps(canonical, indent=2, sort_keys=True) + "\n")
    target = MAPPED / name
    write_tsv(target / "schedule.tsv", columns, output_rows)
    unique = {(int(row["owner"]), row["key"]): row for row in output_rows}
    for node in range(1, 65):
        write_tsv(target / "data" / f"node{node}.tsv",
                  ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"],
                  [row for (owner, _), row in sorted(unique.items()) if owner == node])
    activation = {"boundary_nodes": [1, 33], "peers": {"1": 33, "33": 1},
                  "endpoints": {"1": "10.90.0.1:44001", "33": "10.90.0.33:44033"},
                  "semantics": "runtime-local-bootstrap", "schedule_sha256": schedule_hash}
    (target / "activation.json").write_text(json.dumps(activation, indent=2, sort_keys=True) + "\n")
    metadata = {"suite": "federation-v1", "scenario": name, "seed": seed,
                "workload": workload, "host_count": 64, "cluster_size": 32,
                "value_size_bytes": SIZE, "wire_version": 2, "schedule_sha256": schedule_hash,
                "canonical_sha256": canonical_sha256, "warmup_requests": 64,
                "measurement_requests": 128, "boundary_nodes": [1, 33]}
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    SCENARIOS.mkdir(parents=True, exist_ok=True)
    (SCENARIOS / f"{name}.yaml").write_text(render_scenario())


def validate() -> None:
    assert len(collision_free_catalog()) == len(set(collision_free_catalog())) == 96
    for seed in SEEDS:
        repeated = make(seed, "repeated"); uniform = make(seed, "uniform")
        for data in (repeated, uniform):
            warm = [r for r in data["rows"] if r["phase"] == "warmup"]
            measured = [r for r in data["rows"] if r["phase"] == "measurement"]
            assert len(warm) == 64 and len(measured) == 128
            assert all(r["requester"] != r["owner"] for r in data["rows"])
            assert all(r["direction"] in ("A_to_B", "B_to_A") for r in measured)
            assert all(sum(r["round"] == rnd for r in measured) == 16 for rnd in range(1, 9))
        rep_slots = [(r["requester"], r["owner"], r["direction"], r["round"])
                     for r in repeated["rows"] if r["phase"] == "measurement"]
        uni_slots = [(r["requester"], r["owner"], r["direction"], r["round"])
                     for r in uniform["rows"] if r["phase"] == "measurement"]
        assert rep_slots == uni_slots


def main() -> int:
    for seed in SEEDS:
        for workload in ("repeated", "uniform"): generate(seed, workload)
    validate()
    print(f"generated federation-v1 workloads under {ROOT}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
