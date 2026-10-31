#!/usr/bin/env python3
"""Generate deterministic Kua-DC v2 workload scenarios.

The generator writes both Kathara and Docker scenario YAML plus a JSON sidecar
used by the log analyzer to recover workload phase and request index without
instrumenting the protocol runtime.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[4]
EXP_DIR = Path(__file__).resolve().parents[1]
KATHARA_SCENARIO_DIR = REPO_ROOT / "kathara" / "ruikekkk" / "scenarios"
DOCKER_CONFIG_DIR = EXP_DIR / "configs"
WORKLOAD_DIR = EXP_DIR / "workloads" / "kua-dc-v2"
GENERATED_DIR = WORKLOAD_DIR / "generated"

KEY_COUNT = 64
REQUEST_COUNT = 200
WARMUP_REQUESTS = 60
MEASUREMENT_REQUESTS = 140
WARMUP_TICK_RANGE = (80, 520)
MEASUREMENT_TICK_RANGE = (600, 1450)
SCENARIO_TICKS = 1600
ZIPF_ALPHA = 1.1
KEY_INDEX_COOLDOWN = 3

WORKLOADS = ("zipf", "uniform")
TOPOLOGIES = ("kua-dc-16", "kua-dc-32")
SEED = 1


def node_ids(topology: str) -> List[int]:
    if topology == "kua-dc-16":
        return list(range(1, 17))
    if topology == "kua-dc-32":
        return list(range(1, 33))
    raise ValueError(f"unknown topology: {topology}")


def graph(topology: str) -> Dict[int, List[int]]:
    if topology == "kua-dc-16":
        result: Dict[int, List[int]] = {
            1: [10],
            2: [10],
            3: [10],
            4: [11],
            5: [11],
            6: [11],
            7: [12],
            8: [12],
            9: [12],
            10: [1, 2, 3, 13, 14, 15, 16],
            11: [4, 5, 6, 13, 14, 15, 16],
            12: [7, 8, 9, 13, 14, 15, 16],
        }
        for core in range(13, 17):
            result[core] = [10, 11, 12]
        return result
    if topology == "kua-dc-32":
        result = {}
        for node in range(1, 7):
            result[node] = [25]
        for node in range(7, 13):
            result[node] = [26]
        for node in range(13, 19):
            result[node] = [27]
        for node in range(19, 25):
            result[node] = [28]
        result[25] = [1, 2, 3, 4, 5, 6, 29, 30, 31, 32]
        result[26] = [7, 8, 9, 10, 11, 12, 29, 30, 31, 32]
        result[27] = [13, 14, 15, 16, 17, 18, 29, 30, 31, 32]
        result[28] = [19, 20, 21, 22, 23, 24, 29, 30, 31, 32]
        for core in range(29, 33):
            result[core] = [25, 26, 27, 28]
        return result
    raise ValueError(f"unknown topology: {topology}")


def docker_network(topology: str, workload_type: str) -> Tuple[str, int, int]:
    table = {
        ("kua-dc-16", "zipf"): ("172.31.70", 43700, 70),
        ("kua-dc-32", "zipf"): ("172.31.80", 43800, 80),
        ("kua-dc-16", "uniform"): ("172.31.90", 43900, 90),
        ("kua-dc-32", "uniform"): ("172.31.100", 44000, 100),
    }
    return table[(topology, workload_type)]


def providers(topology: str) -> List[int]:
    return [6, 7, 8, 9] if topology == "kua-dc-16" else list(range(17, 25))


def requesters(topology: str) -> List[int]:
    return list(range(1, 7)) if topology == "kua-dc-16" else list(range(1, 17))


def keys() -> List[str]:
    return [f"redis_key_{index:03d}" for index in range(1, KEY_COUNT + 1)]


def evenly_spaced_ticks(count: int, start: int, end: int) -> List[int]:
    if count == 1:
        return [start]
    ticks = [round(start + index * (end - start) / (count - 1)) for index in range(count)]
    if len(set(ticks)) != len(ticks):
        raise ValueError(f"tick range {start}..{end} cannot hold {count} unique requests")
    return ticks


def request_key_counts(workload_type: str) -> Counter[str]:
    population = keys()
    if workload_type == "zipf":
        weights = [1.0 / (rank**ZIPF_ALPHA) for rank in range(1, KEY_COUNT + 1)]
    elif workload_type == "uniform":
        weights = [1.0 for _ in population]
    else:
        raise ValueError(f"unknown workload type: {workload_type}")
    base = 1 if REQUEST_COUNT >= KEY_COUNT else 0
    remaining = REQUEST_COUNT - (base * KEY_COUNT)
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


def introduction_positions(count: int) -> List[int]:
    if count <= 0:
        return []
    step = MEASUREMENT_REQUESTS // (count + 1)
    return [WARMUP_REQUESTS + (index + 1) * step for index in range(count)]


def request_key_sequence(workload_type: str, rng: random.Random) -> List[str]:
    remaining = request_key_counts(workload_type)
    ranks = {key: index + 1 for index, key in enumerate(keys())}
    introduction_keys = keys()
    if workload_type == "uniform":
        rng.shuffle(introduction_keys)
    warmup_introductions = introduction_keys[:WARMUP_REQUESTS]
    measurement_introductions = introduction_keys[WARMUP_REQUESTS:KEY_COUNT]
    forced_introductions = {
        position: key for position, key in zip(range(WARMUP_REQUESTS), warmup_introductions)
    }
    forced_introductions.update(
        {
            position: key
            for position, key in zip(introduction_positions(len(measurement_introductions)), measurement_introductions)
        }
    )
    introduced = set()
    last_seen = {key: -REQUEST_COUNT for key in keys()}
    sequence: List[str] = []

    for index in range(REQUEST_COUNT):
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
                eligible = [
                    key
                    for key in keys()
                    if key in introduced
                    and remaining[key] > 0
                ]
            if not eligible:
                raise ValueError("no introduced key is available for repeat scheduling")
            max_remaining = max(remaining[key] for key in eligible)
            candidates = [key for key in eligible if remaining[key] == max_remaining]
            if workload_type == "zipf":
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
    if not candidates:
        raise ValueError("requester set cannot avoid provider-local requests")
    unused = [node for node in candidates if node not in per_key_requesters.get(key, set())]
    if unused:
        candidates = unused
    start = rng.randrange(len(candidates))
    requester = candidates[(start + request_index) % len(candidates)]
    per_key_requesters.setdefault(key, set()).add(requester)
    return requester


def build_requests(topology: str, workload_type: str, seed: int) -> List[Dict[str, object]]:
    rng = random.Random(f"{topology}:{workload_type}:{seed}")
    key_to_provider = key_provider_map(topology)
    requester_nodes = requesters(topology)
    warmup_ticks = evenly_spaced_ticks(WARMUP_REQUESTS, *WARMUP_TICK_RANGE)
    measurement_ticks = evenly_spaced_ticks(MEASUREMENT_REQUESTS, *MEASUREMENT_TICK_RANGE)
    chosen_keys = request_key_sequence(workload_type, rng)
    phase_ticks = [("warmup", tick) for tick in warmup_ticks] + [
        ("measurement", tick) for tick in measurement_ticks
    ]
    requests: List[Dict[str, object]] = []
    seen_keys = set()
    per_key_requesters: Dict[str, set[int]] = {}
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


def validate_generated(topology: str, workload_type: str, requests: Sequence[Dict[str, object]]) -> None:
    if len(requests) != REQUEST_COUNT:
        raise ValueError("request count mismatch")
    counts = Counter(str(request["phase"]) for request in requests)
    if counts["warmup"] != WARMUP_REQUESTS or counts["measurement"] != MEASUREMENT_REQUESTS:
        raise ValueError(f"phase count mismatch: {counts}")
    if len({int(request["tick"]) for request in requests}) != REQUEST_COUNT:
        raise ValueError("request ticks are not unique")
    provider_nodes = set(providers(topology))
    requester_nodes = set(requesters(topology))
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
    if workload_type == "zipf":
        top_n = max(1, KEY_COUNT // 10)
        top_mean = sum(ordered_counts[:top_n]) / top_n
        tail_values = ordered_counts[-top_n:]
        tail_mean = sum(tail_values) / len(tail_values)
        if top_mean <= tail_mean * 2:
            raise ValueError("Zipf distribution is not skewed enough")
    else:
        if max(ordered_counts) - min(ordered_counts) > 8:
            raise ValueError("Uniform distribution is unexpectedly skewed")


def data_entries(topology: str) -> List[Tuple[int, str, str]]:
    key_to_provider = key_provider_map(topology)
    return [(key_to_provider[key], key, f"VALUE_{key[-3:]}") for key in keys()]


def metadata(topology: str, workload_type: str, requests: Sequence[Dict[str, object]]) -> Dict[str, object]:
    scenario = scenario_name(topology, workload_type)
    return {
        "workload_id": f"kua-dc-v2-{workload_type}",
        "workload_type": workload_type,
        "scenario": scenario,
        "topology": topology,
        "seed": SEED,
        "key_count": KEY_COUNT,
        "request_count": REQUEST_COUNT,
        "warmup_requests": WARMUP_REQUESTS,
        "measurement_requests": MEASUREMENT_REQUESTS,
        "provider_count": len(providers(topology)),
        "requester_count": len(requesters(topology)),
        "zipf_alpha": ZIPF_ALPHA if workload_type == "zipf" else None,
        "phase_policy": (
            f"warmup={WARMUP_REQUESTS} requests ticks {WARMUP_TICK_RANGE[0]}..{WARMUP_TICK_RANGE[1]}; "
            f"measurement={MEASUREMENT_REQUESTS} requests ticks {MEASUREMENT_TICK_RANGE[0]}..{MEASUREMENT_TICK_RANGE[1]}"
        ),
        "providers": providers(topology),
        "requesters": requesters(topology),
        "keys": [
            {"key": key, "provider": provider, "value": value}
            for provider, key, value in data_entries(topology)
        ],
        "requests": list(requests),
    }


def scenario_name(topology: str, workload_type: str) -> str:
    return f"{topology}-{workload_type}-seed{SEED:03d}"


def render_yaml(topology: str, workload_type: str, docker: bool, requests: Sequence[Dict[str, object]]) -> str:
    lines: List[str] = [
        f"# Generated Kua-DC v2 {workload_type} workload for {topology}.",
        "# Do not edit by hand; regenerate with scripts/generate_workloads.py.",
        "protocol:",
        "  max_hop_count: 8",
        "",
        "nodes:",
    ]
    host_prefix, port_base, _ = docker_network(topology, workload_type)
    for node in node_ids(topology):
        lines.append(f"  - id: {node}" if docker else f"- id: {node}")
        if docker:
            lines.append(f"    host: {host_prefix}.{node + 10}")
            lines.append(f"    port: {port_base + node}")
            lines.append(f"    neighbors: [{', '.join(str(n) for n in graph(topology)[node])}]")
        else:
            lines.append(f"  neighbors: [{', '.join(str(n) for n in graph(topology)[node])}]")
    lines.extend(["", "scenario:", f"  ticks: {SCENARIO_TICKS}", "  data:"])
    for provider, key, value in data_entries(topology):
        lines.append(f"    - {provider}:{key}={value}")
    lines.append("  requests:")
    for request in requests:
        lines.append(f"    - {request['node']}:{request['key']}@{request['tick']}")
    return "\n".join(lines) + "\n"


def write_outputs(dry_run: bool = False) -> None:
    targets: List[Tuple[Path, str]] = []
    metadata_targets: List[Tuple[Path, Dict[str, object]]] = []
    for topology in TOPOLOGIES:
        for workload_type in WORKLOADS:
            requests = build_requests(topology, workload_type, SEED)
            validate_generated(topology, workload_type, requests)
            scenario = scenario_name(topology, workload_type)
            targets.append((KATHARA_SCENARIO_DIR / f"{scenario}.yaml", render_yaml(topology, workload_type, False, requests)))
            targets.append((DOCKER_CONFIG_DIR / f"{scenario}.yaml", render_yaml(topology, workload_type, True, requests)))
            metadata_targets.append((GENERATED_DIR / f"{scenario}.metadata.json", metadata(topology, workload_type, requests)))

    if dry_run:
        for path, _ in targets:
            print(path)
        for path, _ in metadata_targets:
            print(path)
        return

    for path, content in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for path, content in metadata_targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print generated paths without writing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    write_outputs(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
