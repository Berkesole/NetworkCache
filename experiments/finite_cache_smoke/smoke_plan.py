#!/usr/bin/env python3
"""Run-before request plan for the finite-cache smoke.

The arrival process MUST come from the frozen generator
`experiments/concurrency_slides_v5/controller.py:offsets(count, model, rate,
seed)` -- it defines Poisson arrivals and ARRIVAL_SEED=9173. This module freezes
the plan before a run (params + full arrival arrays + request identities +
hash), so the runner loads and verifies the SAME array instead of regenerating
it or passing a fixed 1 ms array.

r32/r128 therefore produce genuinely different arrival arrays, while the three
methods at the same rate share byte-identical arrays (same rate, seed, group
sizes and request identities).

Usage:
  smoke_plan.py build --frozen-lib DIR --slot-id ID --rate R [--out plan.json]
  smoke_plan.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = "finite-cache-smoke-plan"
VERSION = 1
DEFAULT_SEED = 9173
DEFAULT_MODEL = "poisson"
SETUP_SPACING_NS = 25_000_000  # frozen pilot setup spacing


def frozen_offsets(frozen_lib: Path, count: int, model: str, rate: int, seed: int):
    lib = str(frozen_lib)
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import controller as ctl  # frozen generator
    arrival, phase = ctl.offsets(count, model, rate, seed)
    return list(arrival), phase


def build_plan(frozen_lib: Path, slot_id: str, transport: str, groups: list,
               setup: list, rate: int, seed: int = DEFAULT_SEED,
               model: str = DEFAULT_MODEL) -> dict:
    if model not in ("poisson", "burst"):
        raise ValueError(f"unsupported model {model}")
    if rate <= 0:
        raise ValueError("rate must be positive")
    measurement_arrival = []
    for group in groups:
        arrival, _ = frozen_offsets(frozen_lib, len(group), model, rate, seed)
        measurement_arrival.append(arrival)
    setup_arrival = [i * SETUP_SPACING_NS for i in range(len(setup))]
    plan = {
        "schema": SCHEMA,
        "version": VERSION,
        "slot_id": slot_id,
        "transport": transport,
        "rate": rate,
        "seed": seed,
        "model": model,
        "group_sizes": [len(g) for g in groups],
        "setup_size": len(setup),
        "request_indexes": [[r["request_index"] for r in g] for g in groups],
        "setup_indexes": [r["request_index"] for r in setup],
        "measurement_arrival_ns": measurement_arrival,
        "setup_arrival_ns": setup_arrival,
        "request_identity": [
            [[r["request_index"], str(r["requester"]), r["key"],
              str(r["value_size_bytes"]), r["expected_fnv1a64"]] for r in g]
            for g in groups
        ],
        "release_rule": "frozen_open_arrival",
        "note": ("four short Poisson-arrival groups (setup + 4 measurement); "
                 "not a long steady-state load"),
    }
    plan["plan_sha256"] = plan_sha256(plan)
    return plan


def plan_sha256(plan: dict) -> str:
    frozen = {k: v for k, v in plan.items() if k != "plan_sha256"}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()


def verify_plan(plan: dict, frozen_lib: Path) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if plan.get("schema") != SCHEMA:
        reasons.append("plan_bad_schema")
    if plan.get("version") != VERSION:
        reasons.append("plan_bad_version")
    if plan.get("plan_sha256") != plan_sha256(plan):
        reasons.append("plan_hash_mismatch")
    model = plan.get("model")
    rate = plan.get("rate")
    seed = plan.get("seed")
    if not isinstance(rate, int) or rate <= 0:
        reasons.append("plan_bad_rate")
        return (not reasons), reasons
    expected = [frozen_offsets(frozen_lib, n, model, rate, seed)[0]
                for n in plan.get("group_sizes", [])]
    stored = plan.get("measurement_arrival_ns", [])
    if expected != stored:
        reasons.append("plan_arrival_not_frozen_generator")
    if plan.get("setup_arrival_ns") != [i * SETUP_SPACING_NS
                                        for i in range(plan.get("setup_size", 0))]:
        reasons.append("plan_setup_arrival_mismatch")
    for arrival in stored:
        if any(a > b for a, b in zip(arrival, arrival[1:])):
            reasons.append("plan_arrival_not_monotonic")
            break
    return (not reasons), reasons


def _self_test() -> int:
    failures = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"{name}: {'PASS' if condition else 'FAIL'}{' ' + detail if detail and not condition else ''}")
        if not condition:
            failures += 1

    frozen = Path(__file__).resolve().parents[1] / "concurrency_slides_v5"
    groups = [[{"request_index": str(500001 + i + 4 * g), "requester": "1",
                "key": f"k{i}", "value_size_bytes": "65536",
                "expected_fnv1a64": "0" * 16} for i in range(4)]
              for g in range(4)]
    setup = [{"request_index": str(i), "requester": "1", "key": "s",
              "value_size_bytes": "65536", "expected_fnv1a64": "0" * 16}
             for i in range(32)]

    p32a = build_plan(frozen, "slot-r32-nc", "nc", groups, setup, 32)
    p32b = build_plan(frozen, "slot-r32-tcp", "tcp-direct", groups, setup, 32)
    p128 = build_plan(frozen, "slot-r128-nc", "nc", groups, setup, 128)

    check("plan_verifies", verify_plan(p32a, frozen)[0], str(verify_plan(p32a, frozen)[1]))
    check("arrival_is_frozen_generator_output",
          p32a["measurement_arrival_ns"][0] ==
          frozen_offsets(frozen, 4, "poisson", 32, DEFAULT_SEED)[0])
    check("r32_differs_from_r128",
          p32a["measurement_arrival_ns"] != p128["measurement_arrival_ns"])
    check("not_the_old_fixed_1ms_array",
          p32a["measurement_arrival_ns"][0] != [0, 1_000_000, 2_000_000, 3_000_000])
    check("same_rate_methods_share_arrays",
          p32a["measurement_arrival_ns"] == p32b["measurement_arrival_ns"] and
          p32a["request_indexes"] == p32b["request_indexes"])
    check("r128_shorter_than_r32",
          p128["measurement_arrival_ns"][0][-1] < p32a["measurement_arrival_ns"][0][-1])
    check("setup_arrival_frozen", p32a["setup_arrival_ns"][1] == SETUP_SPACING_NS)

    # wrong rate
    bad = json.loads(json.dumps(p32a))
    bad["rate"] = 128
    bad["plan_sha256"] = plan_sha256(bad)
    check("wrong_rate_rejected", not verify_plan(bad, frozen)[0])
    # wrong seed
    bad = json.loads(json.dumps(p32a))
    bad["seed"] = 1
    bad["plan_sha256"] = plan_sha256(bad)
    check("wrong_seed_rejected", not verify_plan(bad, frozen)[0])
    # replaced array, hash recomputed to hide it -> still rejected because the
    # array must equal the frozen generator output
    bad = json.loads(json.dumps(p32a))
    bad["measurement_arrival_ns"][0] = [0, 1_000_000, 2_000_000, 3_000_000]
    bad["plan_sha256"] = plan_sha256(bad)
    check("replaced_array_rejected", not verify_plan(bad, frozen)[0])
    # tampered array without rehashing -> hash mismatch
    bad = json.loads(json.dumps(p32a))
    bad["measurement_arrival_ns"][0] = [0, 1, 2, 3]
    check("tampered_hash_rejected", not verify_plan(bad, frozen)[0])
    # non-monotonic
    bad = json.loads(json.dumps(p32a))
    bad["measurement_arrival_ns"][0] = [0, 5, 1, 2]
    bad["plan_sha256"] = plan_sha256(bad)
    check("non_monotonic_rejected", not verify_plan(bad, frozen)[0])
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", choices=["build"])
    ap.add_argument("--frozen-lib")
    ap.add_argument("--slot-id", default="")
    ap.add_argument("--transport", default="nc")
    ap.add_argument("--rate", type=int, default=32)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    ap.error("choose --self-test (the runner builds plans via build_plan)")
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))