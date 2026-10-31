#!/usr/bin/env python3
"""Generate the isolated Kathara latency-v2 experiment matrix."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import generate_workloads_v3 as v3


SEEDS = (1, 2, 3, 4, 5)
TOPOLOGIES = ("kua-dc-v3-16", "kua-dc-v3-32")
LOOP_SLEEP_MS = 25
WARMUP_REQUESTS = 16
CONTROLLED_MEASUREMENT_REQUESTS = 120
IID_MEASUREMENT_REQUESTS = 240
IID_KEY_COUNT = 64
ZIPF_ALPHA = 1.1
BIN_SIZE = 10

KATHARA_SCENARIO_DIR = v3.KATHARA_SCENARIO_DIR
GENERATED_DIR = v3.GENERATED_DIR


@lru_cache(maxsize=1)
def collision_free_catalog() -> Tuple[str, ...]:
    source = Path(__file__).with_name("bloom_hash_slots.cpp")
    binary = Path(tempfile.gettempdir()) / "networkcache-latency-v2-bloom-hash-slots"
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        compiler = os.environ.get("CXX", "c++")
        subprocess.run([compiler, "-std=c++20", "-O2", str(source), "-o", str(binary)], check=True)
    candidates = [f"latency_v2_object_{index:04d}" for index in range(1, 513)]
    result = subprocess.run([str(binary), *candidates], check=True, text=True, capture_output=True)
    selected: List[str] = []
    used_slots = set()
    for line in result.stdout.splitlines():
        key, slot_text = line.split("\t", 1)
        slot = int(slot_text)
        if slot in used_slots:
            continue
        selected.append(key)
        used_slots.add(slot)
        if len(selected) == IID_KEY_COUNT + WARMUP_REQUESTS:
            break
    if len(selected) != IID_KEY_COUNT + WARMUP_REQUESTS:
        raise RuntimeError("could not construct collision-free latency-v2 Bloom catalog")
    return tuple(selected)


def measured_keys() -> List[str]:
    return list(collision_free_catalog()[:IID_KEY_COUNT])


def background_keys() -> List[str]:
    return list(collision_free_catalog()[IID_KEY_COUNT:])


def spacing_delta(spacing_ms: int) -> int:
    return max(1, math.ceil(spacing_ms / LOOP_SLEEP_MS))


def provider_map(topology: str, keys: Iterable[str]) -> Dict[str, int]:
    provider_nodes = v3.providers(topology)
    return {
        key: provider_nodes[index % len(provider_nodes)]
        for index, key in enumerate(keys)
    }


def request_fields(position: int, tick: int, first_tick: int, occurrence: int, rank: int) -> Dict[str, object]:
    return {
        "measurement_position": position,
        "round_index": occurrence,
        "key_occurrence_index": occurrence,
        "popularity_rank": rank,
        "measurement_elapsed_ms": (tick - first_tick) * LOOP_SLEEP_MS,
        "request_bin": ((position - 1) // BIN_SIZE) + 1,
        "workload_family": "latency_v2",
    }


def blank_fields() -> Dict[str, object]:
    return {
        "measurement_position": "",
        "round_index": "",
        "key_occurrence_index": "",
        "popularity_rank": "",
        "measurement_elapsed_ms": "",
        "request_bin": "",
        "workload_family": "latency_v2",
    }


def controlled_requests(topology: str, seed: int, spacing_ms: int) -> List[Dict[str, object]]:
    requests = v3.build_convergence_controlled_requests(topology, seed, spacing_ms)
    warmup = [dict(row) for row in requests if row["phase"] == "warmup"]
    measurement = [dict(row) for row in requests if row["phase"] == "measurement"]
    by_round: Dict[int, List[Dict[str, object]]] = {}
    for row in measurement:
        by_round.setdefault(int(row["round_index"]), []).append(row)

    rng = random.Random(f"latency-v2-controlled:{topology}:{seed}")
    delta = spacing_delta(spacing_ms)
    measurement_start = max(int(row["tick"]) for row in warmup) + 4 * delta
    position = 0
    ordered: List[Dict[str, object]] = []
    for round_index in sorted(by_round):
        rows = by_round[round_index]
        rng.shuffle(rows)
        for row in rows:
            position += 1
            tick = measurement_start + (position - 1) * delta
            row["request_index"] = WARMUP_REQUESTS + position
            row["tick"] = tick
            row.update(
                request_fields(
                    position,
                    tick,
                    measurement_start,
                    round_index,
                    int(row["popularity_rank"]),
                )
            )
            ordered.append(row)
    return warmup + ordered


def weighted_key(rng: random.Random, keys: Sequence[str], distribution: str, recent: Sequence[str]) -> str:
    ranks = range(1, len(keys) + 1)
    weights = [1.0 / (rank**ZIPF_ALPHA) if distribution == "zipf" else 1.0 for rank in ranks]
    candidates = [key for key in keys if key not in recent]
    candidate_weights = [weights[keys.index(key)] for key in candidates]
    return rng.choices(candidates, weights=candidate_weights, k=1)[0]


def iid_requests(topology: str, distribution: str, seed: int, spacing_ms: int) -> List[Dict[str, object]]:
    if distribution not in {"zipf", "uniform"}:
        raise ValueError(distribution)
    rng = random.Random(f"latency-v2-iid-{distribution}:{topology}:{seed}:s{spacing_ms}")
    measurement_keys = measured_keys()
    warm_keys = background_keys()
    providers = provider_map(topology, [*measurement_keys, *warm_keys])
    graph = v3.overlay_graph(topology)
    hosts = v3.hosts(topology)
    delta = spacing_delta(spacing_ms)
    warmup_ticks = [80 + index * delta for index in range(WARMUP_REQUESTS)]
    measurement_start = warmup_ticks[-1] + 4 * delta
    measurement_ticks = [measurement_start + index * delta for index in range(IID_MEASUREMENT_REQUESTS)]

    requests: List[Dict[str, object]] = []
    shuffled_warm = list(warm_keys)
    rng.shuffle(shuffled_warm)
    for index, (key, tick) in enumerate(zip(shuffled_warm, warmup_ticks), start=1):
        owner = providers[key]
        requester = rng.choice([node for node in hosts if node != owner])
        requests.append(
            {
                "request_index": index,
                "phase": "warmup",
                "tick": tick,
                "node": requester,
                "key": key,
                "provider": owner,
                "is_first_key_request": True,
                "request_pair_id": "",
                "distance_class": "background_warmup",
                "expected_cold_provider_hop": "",
                "expected_warm_cache_hop": "",
                **blank_fields(),
            }
        )

    occurrences: Counter[str] = Counter()
    recent: List[str] = []
    minimum_gap_slots = 4 if spacing_ms == 250 else 0
    ranks = {key: index + 1 for index, key in enumerate(measurement_keys)}
    for position, tick in enumerate(measurement_ticks, start=1):
        key = weighted_key(rng, measurement_keys, distribution, recent[-minimum_gap_slots:] if minimum_gap_slots else [])
        owner = providers[key]
        requester = rng.choice([node for node in hosts if node != owner])
        occurrences[key] += 1
        provider_hop = v3.shortest_distance(graph, requester, owner)
        requests.append(
            {
                "request_index": WARMUP_REQUESTS + position,
                "phase": "measurement",
                "tick": tick,
                "node": requester,
                "key": key,
                "provider": owner,
                "is_first_key_request": occurrences[key] == 1,
                "request_pair_id": key,
                "distance_class": f"owner_hop_{provider_hop}",
                "expected_cold_provider_hop": str(provider_hop),
                "expected_warm_cache_hop": "",
                **request_fields(position, tick, measurement_start, occurrences[key], ranks[key]),
            }
        )
        recent.append(key)
    return requests


def scenario_name(topology: str, workload: str, spacing_ms: int, seed: int) -> str:
    if workload == "controlled":
        profile = "controlled"
    elif workload == "iid-zipf":
        profile = "iid-zipf-a110"
    else:
        profile = "iid-uniform"
    return f"{topology}-latency-v2-{profile}-s{spacing_ms}-seed{seed:03d}"


def data_entries(topology: str, workload: str, requests: Sequence[Dict[str, object]]) -> List[Tuple[int, str, str]]:
    if workload in {"iid-zipf", "iid-uniform"}:
        all_keys = [*measured_keys(), *background_keys()]
        owners = provider_map(topology, all_keys)
        return [(owners[key], key, f"VALUE_{key}") for key in all_keys]
    owners: Dict[str, int] = {}
    for request in requests:
        key = str(request["key"])
        owner = int(request["provider"])
        if key in owners and owners[key] != owner:
            raise ValueError(f"inconsistent owner for {key}")
        owners[key] = owner
    return [(owner, key, f"VALUE_{key}") for key, owner in sorted(owners.items())]


def render_yaml(topology: str, workload: str, spacing_ms: int, requests: Sequence[Dict[str, object]]) -> str:
    graph = v3.overlay_graph(topology)
    delta = spacing_delta(spacing_ms)
    scenario_ticks = max(int(row["tick"]) for row in requests) + 4 * delta
    lines = [
        f"# Generated latency-v2 {workload} workload for {topology}.",
        "# Hosts run NetworkCache; generated leaf/spine devices only forward L3 traffic.",
        "protocol:",
        "  max_hop_count: 8",
        "",
        "nodes:",
    ]
    for node in v3.hosts(topology):
        lines.append(f"- id: {node}")
        lines.append(f"  neighbors: [{', '.join(str(peer) for peer in graph[node])}]")
    lines.extend(["", "scenario:", f"  ticks: {scenario_ticks}", "  data:"])
    for owner, key, value in data_entries(topology, workload, requests):
        lines.append(f"    - {owner}:{key}={value}")
    lines.append("  requests:")
    for request in requests:
        lines.append(f"    - {request['node']}:{request['key']}@{request['tick']}")
    return "\n".join(lines) + "\n"


def build_metadata(
    topology: str,
    workload: str,
    spacing_ms: int,
    seed: int,
    scenario: str,
    requests: Sequence[Dict[str, object]],
) -> Dict[str, object]:
    entries = data_entries(topology, workload, requests)
    measurement = [row for row in requests if row["phase"] == "measurement"]
    delta = spacing_delta(spacing_ms)
    scenario_ticks = max(int(row["tick"]) for row in requests) + 4 * delta
    empirical = Counter(str(row["key"]) for row in measurement)
    return {
        "suite": "latency-v2",
        "workload_id": f"latency-v2-{workload}-s{spacing_ms}",
        "workload_type": f"latency-v2-{workload}",
        "scenario": scenario,
        "topology": topology,
        "seed": seed,
        "key_count": len(entries),
        "measured_key_count": 15 if workload == "controlled" else IID_KEY_COUNT,
        "background_key_count": WARMUP_REQUESTS,
        "request_count": len(requests),
        "warmup_requests": WARMUP_REQUESTS,
        "measurement_requests": len(measurement),
        "provider_count": len(v3.providers(topology)),
        "requester_count": len(v3.hosts(topology)),
        "host_count": v3.host_count(topology),
        "leaf_count": v3.host_count(topology) // 4,
        "spine_count": 4,
        "measurement_spacing_ms": spacing_ms,
        "spacing_loop_sleep_ms": LOOP_SLEEP_MS,
        "spacing_delta_ticks": delta,
        "scenario_ticks": scenario_ticks,
        "request_bin_size": BIN_SIZE,
        "request_bin_count": math.ceil(len(measurement) / BIN_SIZE),
        "zipf_alpha": ZIPF_ALPHA if workload == "iid-zipf" else None,
        "minimum_same_key_gap_slots": 4 if workload == "iid-zipf" and spacing_ms == 250 else 0,
        "sampling_policy": "iid_weighted" if spacing_ms != 250 else "zipf_weighted_with_four_slot_same_key_gap",
        "bloom_key_policy": "unique_std_hash_mod_1024_within_catalog",
        "phase_policy": f"background_warmup={WARMUP_REQUESTS}; measurement={len(measurement)}; spacing_ms={spacing_ms}",
        "physical_model": "host-only NetworkCache with pure L3 leaf/spine forwarding",
        "providers": v3.providers(topology),
        "requesters": v3.hosts(topology),
        "keys": [{"key": key, "provider": owner, "value": value} for owner, key, value in entries],
        "empirical_measurement_key_counts": dict(sorted(empirical.items())),
        "requests": list(requests),
    }


def validate(topology: str, workload: str, spacing_ms: int, requests: Sequence[Dict[str, object]]) -> None:
    measurement = [row for row in requests if row["phase"] == "measurement"]
    warmup = [row for row in requests if row["phase"] == "warmup"]
    expected = CONTROLLED_MEASUREMENT_REQUESTS if workload == "controlled" else IID_MEASUREMENT_REQUESTS
    if len(warmup) != WARMUP_REQUESTS or len(measurement) != expected:
        raise ValueError("latency-v2 phase count mismatch")
    if set(str(row["key"]) for row in warmup) & set(str(row["key"]) for row in measurement):
        raise ValueError("background warm-up keys overlap measured keys")
    if len({int(row["tick"]) for row in requests}) != len(requests):
        raise ValueError("request ticks are not unique")
    for row in requests:
        if int(row["node"]) == int(row["provider"]):
            raise ValueError("requester equals owner")
    if spacing_ms == 250:
        keys = [str(row["key"]) for row in measurement]
        for index, key in enumerate(keys):
            if key in keys[max(0, index - 4):index]:
                raise ValueError("250ms workload violates same-key gap")


def parse_seeds(text: str) -> Tuple[int, ...]:
    seeds = tuple(int(part.strip()) for part in text.split(",") if part.strip())
    if not seeds or any(seed <= 0 for seed in seeds):
        raise argparse.ArgumentTypeError("seeds must be positive comma-separated integers")
    return seeds


def write_outputs(seeds: Sequence[int], check_only: bool) -> int:
    outputs: List[Tuple[Path, str]] = []
    metadata_outputs: List[Tuple[Path, Dict[str, object]]] = []
    matrix = (("controlled", 2000), ("iid-zipf", 2000), ("iid-uniform", 2000), ("iid-zipf", 250))
    for topology in TOPOLOGIES:
        for workload, spacing_ms in matrix:
            if spacing_ms == 250 and topology != "kua-dc-v3-32":
                continue
            for seed in seeds:
                requests = (
                    controlled_requests(topology, seed, spacing_ms)
                    if workload == "controlled"
                    else iid_requests(topology, workload.removeprefix("iid-"), seed, spacing_ms)
                )
                validate(topology, workload, spacing_ms, requests)
                scenario = scenario_name(topology, workload, spacing_ms, seed)
                outputs.append((KATHARA_SCENARIO_DIR / f"{scenario}.yaml", render_yaml(topology, workload, spacing_ms, requests)))
                metadata_outputs.append(
                    (
                        GENERATED_DIR / f"{scenario}.metadata.json",
                        build_metadata(topology, workload, spacing_ms, seed, scenario, requests),
                    )
                )

    if check_only:
        print(f"validated {len(outputs)} latency-v2 scenarios")
        return 0
    KATHARA_SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in outputs:
        path.write_text(content, encoding="utf-8")
    for path, payload in metadata_outputs:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(outputs)} latency-v2 scenarios and metadata files")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=parse_seeds, default=SEEDS)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return write_outputs(args.seeds, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
