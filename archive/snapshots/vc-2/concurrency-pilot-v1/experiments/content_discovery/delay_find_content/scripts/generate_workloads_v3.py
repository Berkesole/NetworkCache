#!/usr/bin/env python3
"""Generate Kua-DC v3 host-only rsync scenarios.

v3 separates the protocol overlay from the physical Kathara fabric. The
generated scenario YAML contains only rsync hosts. The Kathara renderer
synthesizes leaf/spine forwarding devices for scenario names prefixed with
``kua-dc-v3-``.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[4]
EXP_DIR = Path(__file__).resolve().parents[1]
KATHARA_SCENARIO_DIR = REPO_ROOT / "kathara" / "ruikekkk" / "scenarios"
DOCKER_CONFIG_DIR = EXP_DIR / "configs"
WORKLOAD_DIR = EXP_DIR / "workloads" / "kua-dc-v3"
GENERATED_DIR = WORKLOAD_DIR / "generated"

KEY_COUNT = 64
REQUEST_COUNT = 200
WARMUP_REQUESTS = 60
MEASUREMENT_REQUESTS = 140
LIGHT_REQUEST_COUNT = 120
LIGHT_WARMUP_REQUESTS = 60
LIGHT_MEASUREMENT_REQUESTS = 60
PAIRED_REQUEST_COUNT = 120
PAIRED_WARMUP_REQUESTS = 40
PAIRED_MEASUREMENT_PAIRS = 40
PAIRED_MEASUREMENT_REQUESTS = PAIRED_MEASUREMENT_PAIRS * 2
PAIRBLOCK_REQUEST_COUNT = 64
PAIRBLOCK_WARMUP_REQUESTS = 16
PAIRBLOCK_MEASUREMENT_PAIRS = 24
PAIRBLOCK_MEASUREMENT_REQUESTS = PAIRBLOCK_MEASUREMENT_PAIRS * 2
PAIRBLOCK_INTER_BLOCK_IDLE_MS = 6000
PAIRBLOCK_SPACING_POLICIES_MS = (1500, 2500)
DISTANCEBLOCK_REQUEST_COUNT = 64
DISTANCEBLOCK_WARMUP_REQUESTS = 16
DISTANCEBLOCK_MEASUREMENT_PAIRS = 24
DISTANCEBLOCK_MEASUREMENT_REQUESTS = DISTANCEBLOCK_MEASUREMENT_PAIRS * 2
DISTANCEBLOCK_INTER_BLOCK_IDLE_MS = 6000
DISTANCEBLOCK_SPACING_POLICIES_MS = (2000, 2500)
CONVERGENCE_WARMUP_REQUESTS = 16
CONVERGENCE_CONTROLLED_KEYS = 15
CONVERGENCE_CONTROLLED_ROUNDS = 8
CONVERGENCE_MEASUREMENT_REQUESTS = CONVERGENCE_CONTROLLED_KEYS * CONVERGENCE_CONTROLLED_ROUNDS
CONVERGENCE_REQUEST_COUNT = CONVERGENCE_WARMUP_REQUESTS + CONVERGENCE_MEASUREMENT_REQUESTS
CONVERGENCE_ZIPF_LONG_MEASUREMENT_REQUESTS = 240
CONVERGENCE_ZIPF_LONG_REQUEST_COUNT = CONVERGENCE_WARMUP_REQUESTS + CONVERGENCE_ZIPF_LONG_MEASUREMENT_REQUESTS
CONVERGENCE_SPACING_POLICIES_MS = (2000,)
CONVERGENCE_FALLBACK_SPACING_POLICIES_MS = (2500,)
CONVERGENCE_BIN_SIZE = 10
WARMUP_TICK_RANGE = (80, 520)
MEASUREMENT_TICK_RANGE = (600, 1450)
SCENARIO_TICKS = 1600
SINGLE_KEY_TICKS = 1200
ZIPF_ALPHA = 1.1
KEY_INDEX_COOLDOWN = 3
SEED = 1
SPACING_LOOP_SLEEP_MS = 25
SPACING_POLICIES_MS = (300, 500)
LIGHT_SPACING_MS = 500
PAIRED_SPACING_MS = 700

WORKLOADS = ("zipf", "uniform")
TOPOLOGIES = ("kua-dc-v3-16", "kua-dc-v3-32")


def host_count(topology: str) -> int:
    if topology == "kua-dc-v3-16":
        return 16
    if topology == "kua-dc-v3-32":
        return 32
    raise ValueError(f"unknown topology: {topology}")


def hosts(topology: str) -> List[int]:
    return list(range(1, host_count(topology) + 1))


def racks(topology: str) -> List[List[int]]:
    ids = hosts(topology)
    return [ids[index : index + 4] for index in range(0, len(ids), 4)]


def rack_gateways(rack: Sequence[int]) -> List[int]:
    return [rack[0]]


def gateways(topology: str) -> List[int]:
    result: List[int] = []
    for rack in racks(topology):
        result.extend(rack_gateways(rack))
    return result


def overlay_graph(topology: str) -> Dict[int, List[int]]:
    gateway_nodes = gateways(topology)
    all_gateways = set(gateway_nodes)
    result: Dict[int, List[int]] = {}
    for rack in racks(topology):
        local_gateways = set(rack_gateways(rack))
        for node in rack:
            if node in local_gateways:
                local_neighbors = {peer for peer in rack if peer != node}
                gateway_index = gateway_nodes.index(node)
                remote_gateways = set()
                if len(gateway_nodes) > 1:
                    remote_gateways.add(gateway_nodes[(gateway_index - 1) % len(gateway_nodes)])
                    remote_gateways.add(gateway_nodes[(gateway_index + 1) % len(gateway_nodes)])
                result[node] = sorted(local_neighbors | (remote_gateways - local_gateways))
            else:
                result[node] = sorted(local_gateways)
    return result


def shortest_path(graph: Dict[int, List[int]], src: int, dest: int) -> List[int]:
    if src == dest:
        return [src]
    queue: List[Tuple[int, List[int]]] = [(src, [src])]
    seen = {src}
    for node, path in queue:
        for neighbor in sorted(graph.get(node, [])):
            if neighbor == dest:
                return path + [neighbor]
            if neighbor in seen:
                continue
            seen.add(neighbor)
            queue.append((neighbor, path + [neighbor]))
    raise ValueError(f"no overlay path between {src} and {dest}")


def shortest_distance(graph: Dict[int, List[int]], src: int, dest: int) -> int:
    return len(shortest_path(graph, src, dest)) - 1


def distance_to_cached_path(graph: Dict[int, List[int]], src: int, cached_path: Sequence[int]) -> int:
    return min(shortest_distance(graph, src, cached_node) for cached_node in cached_path)


def providers(topology: str) -> List[int]:
    return [rack[-1] for rack in racks(topology)]


def requesters(topology: str) -> List[int]:
    return hosts(topology)[: host_count(topology) // 2]


def keys() -> List[str]:
    return [f"redis_key_{index:03d}" for index in range(1, KEY_COUNT + 1)]


def workload_network_key(workload_type: str, spacing_ms: int | None = None) -> str:
    if spacing_ms is None:
        return workload_type
    return f"{workload_type}-s{spacing_ms}"


def docker_network(topology: str, workload_type: str, spacing_ms: int | None = None) -> Tuple[str, int]:
    table = {
        ("kua-dc-v3-16", "zipf"): ("172.31.110", 44100),
        ("kua-dc-v3-32", "zipf"): ("172.31.120", 44200),
        ("kua-dc-v3-16", "uniform"): ("172.31.130", 44300),
        ("kua-dc-v3-32", "uniform"): ("172.31.140", 44400),
        ("kua-dc-v3-16", "single-key"): ("172.31.150", 44500),
        ("kua-dc-v3-32", "single-key"): ("172.31.160", 44600),
        ("kua-dc-v3-16", "zipf-s300"): ("172.31.170", 44700),
        ("kua-dc-v3-32", "zipf-s300"): ("172.31.180", 44800),
        ("kua-dc-v3-16", "zipf-s500"): ("172.31.190", 44900),
        ("kua-dc-v3-32", "zipf-s500"): ("172.31.200", 45000),
        ("kua-dc-v3-16", "zipf-light-s500"): ("172.31.210", 45100),
        ("kua-dc-v3-32", "zipf-light-s500"): ("172.31.220", 45200),
        ("kua-dc-v3-16", "zipf-paired-s700"): ("172.31.230", 45300),
        ("kua-dc-v3-32", "zipf-paired-s700"): ("172.31.240", 45400),
        ("kua-dc-v3-16", "zipf-pairblock-s1500"): ("172.31.250", 45500),
        ("kua-dc-v3-32", "zipf-pairblock-s1500"): ("172.31.251", 45600),
        ("kua-dc-v3-16", "zipf-pairblock-s2500"): ("172.31.252", 45700),
        ("kua-dc-v3-32", "zipf-pairblock-s2500"): ("172.31.253", 45800),
        ("kua-dc-v3-16", "zipf-distanceblock-s2000"): ("172.31.254", 45900),
        ("kua-dc-v3-32", "zipf-distanceblock-s2000"): ("172.31.255", 46000),
        ("kua-dc-v3-16", "zipf-distanceblock-s2500"): ("172.30.10", 46100),
        ("kua-dc-v3-32", "zipf-distanceblock-s2500"): ("172.30.20", 46200),
        ("kua-dc-v3-16", "convergence-controlled-s2000"): ("172.30.30", 46300),
        ("kua-dc-v3-32", "convergence-controlled-s2000"): ("172.30.40", 46400),
        ("kua-dc-v3-16", "convergence-zipf-s2000"): ("172.30.50", 46500),
        ("kua-dc-v3-32", "convergence-zipf-s2000"): ("172.30.60", 46600),
        ("kua-dc-v3-16", "convergence-zipf-long-s2000"): ("172.30.65", 46650),
        ("kua-dc-v3-32", "convergence-zipf-long-s2000"): ("172.30.66", 46670),
        ("kua-dc-v3-16", "convergence-uniform-s2000"): ("172.30.70", 46700),
        ("kua-dc-v3-32", "convergence-uniform-s2000"): ("172.30.80", 46800),
        ("kua-dc-v3-16", "convergence-controlled-s2500"): ("172.30.90", 46900),
        ("kua-dc-v3-32", "convergence-controlled-s2500"): ("172.30.100", 47000),
        ("kua-dc-v3-16", "convergence-zipf-s2500"): ("172.30.110", 47100),
        ("kua-dc-v3-32", "convergence-zipf-s2500"): ("172.30.120", 47200),
        ("kua-dc-v3-16", "convergence-uniform-s2500"): ("172.30.130", 47300),
        ("kua-dc-v3-32", "convergence-uniform-s2500"): ("172.30.140", 47400),
    }
    return table[(topology, workload_network_key(workload_type, spacing_ms))]


def evenly_spaced_ticks(count: int, start: int, end: int) -> List[int]:
    if count == 1:
        return [start]
    ticks = [round(start + index * (end - start) / (count - 1)) for index in range(count)]
    if len(set(ticks)) != len(ticks):
        raise ValueError(f"tick range {start}..{end} cannot hold {count} unique requests")
    return ticks


def spacing_delta_ticks(spacing_ms: int) -> int:
    return max(1, math.ceil(spacing_ms / SPACING_LOOP_SLEEP_MS))


def spaced_ticks(count: int, start: int, delta: int) -> List[int]:
    return [start + index * delta for index in range(count)]


def build_phase_ticks(
    spacing_ms: int | None = None,
    warmup_requests: int = WARMUP_REQUESTS,
    measurement_requests: int = MEASUREMENT_REQUESTS,
) -> Tuple[List[int], List[int], int]:
    if spacing_ms is None:
        if warmup_requests != WARMUP_REQUESTS or measurement_requests != MEASUREMENT_REQUESTS:
            raise ValueError("custom request counts require explicit spacing")
        return (
            evenly_spaced_ticks(WARMUP_REQUESTS, *WARMUP_TICK_RANGE),
            evenly_spaced_ticks(MEASUREMENT_REQUESTS, *MEASUREMENT_TICK_RANGE),
            SCENARIO_TICKS,
        )

    delta = spacing_delta_ticks(spacing_ms)
    warmup_ticks = spaced_ticks(warmup_requests, WARMUP_TICK_RANGE[0], delta)
    measurement_start = warmup_ticks[-1] + 4 * delta
    measurement_ticks = spaced_ticks(measurement_requests, measurement_start, delta)
    scenario_ticks = measurement_ticks[-1] + 4 * delta
    return warmup_ticks, measurement_ticks, scenario_ticks


def scenario_ticks_for_requests(
    workload_type: str,
    requests: Sequence[Dict[str, object]],
    spacing_ms: int | None,
) -> int:
    if workload_type == "single-key":
        return SINGLE_KEY_TICKS
    if spacing_ms is None:
        return SCENARIO_TICKS
    return max(int(request["tick"]) for request in requests) + 4 * spacing_delta_ticks(spacing_ms)


def phase_policy_for_requests(requests: Sequence[Dict[str, object]]) -> str:
    warmup_ticks = [int(request["tick"]) for request in requests if request.get("phase") == "warmup"]
    measurement_ticks = [
        int(request["tick"]) for request in requests if request.get("phase") == "measurement"
    ]
    pieces = []
    if warmup_ticks:
        pieces.append(
            f"warmup={len(warmup_ticks)} requests ticks {min(warmup_ticks)}..{max(warmup_ticks)}"
        )
    if measurement_ticks:
        pieces.append(
            f"measurement={len(measurement_ticks)} requests ticks "
            f"{min(measurement_ticks)}..{max(measurement_ticks)}"
        )
    return "; ".join(pieces)


def request_key_counts(workload_type: str, request_count: int = REQUEST_COUNT) -> Counter[str]:
    population = keys()
    if workload_type.startswith("zipf"):
        weights = [1.0 / (rank**ZIPF_ALPHA) for rank in range(1, KEY_COUNT + 1)]
    elif workload_type == "uniform":
        weights = [1.0 for _ in population]
    else:
        raise ValueError(f"unknown workload type: {workload_type}")

    base = 1
    remaining = request_count - KEY_COUNT
    if remaining < 0:
        raise ValueError("request count must be at least key count")
    total = sum(weights)
    raw_counts = [weight / total * remaining for weight in weights]
    floors = [int(value) for value in raw_counts]
    remainder = remaining - sum(floors)
    order = sorted(
        range(KEY_COUNT),
        key=lambda index: (raw_counts[index] - floors[index], -index),
        reverse=True,
    )
    counts = Counter({key: base + floor for key, floor in zip(population, floors)})
    for index in order[:remainder]:
        counts[population[index]] += 1
    return counts


def introduction_positions(
    count: int,
    warmup_requests: int = WARMUP_REQUESTS,
    measurement_requests: int = MEASUREMENT_REQUESTS,
) -> List[int]:
    if count <= 0:
        return []
    step = measurement_requests // (count + 1)
    return [warmup_requests + (index + 1) * step for index in range(count)]


def request_key_sequence(
    workload_type: str,
    rng: random.Random,
    request_count: int = REQUEST_COUNT,
    warmup_requests: int = WARMUP_REQUESTS,
    measurement_requests: int = MEASUREMENT_REQUESTS,
) -> List[str]:
    remaining = request_key_counts(workload_type, request_count)
    ranks = {key: index + 1 for index, key in enumerate(keys())}
    introduction_keys = keys()
    if workload_type == "uniform":
        rng.shuffle(introduction_keys)

    forced_introductions = {
        position: key
        for position, key in zip(range(warmup_requests), introduction_keys[:warmup_requests])
    }
    forced_introductions.update(
        {
            position: key
            for position, key in zip(
                introduction_positions(KEY_COUNT - warmup_requests, warmup_requests, measurement_requests),
                introduction_keys[warmup_requests:],
            )
        }
    )

    introduced = set()
    last_seen = {key: -request_count for key in keys()}
    sequence: List[str] = []
    for index in range(request_count):
        if index in forced_introductions:
            key = forced_introductions[index]
        else:
            eligible = [
                key
                for key in keys()
                if key in introduced
                and remaining[key] > 0
                and index - last_seen[key] > KEY_INDEX_COOLDOWN
            ]
            if not eligible:
                eligible = [key for key in keys() if key in introduced and remaining[key] > 0]
            if not eligible:
                raise ValueError("no introduced key is available for repeat scheduling")
            max_remaining = max(remaining[key] for key in eligible)
            candidates = [key for key in eligible if remaining[key] == max_remaining]
            if workload_type.startswith("zipf"):
                candidates.sort(key=lambda key: (ranks[key], key))
            else:
                rng.shuffle(candidates)
            key = candidates[0]
        sequence.append(key)
        introduced.add(key)
        remaining[key] -= 1
        last_seen[key] = index

    if sum(remaining.values()) != 0:
        raise ValueError("internal key sequencing error")
    return sequence


def key_provider_map(topology: str) -> Dict[str, int]:
    provider_nodes = providers(topology)
    return {key: provider_nodes[index % len(provider_nodes)] for index, key in enumerate(keys())}


def choose_requester(
    requester_nodes: Sequence[int],
    provider_node: int,
    key: str,
    per_key_requesters: Dict[str, set[int]],
    request_index: int,
    rng: random.Random,
) -> int:
    candidates = [node for node in requester_nodes if node != provider_node]
    unused = [node for node in candidates if node not in per_key_requesters.get(key, set())]
    if unused:
        candidates = unused
    start = rng.randrange(len(candidates))
    requester = candidates[(start + request_index) % len(candidates)]
    per_key_requesters.setdefault(key, set()).add(requester)
    return requester


def build_requests(
    topology: str,
    workload_type: str,
    seed: int,
    spacing_ms: int | None = None,
    request_count: int = REQUEST_COUNT,
    warmup_requests: int = WARMUP_REQUESTS,
    measurement_requests: int = MEASUREMENT_REQUESTS,
) -> List[Dict[str, object]]:
    rng = random.Random(f"{topology}:{workload_type}:{seed}")
    key_to_provider = key_provider_map(topology)
    requester_nodes = requesters(topology)
    warmup_ticks, measurement_ticks, _scenario_ticks = build_phase_ticks(
        spacing_ms,
        warmup_requests,
        measurement_requests,
    )
    chosen_keys = request_key_sequence(
        workload_type,
        rng,
        request_count,
        warmup_requests,
        measurement_requests,
    )
    phase_ticks = [("warmup", tick) for tick in warmup_ticks] + [
        ("measurement", tick) for tick in measurement_ticks
    ]
    seen_keys = set()
    per_key_requesters: Dict[str, set[int]] = {}
    requests: List[Dict[str, object]] = []
    for index, ((phase, tick), key) in enumerate(zip(phase_ticks, chosen_keys), start=1):
        provider_node = key_to_provider[key]
        requester_node = choose_requester(
            requester_nodes,
            provider_node,
            key,
            per_key_requesters,
            index,
            rng,
        )
        is_first = key not in seen_keys
        seen_keys.add(key)
        requests.append(
            {
                "request_index": index,
                "phase": phase,
                "tick": tick,
                "node": requester_node,
                "key": key,
                "provider": provider_node,
                "is_first_key_request": is_first,
            }
        )
    return requests


def rack_index_for_node(topology: str, node: int) -> int:
    for index, rack in enumerate(racks(topology)):
        if node in rack:
            return index
    raise ValueError(f"node {node} is not in {topology}")


def paired_requester_pair(topology: str, provider_node: int, key_index: int) -> Tuple[int, int]:
    rack_list = racks(topology)
    provider_rack = rack_index_for_node(topology, provider_node)
    consumer_rack = rack_list[(provider_rack + 1) % len(rack_list)]
    candidates = [node for node in consumer_rack[1:] if node != provider_node]
    if len(candidates) < 2:
        candidates = [node for node in consumer_rack if node != provider_node]
    if len(candidates) < 2:
        raise ValueError("paired workload needs at least two requester hosts")
    cold = candidates[key_index % len(candidates)]
    warm = candidates[(key_index + 1) % len(candidates)]
    return cold, warm


def build_paired_requests(topology: str, seed: int, spacing_ms: int) -> List[Dict[str, object]]:
    rng = random.Random(f"{topology}:zipf-paired:{seed}")
    warmup_ticks, measurement_ticks, _scenario_ticks = build_phase_ticks(
        spacing_ms,
        PAIRED_WARMUP_REQUESTS,
        PAIRED_MEASUREMENT_REQUESTS,
    )
    key_to_provider = key_provider_map(topology)
    measured_keys = keys()[:PAIRED_MEASUREMENT_PAIRS]
    background_keys = keys()[PAIRED_MEASUREMENT_PAIRS:]
    if not background_keys:
        raise ValueError("paired workload needs background keys outside measured keys")

    requests: List[Dict[str, object]] = []
    background_sequence = list(background_keys)
    while len(background_sequence) < PAIRED_WARMUP_REQUESTS:
        shuffled = list(background_keys)
        rng.shuffle(shuffled)
        background_sequence.extend(shuffled)
    background_sequence = background_sequence[:PAIRED_WARMUP_REQUESTS]

    for index, (tick, key) in enumerate(zip(warmup_ticks, background_sequence), start=1):
        provider_node = key_to_provider[key]
        cold, warm = paired_requester_pair(topology, provider_node, index)
        requester_node = cold if index % 2 else warm
        requests.append(
            {
                "request_index": index,
                "phase": "warmup",
                "tick": tick,
                "node": requester_node,
                "key": key,
                "provider": provider_node,
                "is_first_key_request": key not in {str(request["key"]) for request in requests},
            }
        )

    measurement_index = 0
    for key_index, key in enumerate(measured_keys):
        provider_node = key_to_provider[key]
        cold, warm = paired_requester_pair(topology, provider_node, key_index)
        for requester_node, is_first in ((cold, True), (warm, False)):
            tick = measurement_ticks[measurement_index]
            requests.append(
                {
                    "request_index": len(requests) + 1,
                    "phase": "measurement",
                    "tick": tick,
                    "node": requester_node,
                    "key": key,
                    "provider": provider_node,
                    "is_first_key_request": is_first,
                }
            )
            measurement_index += 1
    return requests


def build_pairblock_requests(
    topology: str,
    seed: int,
    spacing_ms: int,
    inter_block_idle_ms: int = PAIRBLOCK_INTER_BLOCK_IDLE_MS,
) -> List[Dict[str, object]]:
    rng = random.Random(f"{topology}:zipf-pairblock:{seed}")
    delta = spacing_delta_ticks(spacing_ms)
    inter_block_delta = spacing_delta_ticks(inter_block_idle_ms)
    key_to_provider = key_provider_map(topology)
    measured_keys = keys()[:PAIRBLOCK_MEASUREMENT_PAIRS]
    background_keys = keys()[PAIRBLOCK_MEASUREMENT_PAIRS:]
    if len(background_keys) < PAIRBLOCK_WARMUP_REQUESTS:
        raise ValueError("pairblock workload needs enough background warm-up keys")

    requests: List[Dict[str, object]] = []
    warmup_keys = list(background_keys)
    rng.shuffle(warmup_keys)
    warmup_ticks = spaced_ticks(PAIRBLOCK_WARMUP_REQUESTS, WARMUP_TICK_RANGE[0], delta)
    for index, (tick, key) in enumerate(
        zip(warmup_ticks, warmup_keys[:PAIRBLOCK_WARMUP_REQUESTS]), start=1
    ):
        provider_node = key_to_provider[key]
        cold, warm = paired_requester_pair(topology, provider_node, index)
        requester_node = cold if index % 2 else warm
        requests.append(
            {
                "request_index": index,
                "phase": "warmup",
                "tick": tick,
                "node": requester_node,
                "key": key,
                "provider": provider_node,
                "is_first_key_request": True,
            }
        )

    cold_start = warmup_ticks[-1] + 4 * delta
    cold_ticks = spaced_ticks(PAIRBLOCK_MEASUREMENT_PAIRS, cold_start, delta)
    warm_start = cold_ticks[-1] + inter_block_delta
    warm_ticks = spaced_ticks(PAIRBLOCK_MEASUREMENT_PAIRS, warm_start, delta)
    for key_index, (tick, key) in enumerate(zip(cold_ticks, measured_keys)):
        provider_node = key_to_provider[key]
        cold, _warm = paired_requester_pair(topology, provider_node, key_index)
        requests.append(
            {
                "request_index": len(requests) + 1,
                "phase": "measurement",
                "tick": tick,
                "node": cold,
                "key": key,
                "provider": provider_node,
                "is_first_key_request": True,
            }
        )
    for key_index, (tick, key) in enumerate(zip(warm_ticks, measured_keys)):
        provider_node = key_to_provider[key]
        _cold, warm = paired_requester_pair(topology, provider_node, key_index)
        requests.append(
            {
                "request_index": len(requests) + 1,
                "phase": "measurement",
                "tick": tick,
                "node": warm,
                "key": key,
                "provider": provider_node,
                "is_first_key_request": False,
            }
        )
    return requests


def distanceblock_cold_hops(topology: str) -> List[int]:
    if topology == "kua-dc-v3-16":
        return [2, 3, 4] * 8
    if topology == "kua-dc-v3-32":
        return [2, 3, 4, 5] * 5 + [6] * 4
    raise ValueError(f"unknown topology: {topology}")


def non_gateway_requesters_for_rack(rack: Sequence[int], provider_node: int) -> List[int]:
    return [node for node in rack[1:] if node != provider_node]


def cold_requester_for_hop(
    topology: str,
    provider_node: int,
    target_hop: int,
    pair_index: int,
) -> int:
    rack_list = racks(topology)
    provider_rack = rack_index_for_node(topology, provider_node)
    rack_offset = target_hop - 2
    if rack_offset < 0 or rack_offset > len(rack_list) // 2:
        raise ValueError(f"target hop {target_hop} is not supported for {topology}")
    requester_rack = rack_list[(provider_rack + rack_offset) % len(rack_list)]
    candidates = non_gateway_requesters_for_rack(requester_rack, provider_node)
    if not candidates:
        raise ValueError(f"no cold requester candidates for hop {target_hop}")
    return candidates[pair_index % len(candidates)]


def warm_requester_for_path(
    topology: str,
    provider_node: int,
    cold_requester: int,
    cached_path: Sequence[int],
    cold_hop: int,
    pair_index: int,
) -> Tuple[int, int]:
    graph = overlay_graph(topology)
    candidates = [node for node in hosts(topology) if node not in {provider_node, cold_requester}]
    scored = [
        (node, distance_to_cached_path(graph, node, cached_path))
        for node in candidates
    ]
    eligible = [(node, distance) for node, distance in scored if 0 < distance < cold_hop]
    if not eligible:
        eligible = [(node, distance) for node, distance in scored if distance < cold_hop]
    if not eligible:
        raise ValueError(f"no warm requester candidates below cold hop {cold_hop}")
    by_distance: Dict[int, List[int]] = {}
    for node, distance in eligible:
        by_distance.setdefault(distance, []).append(node)
    distances = sorted(by_distance)
    selected_distance = distances[pair_index % len(distances)]
    nodes = sorted(by_distance[selected_distance])
    return nodes[(pair_index // len(distances)) % len(nodes)], selected_distance


def build_distanceblock_requests(
    topology: str,
    seed: int,
    spacing_ms: int,
    inter_block_idle_ms: int = DISTANCEBLOCK_INTER_BLOCK_IDLE_MS,
) -> List[Dict[str, object]]:
    rng = random.Random(f"{topology}:zipf-distanceblock:{seed}")
    delta = spacing_delta_ticks(spacing_ms)
    inter_block_delta = spacing_delta_ticks(inter_block_idle_ms)
    graph = overlay_graph(topology)
    key_to_provider = key_provider_map(topology)
    measured_keys = keys()[:DISTANCEBLOCK_MEASUREMENT_PAIRS]
    background_keys = keys()[DISTANCEBLOCK_MEASUREMENT_PAIRS:]
    if len(background_keys) < DISTANCEBLOCK_WARMUP_REQUESTS:
        raise ValueError("distanceblock workload needs enough background warm-up keys")

    requests: List[Dict[str, object]] = []
    warmup_keys = list(background_keys)
    rng.shuffle(warmup_keys)
    warmup_ticks = spaced_ticks(DISTANCEBLOCK_WARMUP_REQUESTS, WARMUP_TICK_RANGE[0], delta)
    for index, (tick, key) in enumerate(
        zip(warmup_ticks, warmup_keys[:DISTANCEBLOCK_WARMUP_REQUESTS]), start=1
    ):
        provider_node = key_to_provider[key]
        requester_candidates = [node for node in hosts(topology) if node != provider_node]
        requester_node = requester_candidates[index % len(requester_candidates)]
        requests.append(
            {
                "request_index": index,
                "phase": "warmup",
                "tick": tick,
                "node": requester_node,
                "key": key,
                "provider": provider_node,
                "is_first_key_request": True,
                "request_pair_id": "",
                "distance_class": "warmup",
                "expected_cold_provider_hop": "",
                "expected_warm_cache_hop": "",
            }
        )

    cold_hops = distanceblock_cold_hops(topology)
    if len(cold_hops) != DISTANCEBLOCK_MEASUREMENT_PAIRS:
        raise ValueError("distanceblock hop plan length mismatch")
    cold_start = warmup_ticks[-1] + 4 * delta
    cold_ticks = spaced_ticks(DISTANCEBLOCK_MEASUREMENT_PAIRS, cold_start, delta)
    warm_start = cold_ticks[-1] + inter_block_delta
    warm_ticks = spaced_ticks(DISTANCEBLOCK_MEASUREMENT_PAIRS, warm_start, delta)
    pair_specs: List[Dict[str, object]] = []

    for key_index, (tick, key, target_hop) in enumerate(zip(cold_ticks, measured_keys, cold_hops)):
        provider_node = key_to_provider[key]
        cold_requester = cold_requester_for_hop(topology, provider_node, target_hop, key_index)
        cold_path = shortest_path(graph, cold_requester, provider_node)
        cold_hop = len(cold_path) - 1
        if cold_hop != target_hop:
            raise ValueError(f"expected cold hop {target_hop}, got {cold_hop}")
        warm_requester, expected_warm_hop = warm_requester_for_path(
            topology,
            provider_node,
            cold_requester,
            cold_path,
            cold_hop,
            key_index,
        )
        pair_id = f"pair_{key_index + 1:03d}"
        distance_class = f"cold_hop_{cold_hop}"
        pair_specs.append(
            {
                "key": key,
                "provider": provider_node,
                "cold_requester": cold_requester,
                "warm_requester": warm_requester,
                "cold_hop": cold_hop,
                "warm_hop": expected_warm_hop,
                "distance_class": distance_class,
                "pair_id": pair_id,
            }
        )
        requests.append(
            {
                "request_index": len(requests) + 1,
                "phase": "measurement",
                "tick": tick,
                "node": cold_requester,
                "key": key,
                "provider": provider_node,
                "is_first_key_request": True,
                "request_pair_id": pair_id,
                "distance_class": distance_class,
                "expected_cold_provider_hop": str(cold_hop),
                "expected_warm_cache_hop": str(expected_warm_hop),
            }
        )

    for tick, spec in zip(warm_ticks, pair_specs):
        requests.append(
            {
                "request_index": len(requests) + 1,
                "phase": "measurement",
                "tick": tick,
                "node": spec["warm_requester"],
                "key": spec["key"],
                "provider": spec["provider"],
                "is_first_key_request": False,
                "request_pair_id": spec["pair_id"],
                "distance_class": spec["distance_class"],
                "expected_cold_provider_hop": str(spec["cold_hop"]),
                "expected_warm_cache_hop": str(spec["warm_hop"]),
            }
        )
    return requests


def convergence_cold_hops(topology: str) -> List[int]:
    if topology == "kua-dc-v3-16":
        return [2, 3, 4] * 5
    if topology == "kua-dc-v3-32":
        return [2, 3, 4, 5, 6] * 3
    raise ValueError(f"unknown topology: {topology}")


def measurement_fields(
    *,
    measurement_position: int,
    measurement_start_tick: int,
    tick: int,
    round_index: int,
    key_occurrence_index: int,
    popularity_rank: int,
) -> Dict[str, object]:
    return {
        "measurement_position": measurement_position,
        "round_index": round_index,
        "key_occurrence_index": key_occurrence_index,
        "popularity_rank": popularity_rank,
        "measurement_elapsed_ms": (tick - measurement_start_tick) * SPACING_LOOP_SLEEP_MS,
        "request_bin": ((measurement_position - 1) // CONVERGENCE_BIN_SIZE) + 1,
        "workload_family": "latency_convergence",
    }


def blank_convergence_fields() -> Dict[str, object]:
    return {
        "measurement_position": "",
        "round_index": "",
        "key_occurrence_index": "",
        "popularity_rank": "",
        "measurement_elapsed_ms": "",
        "request_bin": "",
        "workload_family": "latency_convergence",
    }


def build_convergence_controlled_requests(
    topology: str,
    seed: int,
    spacing_ms: int,
) -> List[Dict[str, object]]:
    rng = random.Random(f"{topology}:convergence-controlled:{seed}")
    delta = spacing_delta_ticks(spacing_ms)
    graph = overlay_graph(topology)
    key_to_provider = key_provider_map(topology)
    measured_keys = keys()[:CONVERGENCE_CONTROLLED_KEYS]
    background_keys = keys()[CONVERGENCE_CONTROLLED_KEYS:]
    if len(background_keys) < CONVERGENCE_WARMUP_REQUESTS:
        raise ValueError("convergence-controlled needs enough background warm-up keys")

    requests: List[Dict[str, object]] = []
    warmup_keys = list(background_keys)
    rng.shuffle(warmup_keys)
    warmup_ticks = spaced_ticks(CONVERGENCE_WARMUP_REQUESTS, WARMUP_TICK_RANGE[0], delta)
    for index, (tick, key) in enumerate(
        zip(warmup_ticks, warmup_keys[:CONVERGENCE_WARMUP_REQUESTS]), start=1
    ):
        provider_node = key_to_provider[key]
        requester_candidates = [node for node in hosts(topology) if node != provider_node]
        requester_node = requester_candidates[index % len(requester_candidates)]
        requests.append(
            {
                "request_index": index,
                "phase": "warmup",
                "tick": tick,
                "node": requester_node,
                "key": key,
                "provider": provider_node,
                "is_first_key_request": True,
                "request_pair_id": "",
                "distance_class": "warmup",
                "expected_cold_provider_hop": "",
                "expected_warm_cache_hop": "",
                **blank_convergence_fields(),
            }
        )

    cold_hops = convergence_cold_hops(topology)
    if len(cold_hops) != CONVERGENCE_CONTROLLED_KEYS:
        raise ValueError("convergence controlled hop plan length mismatch")
    measurement_start = warmup_ticks[-1] + 4 * delta
    measurement_ticks = spaced_ticks(
        CONVERGENCE_MEASUREMENT_REQUESTS,
        measurement_start,
        delta,
    )
    pair_specs: List[Dict[str, object]] = []
    for key_index, (key, target_hop) in enumerate(zip(measured_keys, cold_hops)):
        provider_node = key_to_provider[key]
        cold_requester = cold_requester_for_hop(topology, provider_node, target_hop, key_index)
        cold_path = shortest_path(graph, cold_requester, provider_node)
        cold_hop = len(cold_path) - 1
        if cold_hop != target_hop:
            raise ValueError(f"expected cold hop {target_hop}, got {cold_hop}")
        repeated_candidates = [
            (
                node,
                distance_to_cached_path(graph, node, cold_path),
            )
            for node in hosts(topology)
            if node not in {provider_node, cold_requester}
        ]
        repeated_candidates = [
            (node, distance)
            for node, distance in repeated_candidates
            if distance < cold_hop
        ]
        if not repeated_candidates:
            raise ValueError(f"no repeated requester candidates below cold hop {cold_hop}")
        repeated_candidates.sort(key=lambda item: (item[1], item[0]))
        pair_specs.append(
            {
                "key": key,
                "provider": provider_node,
                "cold_requester": cold_requester,
                "cold_path": cold_path,
                "cold_hop": cold_hop,
                "distance_class": f"cold_hop_{cold_hop}",
                "pair_id": f"tracked_{key_index + 1:03d}",
                "repeated_candidates": repeated_candidates,
                "popularity_rank": key_index + 1,
            }
        )

    measurement_position = 0
    for round_index in range(1, CONVERGENCE_CONTROLLED_ROUNDS + 1):
        for key_index, spec in enumerate(pair_specs):
            measurement_position += 1
            tick = measurement_ticks[measurement_position - 1]
            if round_index == 1:
                requester_node = int(spec["cold_requester"])
                expected_warm_cache_hop = ""
                is_first = True
            else:
                repeated_candidates = spec["repeated_candidates"]
                candidate_index = (
                    key_index + round_index - 2
                ) % len(repeated_candidates)
                requester_node, expected_warm_cache_hop = repeated_candidates[candidate_index]
                is_first = False
            requests.append(
                {
                    "request_index": len(requests) + 1,
                    "phase": "measurement",
                    "tick": tick,
                    "node": requester_node,
                    "key": spec["key"],
                    "provider": spec["provider"],
                    "is_first_key_request": is_first,
                    "request_pair_id": spec["pair_id"],
                    "distance_class": spec["distance_class"],
                    "expected_cold_provider_hop": str(spec["cold_hop"]),
                    "expected_warm_cache_hop": str(expected_warm_cache_hop),
                    **measurement_fields(
                        measurement_position=measurement_position,
                        measurement_start_tick=measurement_start,
                        tick=tick,
                        round_index=round_index,
                        key_occurrence_index=round_index,
                        popularity_rank=int(spec["popularity_rank"]),
                    ),
                }
            )
    return requests


def build_convergence_distribution_requests(
    topology: str,
    distribution: str,
    seed: int,
    spacing_ms: int,
    warmup_requests: int = CONVERGENCE_WARMUP_REQUESTS,
    measurement_requests: int = CONVERGENCE_MEASUREMENT_REQUESTS,
) -> List[Dict[str, object]]:
    if distribution not in {"zipf", "uniform"}:
        raise ValueError(f"unsupported convergence distribution: {distribution}")
    rng = random.Random(f"{topology}:convergence-{distribution}:{seed}")
    key_to_provider = key_provider_map(topology)
    graph = overlay_graph(topology)
    requester_nodes = hosts(topology)
    warmup_ticks, measurement_ticks, _scenario_ticks = build_phase_ticks(
        spacing_ms,
        warmup_requests,
        measurement_requests,
    )
    chosen_keys = request_key_sequence(
        distribution,
        rng,
        warmup_requests + measurement_requests,
        warmup_requests,
        measurement_requests,
    )
    phase_ticks = [("warmup", tick) for tick in warmup_ticks] + [
        ("measurement", tick) for tick in measurement_ticks
    ]
    ranks = {key: index + 1 for index, key in enumerate(keys())}
    seen_keys: set[str] = set()
    per_key_requesters: Dict[str, set[int]] = {}
    per_key_occurrences: Counter[str] = Counter()
    cached_nodes: Dict[str, set[int]] = {}
    requests: List[Dict[str, object]] = []
    measurement_position = 0
    measurement_start = measurement_ticks[0]
    for index, ((phase, tick), key) in enumerate(zip(phase_ticks, chosen_keys), start=1):
        provider_node = key_to_provider[key]
        requester_node = choose_requester(
            requester_nodes,
            provider_node,
            key,
            per_key_requesters,
            index,
            rng,
        )
        provider_hop = shortest_distance(graph, requester_node, provider_node)
        is_first = key not in seen_keys
        seen_keys.add(key)
        per_key_occurrences[key] += 1
        expected_warm_cache_hop = ""
        if key in cached_nodes and cached_nodes[key]:
            expected_warm_cache_hop = str(distance_to_cached_path(graph, requester_node, sorted(cached_nodes[key])))
        path = shortest_path(graph, requester_node, provider_node)
        cached_nodes.setdefault(key, set()).update(path)
        row = {
            "request_index": index,
            "phase": phase,
            "tick": tick,
            "node": requester_node,
            "key": key,
            "provider": provider_node,
            "is_first_key_request": is_first,
            "request_pair_id": key,
            "distance_class": f"cold_hop_{provider_hop}" if phase == "measurement" else "warmup",
            "expected_cold_provider_hop": str(provider_hop) if phase == "measurement" else "",
            "expected_warm_cache_hop": expected_warm_cache_hop if phase == "measurement" else "",
        }
        if phase == "measurement":
            measurement_position += 1
            row.update(
                measurement_fields(
                    measurement_position=measurement_position,
                    measurement_start_tick=measurement_start,
                    tick=tick,
                    round_index=per_key_occurrences[key],
                    key_occurrence_index=per_key_occurrences[key],
                    popularity_rank=ranks[key],
                )
            )
        else:
            row.update(blank_convergence_fields())
        requests.append(row)
    return requests


def build_convergence_requests(
    topology: str,
    workload_type: str,
    seed: int,
    spacing_ms: int,
) -> List[Dict[str, object]]:
    if workload_type == "convergence-controlled":
        return build_convergence_controlled_requests(topology, seed, spacing_ms)
    if workload_type == "convergence-zipf":
        return build_convergence_distribution_requests(topology, "zipf", seed, spacing_ms)
    if workload_type == "convergence-zipf-long":
        return build_convergence_distribution_requests(
            topology,
            "zipf",
            seed,
            spacing_ms,
            CONVERGENCE_WARMUP_REQUESTS,
            CONVERGENCE_ZIPF_LONG_MEASUREMENT_REQUESTS,
        )
    if workload_type == "convergence-uniform":
        return build_convergence_distribution_requests(topology, "uniform", seed, spacing_ms)
    raise ValueError(f"unknown convergence workload: {workload_type}")


def data_entries(topology: str) -> List[Tuple[int, str, str]]:
    provider_map = key_provider_map(topology)
    return [(provider_map[key], key, f"VALUE_{key[-3:]}") for key in keys()]


def validate_generated(
    topology: str,
    workload_type: str,
    requests: Sequence[Dict[str, object]],
    request_count: int = REQUEST_COUNT,
    warmup_requests: int = WARMUP_REQUESTS,
    measurement_requests: int = MEASUREMENT_REQUESTS,
) -> None:
    if len(requests) != request_count:
        raise ValueError("request count mismatch")
    counts = Counter(str(request["phase"]) for request in requests)
    if counts["warmup"] != warmup_requests or counts["measurement"] != measurement_requests:
        raise ValueError(f"phase count mismatch: {counts}")
    if len({int(request["tick"]) for request in requests}) != request_count:
        raise ValueError("request ticks are not unique")

    provider_nodes = set(providers(topology))
    requester_nodes = set(
        hosts(topology)
        if workload_type in {"zipf-paired", "zipf-pairblock", "zipf-distanceblock"}
        or workload_type.startswith("convergence-")
        else requesters(topology)
    )
    for request in requests:
        if int(request["provider"]) not in provider_nodes:
            raise ValueError("request uses non-provider host")
        if int(request["node"]) not in requester_nodes:
            raise ValueError("request uses non-requester host")
        if int(request["node"]) == int(request["provider"]):
            raise ValueError("requester is local provider for requested key")

    measurement = [request for request in requests if request["phase"] == "measurement"]
    warm_types = Counter(bool(request["is_first_key_request"]) for request in measurement)
    if not warm_types[True] or not warm_types[False]:
        raise ValueError("measurement phase must include first and repeated key requests")

    key_counts = Counter(str(request["key"]) for request in requests)
    ordered_counts = sorted(key_counts.values(), reverse=True)
    if workload_type in {"zipf-paired", "zipf-pairblock", "zipf-distanceblock"}:
        measurement_by_key: Dict[str, List[Dict[str, object]]] = {}
        for request in measurement:
            measurement_by_key.setdefault(str(request["key"]), []).append(request)
        if workload_type == "zipf-paired":
            expected_pairs = PAIRED_MEASUREMENT_PAIRS
        elif workload_type == "zipf-pairblock":
            expected_pairs = PAIRBLOCK_MEASUREMENT_PAIRS
        else:
            expected_pairs = DISTANCEBLOCK_MEASUREMENT_PAIRS
        if len(measurement_by_key) != expected_pairs:
            raise ValueError(f"{workload_type} measurement key count mismatch")
        for key, key_requests in measurement_by_key.items():
            if len(key_requests) != 2:
                raise ValueError(f"{workload_type} key {key} is not requested exactly twice")
            if key_requests[0]["is_first_key_request"] is not True:
                raise ValueError(f"{workload_type} key {key} first request is not cold")
            if key_requests[1]["is_first_key_request"] is not False:
                raise ValueError(f"{workload_type} key {key} second request is not warm")
            if key_requests[0]["node"] == key_requests[1]["node"]:
                raise ValueError(f"{workload_type} key {key} uses the same requester twice")
        if workload_type == "zipf-pairblock":
            midpoint = len(measurement) // 2
            if not all(bool(request["is_first_key_request"]) for request in measurement[:midpoint]):
                raise ValueError("pairblock cold block contains a repeated key request")
            if not all(not bool(request["is_first_key_request"]) for request in measurement[midpoint:]):
                raise ValueError("pairblock warm block contains a first key request")
        if workload_type == "zipf-distanceblock":
            midpoint = len(measurement) // 2
            if not all(bool(request["is_first_key_request"]) for request in measurement[:midpoint]):
                raise ValueError("distanceblock cold block contains a repeated key request")
            if not all(not bool(request["is_first_key_request"]) for request in measurement[midpoint:]):
                raise ValueError("distanceblock warm block contains a first key request")
            cold_hops = {
                int(request["expected_cold_provider_hop"])
                for request in measurement[:midpoint]
                if str(request.get("expected_cold_provider_hop", "")).strip()
            }
            warm_hops = {
                int(request["expected_warm_cache_hop"])
                for request in measurement[midpoint:]
                if str(request.get("expected_warm_cache_hop", "")).strip()
            }
            min_cold_hops = 3 if topology == "kua-dc-v3-16" else 4
            if len(cold_hops) < min_cold_hops:
                raise ValueError(f"distanceblock cold hop distribution too narrow: {cold_hops}")
            if len(warm_hops) < 2:
                raise ValueError(f"distanceblock warm hop distribution too narrow: {warm_hops}")
            if sum(warm_hops) / len(warm_hops) >= sum(cold_hops) / len(cold_hops):
                raise ValueError("distanceblock warm expected hops are not lower than cold hops")
    elif workload_type.startswith("convergence-"):
        for request in measurement:
            for field in [
                "round_index",
                "key_occurrence_index",
                "popularity_rank",
                "measurement_elapsed_ms",
                "request_bin",
                "workload_family",
            ]:
                if field not in request:
                    raise ValueError(f"{workload_type} request is missing {field}")
        if any(str(request.get("workload_family", "")) != "latency_convergence" for request in measurement):
            raise ValueError(f"{workload_type} has invalid workload_family metadata")
        if workload_type == "convergence-controlled":
            measurement_by_key: Dict[str, List[Dict[str, object]]] = {}
            for request in measurement:
                measurement_by_key.setdefault(str(request["key"]), []).append(request)
            if len(measurement_by_key) != CONVERGENCE_CONTROLLED_KEYS:
                raise ValueError("convergence-controlled tracked key count mismatch")
            for key, key_requests in measurement_by_key.items():
                if len(key_requests) != CONVERGENCE_CONTROLLED_ROUNDS:
                    raise ValueError(f"convergence-controlled key {key} does not have 8 rounds")
                key_requests.sort(key=lambda request: int(request["round_index"]))
                if key_requests[0]["is_first_key_request"] is not True:
                    raise ValueError(f"convergence-controlled key {key} first round is not cold")
                if any(bool(request["is_first_key_request"]) for request in key_requests[1:]):
                    raise ValueError(f"convergence-controlled key {key} repeats are not warm")
                if len({int(request["node"]) for request in key_requests[1:]}) < 2:
                    raise ValueError(f"convergence-controlled key {key} repeats do not rotate requesters")
            cold_hops = {
                int(request["expected_cold_provider_hop"])
                for request in measurement
                if int(request["round_index"]) == 1
            }
            min_cold_hops = 3 if topology == "kua-dc-v3-16" else 5
            if len(cold_hops) < min_cold_hops:
                raise ValueError(f"convergence-controlled cold hop distribution too narrow: {cold_hops}")
    elif workload_type.startswith("zipf"):
        top_n = max(1, KEY_COUNT // 10)
        top_mean = sum(ordered_counts[:top_n]) / top_n
        tail_values = ordered_counts[-top_n:]
        tail_mean = sum(tail_values) / len(tail_values)
        if top_mean <= tail_mean * 2:
            raise ValueError("Zipf distribution is not skewed enough")
    elif max(ordered_counts) - min(ordered_counts) > 8:
        raise ValueError("Uniform distribution is unexpectedly skewed")


def scenario_name(topology: str, workload_type: str, spacing_ms: int | None = None) -> str:
    if workload_type == "single-key":
        return f"{topology}-warm"
    if spacing_ms is not None:
        return f"{topology}-{workload_type}-s{spacing_ms}-seed{SEED:03d}"
    return f"{topology}-{workload_type}-seed{SEED:03d}"


def metadata(
    topology: str,
    workload_type: str,
    requests: Sequence[Dict[str, object]],
    spacing_ms: int | None = None,
    request_count: int = REQUEST_COUNT,
    warmup_requests: int = WARMUP_REQUESTS,
    measurement_requests: int = MEASUREMENT_REQUESTS,
) -> Dict[str, object]:
    leaf_count = host_count(topology) // 4
    request_count = len(requests)
    warmup = sum(1 for request in requests if request.get("phase") == "warmup")
    measurement = sum(1 for request in requests if request.get("phase") == "measurement")
    scenario_ticks = scenario_ticks_for_requests(workload_type, requests, spacing_ms)
    payload = {
        "workload_id": f"kua-dc-v3-{workload_type}" if spacing_ms is None else f"kua-dc-v3-{workload_type}-s{spacing_ms}",
        "workload_type": workload_type,
        "scenario": scenario_name(topology, workload_type, spacing_ms),
        "topology": topology,
        "seed": SEED,
        "key_count": KEY_COUNT if workload_type != "single-key" else 1,
        "request_count": request_count,
        "warmup_requests": warmup,
        "measurement_requests": measurement,
        "provider_count": len(providers(topology)) if workload_type != "single-key" else 1,
        "requester_count": (
            len({int(request["node"]) for request in requests})
            if workload_type == "zipf-paired" or workload_type.startswith("convergence-")
            else len(requesters(topology))
            if workload_type != "single-key"
            else 2
        ),
        "zipf_alpha": ZIPF_ALPHA if workload_type.startswith("zipf") or "convergence-zipf" in workload_type else None,
        "host_count": host_count(topology),
        "leaf_count": leaf_count,
        "spine_count": 4,
        "physical_links": host_count(topology) + leaf_count * 4,
        "physical_model": "host-only rsync with pure L3 leaf/spine forwarding",
        "protocol_overlay": "one primary gateway host per rack; gateways form a bounded-degree ring",
        "phase_policy": (
            phase_policy_for_requests(requests)
            if workload_type != "single-key"
            else "single-key cold/warm requests at ticks 220 and 900"
        ),
        "providers": providers(topology),
        "requesters": (
            sorted({int(request["node"]) for request in requests})
            if workload_type == "zipf-paired" or workload_type.startswith("convergence-")
            else requesters(topology)
        ),
        "gateways": gateways(topology),
        "keys": [
            {"key": key, "provider": provider, "value": value}
            for provider, key, value in (
                data_entries(topology)
                if workload_type != "single-key"
                else [(host_count(topology), "redis_key_a", "VALUE_OF_A")]
            )
        ],
        "requests": list(requests),
    }
    if spacing_ms is not None:
        payload.update(
            {
                "tick_policy": f"spacing_s{spacing_ms}",
                "measurement_spacing_ms": spacing_ms,
                "spacing_loop_sleep_ms": SPACING_LOOP_SLEEP_MS,
                "spacing_delta_ticks": spacing_delta_ticks(spacing_ms),
                "scenario_ticks": scenario_ticks,
                "query_table_timeout_ms": "500_assumed",
            }
        )
    if workload_type != "single-key" and request_count != REQUEST_COUNT:
        profile_prefix = "long" if workload_type == "convergence-zipf-long" else "light"
        payload["request_count_profile"] = f"{profile_prefix}_{request_count}"
    if workload_type == "zipf-paired":
        payload["request_pairing_policy"] = (
            "measurement keys use exactly one cold requester and one warm requester "
            "from the same adjacent consumer rack"
        )
    if workload_type == "zipf-pairblock":
        payload.update(
            {
                "measurement_pair_count": PAIRBLOCK_MEASUREMENT_PAIRS,
                "measurement_block_policy": "cold_block_then_warm_block",
                "inter_block_idle_ms": PAIRBLOCK_INTER_BLOCK_IDLE_MS,
                "request_pairing_policy": (
                    "measurement keys use one cold requester in the cold block and "
                    "one different warm requester in the warm block from the same "
                    "adjacent consumer rack"
                ),
            }
        )
    if workload_type == "zipf-distanceblock":
        measurement_rows = [request for request in requests if request.get("phase") == "measurement"]
        cold_rows = [request for request in measurement_rows if request.get("is_first_key_request")]
        warm_rows = [request for request in measurement_rows if not request.get("is_first_key_request")]
        payload.update(
            {
                "measurement_pair_count": DISTANCEBLOCK_MEASUREMENT_PAIRS,
                "measurement_block_policy": "distance_diverse_cold_block_then_warm_block",
                "inter_block_idle_ms": DISTANCEBLOCK_INTER_BLOCK_IDLE_MS,
                "distance_class_policy": (
                    "distance_class is keyed by expected cold provider hop; warm "
                    "requesters are selected by distance to the cold return path cache"
                ),
                "cold_expected_provider_hop_counts": dict(
                    Counter(str(request.get("expected_cold_provider_hop", "")) for request in cold_rows)
                ),
                "warm_expected_cache_hop_counts": dict(
                    Counter(str(request.get("expected_warm_cache_hop", "")) for request in warm_rows)
                ),
            }
        )
    if workload_type.startswith("convergence-"):
        measurement_rows = [request for request in requests if request.get("phase") == "measurement"]
        payload.update(
            {
                "workload_family": "latency_convergence",
                "convergence_policy": (
                    "latency is tracked across repeated measurement requests; "
                    "rolling convergence uses the last 20 percent as steady state"
                ),
                "rolling_window_requests": CONVERGENCE_BIN_SIZE,
                "convergence_threshold_ratio": 1.10,
                "convergence_required_windows": 3,
                "measurement_elapsed_ms_max": max(
                    int(request.get("measurement_elapsed_ms", 0) or 0)
                    for request in measurement_rows
                ),
                "request_bin_count": max(
                    int(request.get("request_bin", 0) or 0)
                    for request in measurement_rows
                ),
            }
        )
        if workload_type == "convergence-controlled":
            first_round = [
                request
                for request in measurement_rows
                if int(request.get("round_index", 0) or 0) == 1
            ]
            payload.update(
                {
                    "tracked_key_count": CONVERGENCE_CONTROLLED_KEYS,
                    "measurement_rounds": CONVERGENCE_CONTROLLED_ROUNDS,
                    "distance_class_policy": (
                        "round 1 covers fixed cold provider hop classes; later "
                        "rounds rotate requester hosts near the cached return path"
                    ),
                    "cold_expected_provider_hop_counts": dict(
                        Counter(str(request.get("expected_cold_provider_hop", "")) for request in first_round)
                    ),
                }
            )
    return payload


def single_key_requests(topology: str) -> List[Dict[str, object]]:
    return [
        {
            "request_index": 1,
            "phase": "measurement",
            "tick": 220,
            "node": 3,
            "key": "redis_key_a",
            "provider": host_count(topology),
            "is_first_key_request": True,
        },
        {
            "request_index": 2,
            "phase": "measurement",
            "tick": 900,
            "node": 4,
            "key": "redis_key_a",
            "provider": host_count(topology),
            "is_first_key_request": False,
        },
    ]


def render_yaml(
    topology: str,
    workload_type: str,
    docker: bool,
    requests: Sequence[Dict[str, object]],
    spacing_ms: int | None = None,
    warmup_requests: int = WARMUP_REQUESTS,
    measurement_requests: int = MEASUREMENT_REQUESTS,
) -> str:
    graph = overlay_graph(topology)
    scenario_ticks = scenario_ticks_for_requests(workload_type, requests, spacing_ms)
    lines = [
        f"# Generated Kua-DC v3 {workload_type} workload for {topology}.",
        "# Protocol nodes are hosts only; Kathara renderer adds pure L3 leaf/spine switches.",
        "# Do not edit by hand; regenerate with scripts/generate_workloads_v3.py.",
        f"# physical: hosts={host_count(topology)} leaves={host_count(topology) // 4} spines=4",
        "protocol:",
        "  max_hop_count: 8",
        "",
        "nodes:",
    ]
    host_prefix, port_base = docker_network(topology, workload_type, spacing_ms)
    for node in hosts(topology):
        lines.append(f"  - id: {node}" if docker else f"- id: {node}")
        if docker:
            lines.append(f"    host: {host_prefix}.{node + 10}")
            lines.append(f"    port: {port_base + node}")
            lines.append(f"    neighbors: [{', '.join(str(n) for n in graph[node])}]")
        else:
            lines.append(f"  neighbors: [{', '.join(str(n) for n in graph[node])}]")

    lines.extend(["", "scenario:", f"  ticks: {scenario_ticks}", "  data:"])
    if workload_type == "single-key":
        lines.append(f"    - {host_count(topology)}:redis_key_a=VALUE_OF_A")
    else:
        for provider, key, value in data_entries(topology):
            lines.append(f"    - {provider}:{key}={value}")
    lines.append("  requests:")
    for request in requests:
        lines.append(f"    - {request['node']}:{request['key']}@{request['tick']}")
    return "\n".join(lines) + "\n"


def write_outputs(dry_run: bool) -> None:
    targets: List[Tuple[Path, str]] = []
    metadata_targets: List[Tuple[Path, Dict[str, object]]] = []

    for topology in TOPOLOGIES:
        requests = single_key_requests(topology)
        scenario = scenario_name(topology, "single-key")
        targets.append((KATHARA_SCENARIO_DIR / f"{scenario}.yaml", render_yaml(topology, "single-key", False, requests)))
        targets.append((DOCKER_CONFIG_DIR / f"{scenario}.yaml", render_yaml(topology, "single-key", True, requests)))
        metadata_targets.append((GENERATED_DIR / f"{scenario}.metadata.json", metadata(topology, "single-key", requests)))

        for workload_type in WORKLOADS:
            requests = build_requests(topology, workload_type, SEED)
            validate_generated(topology, workload_type, requests)
            scenario = scenario_name(topology, workload_type)
            targets.append((KATHARA_SCENARIO_DIR / f"{scenario}.yaml", render_yaml(topology, workload_type, False, requests)))
            targets.append((DOCKER_CONFIG_DIR / f"{scenario}.yaml", render_yaml(topology, workload_type, True, requests)))
            metadata_targets.append((GENERATED_DIR / f"{scenario}.metadata.json", metadata(topology, workload_type, requests)))

        for spacing_ms in SPACING_POLICIES_MS:
            requests = build_requests(topology, "zipf", SEED, spacing_ms)
            validate_generated(topology, "zipf", requests)
            scenario = scenario_name(topology, "zipf", spacing_ms)
            targets.append((KATHARA_SCENARIO_DIR / f"{scenario}.yaml", render_yaml(topology, "zipf", False, requests, spacing_ms)))
            targets.append((DOCKER_CONFIG_DIR / f"{scenario}.yaml", render_yaml(topology, "zipf", True, requests, spacing_ms)))
            metadata_targets.append((GENERATED_DIR / f"{scenario}.metadata.json", metadata(topology, "zipf", requests, spacing_ms)))

        requests = build_requests(
            topology,
            "zipf-light",
            SEED,
            LIGHT_SPACING_MS,
            LIGHT_REQUEST_COUNT,
            LIGHT_WARMUP_REQUESTS,
            LIGHT_MEASUREMENT_REQUESTS,
        )
        validate_generated(
            topology,
            "zipf-light",
            requests,
            LIGHT_REQUEST_COUNT,
            LIGHT_WARMUP_REQUESTS,
            LIGHT_MEASUREMENT_REQUESTS,
        )
        scenario = scenario_name(topology, "zipf-light", LIGHT_SPACING_MS)
        targets.append(
            (
                KATHARA_SCENARIO_DIR / f"{scenario}.yaml",
                render_yaml(
                    topology,
                    "zipf-light",
                    False,
                    requests,
                    LIGHT_SPACING_MS,
                    LIGHT_WARMUP_REQUESTS,
                    LIGHT_MEASUREMENT_REQUESTS,
                ),
            )
        )
        targets.append(
            (
                DOCKER_CONFIG_DIR / f"{scenario}.yaml",
                render_yaml(
                    topology,
                    "zipf-light",
                    True,
                    requests,
                    LIGHT_SPACING_MS,
                    LIGHT_WARMUP_REQUESTS,
                    LIGHT_MEASUREMENT_REQUESTS,
                ),
            )
        )
        metadata_targets.append(
            (
                GENERATED_DIR / f"{scenario}.metadata.json",
                metadata(
                    topology,
                    "zipf-light",
                    requests,
                    LIGHT_SPACING_MS,
                    LIGHT_REQUEST_COUNT,
                    LIGHT_WARMUP_REQUESTS,
                    LIGHT_MEASUREMENT_REQUESTS,
                ),
            )
        )

        requests = build_paired_requests(topology, SEED, PAIRED_SPACING_MS)
        validate_generated(
            topology,
            "zipf-paired",
            requests,
            PAIRED_REQUEST_COUNT,
            PAIRED_WARMUP_REQUESTS,
            PAIRED_MEASUREMENT_REQUESTS,
        )
        scenario = scenario_name(topology, "zipf-paired", PAIRED_SPACING_MS)
        targets.append(
            (
                KATHARA_SCENARIO_DIR / f"{scenario}.yaml",
                render_yaml(
                    topology,
                    "zipf-paired",
                    False,
                    requests,
                    PAIRED_SPACING_MS,
                    PAIRED_WARMUP_REQUESTS,
                    PAIRED_MEASUREMENT_REQUESTS,
                ),
            )
        )
        targets.append(
            (
                DOCKER_CONFIG_DIR / f"{scenario}.yaml",
                render_yaml(
                    topology,
                    "zipf-paired",
                    True,
                    requests,
                    PAIRED_SPACING_MS,
                    PAIRED_WARMUP_REQUESTS,
                    PAIRED_MEASUREMENT_REQUESTS,
                ),
            )
        )
        metadata_targets.append(
            (
                GENERATED_DIR / f"{scenario}.metadata.json",
                metadata(
                    topology,
                    "zipf-paired",
                    requests,
                    PAIRED_SPACING_MS,
                    PAIRED_REQUEST_COUNT,
                    PAIRED_WARMUP_REQUESTS,
                    PAIRED_MEASUREMENT_REQUESTS,
                ),
            )
        )

        for pairblock_spacing_ms in PAIRBLOCK_SPACING_POLICIES_MS:
            requests = build_pairblock_requests(topology, SEED, pairblock_spacing_ms)
            validate_generated(
                topology,
                "zipf-pairblock",
                requests,
                PAIRBLOCK_REQUEST_COUNT,
                PAIRBLOCK_WARMUP_REQUESTS,
                PAIRBLOCK_MEASUREMENT_REQUESTS,
            )
            scenario = scenario_name(topology, "zipf-pairblock", pairblock_spacing_ms)
            targets.append(
                (
                    KATHARA_SCENARIO_DIR / f"{scenario}.yaml",
                    render_yaml(
                        topology,
                        "zipf-pairblock",
                        False,
                        requests,
                        pairblock_spacing_ms,
                        PAIRBLOCK_WARMUP_REQUESTS,
                        PAIRBLOCK_MEASUREMENT_REQUESTS,
                    ),
                )
            )
            targets.append(
                (
                    DOCKER_CONFIG_DIR / f"{scenario}.yaml",
                    render_yaml(
                        topology,
                        "zipf-pairblock",
                        True,
                        requests,
                        pairblock_spacing_ms,
                        PAIRBLOCK_WARMUP_REQUESTS,
                        PAIRBLOCK_MEASUREMENT_REQUESTS,
                    ),
                )
            )
            metadata_targets.append(
                (
                    GENERATED_DIR / f"{scenario}.metadata.json",
                    metadata(
                        topology,
                        "zipf-pairblock",
                        requests,
                        pairblock_spacing_ms,
                        PAIRBLOCK_REQUEST_COUNT,
                        PAIRBLOCK_WARMUP_REQUESTS,
                        PAIRBLOCK_MEASUREMENT_REQUESTS,
                    ),
                )
            )

        for distanceblock_spacing_ms in DISTANCEBLOCK_SPACING_POLICIES_MS:
            requests = build_distanceblock_requests(topology, SEED, distanceblock_spacing_ms)
            validate_generated(
                topology,
                "zipf-distanceblock",
                requests,
                DISTANCEBLOCK_REQUEST_COUNT,
                DISTANCEBLOCK_WARMUP_REQUESTS,
                DISTANCEBLOCK_MEASUREMENT_REQUESTS,
            )
            scenario = scenario_name(topology, "zipf-distanceblock", distanceblock_spacing_ms)
            targets.append(
                (
                    KATHARA_SCENARIO_DIR / f"{scenario}.yaml",
                    render_yaml(
                        topology,
                        "zipf-distanceblock",
                        False,
                        requests,
                        distanceblock_spacing_ms,
                        DISTANCEBLOCK_WARMUP_REQUESTS,
                        DISTANCEBLOCK_MEASUREMENT_REQUESTS,
                    ),
                )
            )
            targets.append(
                (
                    DOCKER_CONFIG_DIR / f"{scenario}.yaml",
                    render_yaml(
                        topology,
                        "zipf-distanceblock",
                        True,
                        requests,
                        distanceblock_spacing_ms,
                        DISTANCEBLOCK_WARMUP_REQUESTS,
                        DISTANCEBLOCK_MEASUREMENT_REQUESTS,
                    ),
                )
            )
            metadata_targets.append(
                (
                    GENERATED_DIR / f"{scenario}.metadata.json",
                    metadata(
                        topology,
                        "zipf-distanceblock",
                        requests,
                        distanceblock_spacing_ms,
                        DISTANCEBLOCK_REQUEST_COUNT,
                        DISTANCEBLOCK_WARMUP_REQUESTS,
                        DISTANCEBLOCK_MEASUREMENT_REQUESTS,
                    ),
                )
            )

        for convergence_spacing_ms in CONVERGENCE_SPACING_POLICIES_MS:
            for convergence_workload_type in (
                "convergence-controlled",
                "convergence-zipf",
                "convergence-zipf-long",
                "convergence-uniform",
            ):
                convergence_measurement_requests = (
                    CONVERGENCE_ZIPF_LONG_MEASUREMENT_REQUESTS
                    if convergence_workload_type == "convergence-zipf-long"
                    else CONVERGENCE_MEASUREMENT_REQUESTS
                )
                convergence_request_count = CONVERGENCE_WARMUP_REQUESTS + convergence_measurement_requests
                requests = build_convergence_requests(
                    topology,
                    convergence_workload_type,
                    SEED,
                    convergence_spacing_ms,
                )
                validate_generated(
                    topology,
                    convergence_workload_type,
                    requests,
                    convergence_request_count,
                    CONVERGENCE_WARMUP_REQUESTS,
                    convergence_measurement_requests,
                )
                scenario = scenario_name(topology, convergence_workload_type, convergence_spacing_ms)
                targets.append(
                    (
                        KATHARA_SCENARIO_DIR / f"{scenario}.yaml",
                        render_yaml(
                            topology,
                            convergence_workload_type,
                            False,
                            requests,
                            convergence_spacing_ms,
                            CONVERGENCE_WARMUP_REQUESTS,
                            convergence_measurement_requests,
                        ),
                    )
                )
                targets.append(
                    (
                        DOCKER_CONFIG_DIR / f"{scenario}.yaml",
                        render_yaml(
                            topology,
                            convergence_workload_type,
                            True,
                            requests,
                            convergence_spacing_ms,
                            CONVERGENCE_WARMUP_REQUESTS,
                            convergence_measurement_requests,
                        ),
                    )
                )
                metadata_targets.append(
                    (
                        GENERATED_DIR / f"{scenario}.metadata.json",
                        metadata(
                            topology,
                            convergence_workload_type,
                            requests,
                            convergence_spacing_ms,
                            convergence_request_count,
                            CONVERGENCE_WARMUP_REQUESTS,
                            convergence_measurement_requests,
                        ),
                    )
                )

    for path, content in targets:
        print(f"write {path}")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    for path, payload in metadata_targets:
        print(f"write {path}")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    write_outputs(args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
