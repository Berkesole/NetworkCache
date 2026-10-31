#!/usr/bin/env python3
"""Generate the frozen capacity-screening matrix (pilot_matrix.json).

Deterministic: the screening order is a fixed-seed shuffle constrained so every
capacity tier appears exactly once in each of the three blocks of eight, giving a
rough (explicitly not perfect) spread across early/middle/late runs.

Usage:
  pilot_matrix_gen.py --plan CAPACITY_SCREENING_PLAN.md --out pilot_matrix.json
  pilot_matrix_gen.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

SCHEMA = "finite-cache-pilot-matrix"
VERSION = 1
ORDER_SEED = "screening-order-v1"

TIERS = [
    {"name": "bypass", "policy": "bypass", "budget_bytes": 0},
    {"name": "lru-0.25MiB", "policy": "lru", "budget_bytes": 262144},
    {"name": "lru-0.5MiB", "policy": "lru", "budget_bytes": 524288},
    {"name": "lru-1MiB", "policy": "lru", "budget_bytes": 1048576},
    {"name": "lru-2MiB", "policy": "lru", "budget_bytes": 2097152},
    {"name": "lru-4MiB", "policy": "lru", "budget_bytes": 4194304},
    {"name": "lru-16MiB", "policy": "lru", "budget_bytes": 16777216},
    {"name": "unlimited", "policy": "unlimited", "budget_bytes": None},
]
SEEDS = [1, 2, 3]
BLOCKS = 3


def matrix_sha256(matrix: dict) -> str:
    frozen = {k: v for k, v in matrix.items() if k != "matrix_sha256"}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()


def build(plan_sha: str) -> dict:
    order = []
    for block in range(BLOCKS):
        rng = random.Random(f"{ORDER_SEED}:{block}")
        tiers = [t["name"] for t in TIERS]
        rng.shuffle(tiers)
        tier_index = {t["name"]: i for i, t in enumerate(TIERS)}
        for tier_name in tiers:
            # the seed depends on the tier's FIXED index and the block, so each
            # tier gets all three seeds across the blocks and no (tier, seed)
            # pair repeats; within a block the three seeds are mixed.
            seed = SEEDS[(tier_index[tier_name] + block) % len(SEEDS)]
            tier = next(t for t in TIERS if t["name"] == tier_name)
            order.append({
                "slot_id": f"pilot-n32-zipf-r128-{tier['name']}-s{seed:03d}",
                "tier": tier["name"], "policy": tier["policy"],
                "budget_bytes": tier["budget_bytes"], "seed": seed, "block": block,
            })
    matrix = {
        "schema": SCHEMA, "version": VERSION,
        "plan_path": "CAPACITY_SCREENING_PLAN.md", "plan_sha256": plan_sha,
        "hosts": 32,
        "scenario_template": "kua-dc-v4-32-latency-v4-bf1024-h3-c512-iid-zipf-b65536-seed{seed:03d}",
        "object_size": 65536, "catalog_objects": 512,
        "rate": 128, "model": "poisson", "arrival_seed_rule": "9172 + seed",
        "setup_requests": 32, "measurement_requests": 4096,
        "seeds": SEEDS, "repetitions": 1,
        "warmup_fraction": 0.25, "measurement_fraction": 0.75,
        "screening_order_seed": ORDER_SEED,
        "tiers": TIERS,
        "order": order,
        "calibration": {
            "seed": 1,
            "tiers": ["lru-0.25MiB", "unlimited"],
            "max_runs": 4,
            "warmup_fraction": 0.25, "measurement_fraction": 0.75,
        },
        "max_calibration_runs": 4,
        "max_screening_slots": 24,
        "image_id": "sha256:f418668fd2ef7e695a45db2673bdeff66a3d49f2d94ca3bbcc2409e910355d3d",
        "image_tag": "rsync-router:concurrency-finite-cache-smoke-81c22fe9",
    }
    matrix["matrix_sha256"] = matrix_sha256(matrix)
    return matrix


def validate(matrix: dict) -> list[str]:
    problems = []
    if matrix.get("schema") != SCHEMA:
        problems.append("bad_schema")
    if matrix.get("version") != VERSION:
        problems.append("bad_version")
    if matrix.get("matrix_sha256") != matrix_sha256(matrix):
        problems.append("matrix_hash_mismatch")
    tiers = {t["name"]: t for t in matrix.get("tiers", [])}
    if len(tiers) != 8:
        problems.append(f"tier_count={len(tiers)}")
    expected = {"bypass": ("bypass", 0), "lru-0.25MiB": ("lru", 262144),
                "lru-0.5MiB": ("lru", 524288), "lru-1MiB": ("lru", 1048576),
                "lru-2MiB": ("lru", 2097152), "lru-4MiB": ("lru", 4194304),
                "lru-16MiB": ("lru", 16777216), "unlimited": ("unlimited", None)}
    for name, (policy, budget) in expected.items():
        tier = tiers.get(name)
        if tier is None:
            problems.append(f"missing_tier:{name}")
        elif tier["policy"] != policy or tier["budget_bytes"] != budget:
            problems.append(f"tier_mismatch:{name}")
    if matrix.get("hosts") != 32 or matrix.get("object_size") != 65536 \
            or matrix.get("catalog_objects") != 512 or matrix.get("rate") != 128 \
            or matrix.get("model") != "poisson":
        problems.append("fixed_design_mismatch")
    order = matrix.get("order", [])
    if len(order) != 24:
        problems.append(f"order_len={len(order)}")
    if len({o["slot_id"] for o in order}) != len(order):
        problems.append("duplicate_slot")
    if sorted({o["seed"] for o in order}) != SEEDS:
        problems.append("seed_set")
    per_block = {}
    for o in order:
        per_block.setdefault(o["block"], []).append(o["tier"])
        tier = tiers.get(o["tier"])
        if tier is None or tier["policy"] != o["policy"] \
                or tier["budget_bytes"] != o["budget_bytes"]:
            problems.append(f"order_tier_mismatch:{o['slot_id']}")
    for block, names in per_block.items():
        if sorted(names) != sorted(tiers):
            problems.append(f"block_not_balanced:{block}")
    for block in range(BLOCKS):
        if block not in per_block:
            problems.append(f"missing_block:{block}")
    return problems


def _self_test() -> int:
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        print(f"{name}: {'PASS' if ok else 'FAIL'}{' ' + detail if detail and not ok else ''}")
        if not ok:
            failures += 1

    m = build("a" * 64)
    check("valid_matrix", not validate(m), str(validate(m)))
    check("24_slots", len(m["order"]) == 24)
    check("three_blocks", {o["block"] for o in m["order"]} == {0, 1, 2})
    check("seed_mix_within_blocks",
          all(len({o["seed"] for o in m["order"] if o["block"] == b}) == 3
              for b in range(BLOCKS)))
    check("tier_once_per_block",
          all(sorted(o["tier"] for o in m["order"] if o["block"] == b) ==
              sorted(t["name"] for t in TIERS) for b in range(BLOCKS)))
    check("unique_slot_ids", len({o["slot_id"] for o in m["order"]}) == 24)
    check("deterministic", build("a" * 64)["order"] == m["order"])
    check("hash_present", len(m["matrix_sha256"]) == 64)

    bad = json.loads(json.dumps(m))
    bad["order"][0]["budget_bytes"] = 999
    bad["matrix_sha256"] = matrix_sha256(bad)
    check("tampered_tier_rejected", bool(validate(bad)))
    bad = json.loads(json.dumps(m))
    del bad["order"][0]
    bad["matrix_sha256"] = matrix_sha256(bad)
    check("short_order_rejected", bool(validate(bad)))
    bad = json.loads(json.dumps(m))
    bad["hosts"] = 16
    bad["matrix_sha256"] = matrix_sha256(bad)
    check("wrong_hosts_rejected", bool(validate(bad)))
    bad = json.loads(json.dumps(m))
    bad["matrix_sha256"] = "0" * 64
    check("hash_mismatch_rejected", bool(validate(bad)))
    return 0 if failures == 0 else 1


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", default="CAPACITY_SCREENING_PLAN.md")
    ap.add_argument("--out", default="pilot_matrix.json")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    plan = Path(args.plan)
    if not plan.is_file():
        raise SystemExit(f"plan not found: {plan}")
    matrix = build(hashlib.sha256(plan.read_bytes()).hexdigest())
    problems = validate(matrix)
    if problems:
        raise SystemExit("generated matrix invalid: " + "; ".join(problems))
    Path(args.out).write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} matrix_sha256={matrix['matrix_sha256']}")
    print(f"plan_sha256={matrix['plan_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))