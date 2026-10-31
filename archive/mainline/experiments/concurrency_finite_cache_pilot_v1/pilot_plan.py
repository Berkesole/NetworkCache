#!/usr/bin/env python3
"""Pilot plan: matrix validation, full-schedule request plan, frozen arrivals.

Reuses the frozen generator for Poisson offsets and the frozen workload files
(schedule.tsv / catalog.tsv / data/nodeN.tsv). The pilot does NOT use the smoke's
6-key plan: it runs the full frozen 32-node Zipf schedule (32 setup + 4096
measurement).

Usage: pilot_plan.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# reuse the already-accepted plan / audit helpers
SMOKE = HERE.parent / "finite_cache_smoke"
if str(SMOKE) not in sys.path:
    sys.path.insert(0, str(SMOKE))
import smoke_plan  # noqa: E402

SCHEMA = "finite-cache-pilot-matrix"
VERSION = 1


def load_matrix(path, plan_path):
    matrix = json.loads(Path(path).read_text(encoding="utf-8"))
    sys.path.insert(0, str(HERE))
    import pilot_matrix_gen
    problems = pilot_matrix_gen.validate(matrix)
    plan = Path(plan_path)
    if not plan.is_file():
        problems.append("plan_missing")
    elif hashlib.sha256(plan.read_bytes()).hexdigest() != matrix.get("plan_sha256"):
        problems.append("plan_sha_mismatch")
    return matrix, problems


def read_tsv(path):
    lines = [l for l in Path(path).read_text(encoding="utf-8").split("\n") if l.strip()]
    header = lines[0].split("\t")
    return [dict(zip(header, l.split("\t"))) for l in lines[1:]]


def full_schedule(inputs, scenario, hosts):
    """The FROZEN full schedule for one seed: setup rows + every measurement row.

    Returns (setup_rows, measurement_rows, digest_from_file)."""
    mapped = Path(inputs) / "mapped" / scenario
    rows = read_tsv(mapped / "schedule.tsv")
    setup = [r for r in rows if r["phase"] == "setup"]
    measurement = [r for r in rows if r["phase"] != "setup"]
    digests = {r.get("schedule_sha256", "") for r in rows}
    if len(digests) != 1:
        raise SystemExit("schedule.tsv has inconsistent schedule_sha256")
    for r in setup + measurement:
        if len(r) < 17:
            raise SystemExit("schedule row missing fields")
    return setup, measurement, digests.pop()


def build_plan(frozen_lib, slots, setup, measurement, rate, model="poisson",
               arrival_seed=None, warmup_fraction=0.25):
    """Freeze the arrival arrays for the whole measurement phase using the frozen
    generator, plus the setup arrival spacing used by the frozen pilot."""
    count = len(measurement)
    arrival, phase = smoke_plan.frozen_offsets(frozen_lib, count, model, rate,
                                               arrival_seed)
    setup_arrival = [i * 25_000_000 for i in range(len(setup))]
    warmup_index = int(count * warmup_fraction)
    plan = {
        "schema": "finite-cache-pilot-plan", "version": 1,
        "model": model, "rate": rate, "arrival_seed": arrival_seed,
        "measurement_count": count, "setup_count": len(setup),
        "measurement_arrival_ns": arrival,
        "setup_arrival_ns": setup_arrival,
        "phase_seconds": phase,
        "warmup_fraction": warmup_fraction,
        "measurement_fraction": 1.0 - warmup_fraction,
        "warmup_index": warmup_index,
        "warmup_request_indexes": [r["request_index"] for r in measurement[:warmup_index]],
        "measurement_request_indexes": [r["request_index"] for r in measurement[warmup_index:]],
        "measurement_window_ns": {
            "start": arrival[warmup_index] if warmup_index < count else arrival[-1],
            "end": arrival[-1],
            "clock": "release-relative monotonic offsets from the frozen generator",
        },
        "request_identity": [[r["request_index"], str(r["requester"]), r["key"],
                              str(r["value_size_bytes"]), r["expected_fnv1a64"]]
                             for r in measurement],
        "setup_identity": [[r["request_index"], str(r["requester"]), r["key"],
                            str(r["value_size_bytes"]), r["expected_fnv1a64"]]
                           for r in setup],
        "slots": slots,
        "note": ("full frozen 32-node Zipf schedule; open Poisson arrival at the "
                 "matrix rate; warmup/measurement split by planned arrival time"),
    }
    plan["plan_sha256"] = plan_hash(plan)
    return plan


def plan_hash(plan):
    frozen = {k: v for k, v in plan.items() if k != "plan_sha256"}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()


def verify_plan(plan, frozen_lib):
    problems = []
    if plan.get("schema") != "finite-cache-pilot-plan" or plan.get("version") != 1:
        problems.append("plan_schema")
    if plan.get("plan_sha256") != plan_hash(plan):
        problems.append("plan_hash_mismatch")
    model, rate, seed = plan.get("model"), plan.get("rate"), plan.get("arrival_seed")
    if not isinstance(rate, int) or rate <= 0:
        problems.append("plan_bad_rate")
        return problems
    expected, _ = smoke_plan.frozen_offsets(frozen_lib, plan["measurement_count"],
                                            model, rate, seed)
    if expected != plan.get("measurement_arrival_ns"):
        problems.append("plan_arrival_not_frozen_generator")
    if plan.get("setup_arrival_ns") != [i * 25_000_000
                                        for i in range(plan.get("setup_count", 0))]:
        problems.append("plan_setup_arrival_mismatch")
    arrival = plan.get("measurement_arrival_ns", [])
    if any(a > b for a, b in zip(arrival, arrival[1:])):
        problems.append("plan_arrival_not_monotonic")
    if len(plan.get("warmup_request_indexes", [])) + \
            len(plan.get("measurement_request_indexes", [])) != plan.get("measurement_count"):
        problems.append("plan_window_split")
    return problems


def _self_test() -> int:
    import tempfile
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        print(f"{name}: {'PASS' if ok else 'FAIL'}{' ' + detail if detail and not ok else ''}")
        if not ok:
            failures += 1

    frozen = Path(__file__).resolve().parents[1] / "concurrency_slides_v5"
    setup = [{"request_index": str(900000 + i), "requester": "1", "key": f"s{i}",
              "value_size_bytes": "65536", "expected_fnv1a64": "0" * 16}
             for i in range(3)]
    measurement = [{"request_index": str(500000 + i), "requester": str(1 + i % 4),
                    "key": f"k{i}", "value_size_bytes": "65536",
                    "expected_fnv1a64": "0" * 16} for i in range(100)]
    plan = build_plan(frozen, [{"slot_id": "s", "policy": "lru", "budget_bytes": 1}],
                      setup, measurement, 128, arrival_seed=9175)
    check("plan_verifies", not verify_plan(plan, frozen), str(verify_plan(plan, frozen)))
    check("arrival_from_frozen_generator",
          plan["measurement_arrival_ns"] ==
          smoke_plan.frozen_offsets(frozen, 100, "poisson", 128, 9175)[0])
    check("warmup_split", len(plan["warmup_request_indexes"]) == 25 and
          len(plan["measurement_request_indexes"]) == 75)
    check("window_start_matches_warmup",
          plan["measurement_window_ns"]["start"] == plan["measurement_arrival_ns"][25])
    check("rate_changes_arrivals",
          build_plan(frozen, [], setup, measurement, 32, arrival_seed=9175)
          ["measurement_arrival_ns"] != plan["measurement_arrival_ns"])
    bad = json.loads(json.dumps(plan))
    bad["measurement_arrival_ns"] = [0] * 100
    bad["plan_sha256"] = plan_hash(bad)
    check("tampered_arrival_rejected", bool(verify_plan(bad, frozen)))
    bad = json.loads(json.dumps(plan))
    bad["plan_sha256"] = "0" * 64
    check("tampered_hash_rejected", bool(verify_plan(bad, frozen)))

    # matrix validation round-trip
    with tempfile.TemporaryDirectory() as tmp:
        import pilot_matrix_gen
        plan_file = Path(tmp) / "PLAN.md"
        plan_file.write_text("plan body", encoding="utf-8")
        matrix = pilot_matrix_gen.build(hashlib.sha256(b"plan body").hexdigest())
        (Path(tmp) / "m.json").write_text(json.dumps(matrix), encoding="utf-8")
        _, problems = load_matrix(Path(tmp) / "m.json", plan_file)
        check("matrix_round_trip", not problems, str(problems))
        (Path(tmp) / "PLAN.md").write_text("changed", encoding="utf-8")
        _, problems = load_matrix(Path(tmp) / "m.json", plan_file)
        check("plan_change_detected", "plan_sha_mismatch" in problems)
    return 0 if failures == 0 else 1


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    ap.error("choose --self-test")
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))