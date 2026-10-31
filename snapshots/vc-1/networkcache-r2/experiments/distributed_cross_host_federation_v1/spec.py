#!/usr/bin/env python3
"""Frozen design helpers for Cross-host Physical Federation v1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "campaign.json"
METHODS = ("nc", "tcp-direct")
SEEDS = tuple(range(1, 11))
REPETITIONS = (1, 2)
CALIBRATION_SEEDS = (1, 2, 3)
MAX_ATTEMPTS = 4
PRE_REQUESTS = 1024
POST_REQUESTS_PER_BATCH = 160
RUNTIME_IMAGE = "rsync-router:cross-host-federation-v1"
EXPECTED_BASE_IMAGE_ID = (
    "sha256:bce2e56d82225efd51335a3af959ec0c0fc199782ab68281fd16f1ff16c13a74"
)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_config(path: Path = CONFIG_PATH) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    validate_config(config)
    return config


def validate_config(config: Mapping[str, object]) -> None:
    if config.get("suite") != "cross-host-federation-v1":
        raise ValueError("unexpected suite")
    if tuple(config.get("methods", [])) != METHODS:
        raise ValueError("methods must be nc,tcp-direct")
    if tuple(config.get("seeds", [])) != SEEDS:
        raise ValueError("seeds must be 1..10")
    if tuple(config.get("repetitions", [])) != REPETITIONS:
        raise ValueError("repetitions must be 1,2")
    workers = config.get("workers")
    if not isinstance(workers, dict) or set(workers) != {"domain-a", "domain-b"}:
        raise ValueError("exactly domain-a and domain-b workers are required")
    if workers["domain-a"].get("ssh") != "volcengine":
        raise ValueError("domain-a must use volcengine")
    if workers["domain-b"].get("ssh") != "volcengine-1":
        raise ValueError("domain-b must use volcengine-1")
    network = config.get("network")
    expected = {
        "internal_delay_ms": 2,
        "rate_mbit": 1000,
        "queue_packets": 1000,
        "experiment_mtu": 1400,
        "wireguard_mtu": 1420,
        "dci_egress_delay_ms_each_side": 2,
        "injected_loss_percent": 0,
    }
    if not isinstance(network, dict) or any(network.get(k) != v for k, v in expected.items()):
        raise ValueError("network parameters do not match the frozen pre-horizon design")
    if config.get("formal_post_batches") is not None:
        raise ValueError("formal_post_batches must remain null until calibration is complete")


def pre_horizon_fingerprint(config: Mapping[str, object]) -> str:
    return sha256_bytes(canonical_json(config))


@dataclass(frozen=True, order=True)
class Slot:
    ordinal: int
    method: str
    seed: int
    repetition: int

    @property
    def key(self) -> str:
        return f"{self.method}/seed{self.seed:03d}/rep{self.repetition}"


def formal_slots() -> list[Slot]:
    ordered: list[tuple[str, int, int]] = []
    for seed in SEEDS:
        first = METHODS if seed % 2 else tuple(reversed(METHODS))
        for repetition in REPETITIONS:
            methods = first if repetition == 1 else tuple(reversed(first))
            ordered.extend((method, seed, repetition) for method in methods)
    slots = [Slot(index, method, seed, repetition)
             for index, (method, seed, repetition) in enumerate(ordered, 1)]
    if len(slots) != 40 or len({slot.key for slot in slots}) != 40:
        raise AssertionError("formal assignment must contain 40 unique slots")
    return slots


def requests_per_run(post_batches: int) -> int:
    if post_batches <= 0 or post_batches % 4:
        raise ValueError("POST batches must be a positive multiple of four")
    return PRE_REQUESTS + POST_REQUESTS_PER_BATCH * post_batches


def choose_horizon(reports: Iterable[Mapping[str, object]]) -> int:
    reports = list(reports)
    seeds = {int(report["seed"]) for report in reports}
    if seeds != set(CALIBRATION_SEEDS) or len(reports) != len(CALIBRATION_SEEDS):
        raise ValueError("exactly one calibration report is required for seeds 001..003")
    candidates = []
    for report in reports:
        observed = int(report.get("post_batches_observed", 0))
        candidate = report.get("earliest_recommended_post_batches")
        if candidate is None:
            if observed < 96:
                raise ValueError("calibration must extend this seed to 96 POST batches")
            raise ValueError("not converged by batch 96; formal collection is blocked")
        candidate = int(candidate)
        if candidate <= 0 or candidate % 4 or candidate > observed:
            raise ValueError("invalid convergence candidate")
        candidates.append(candidate)
    return max(candidates)


def frozen_campaign(config: Mapping[str, object], post_batches: int,
                    workload_sha256: str, topology_sha256: str,
                    image_id: str) -> dict:
    result = dict(config)
    result["evidence_status"] = "FROZEN_COLLECTING"
    result["formal_post_batches"] = post_batches
    result["requests_per_run"] = requests_per_run(post_batches)
    result["formal_selected_runs"] = 40
    result["formal_requests_total"] = 40 * result["requests_per_run"]
    result["workload_sha256"] = workload_sha256
    result["topology_sha256"] = topology_sha256
    result["runtime_image_id"] = image_id
    result["campaign_fingerprint"] = sha256_bytes(canonical_json(result))
    return result
