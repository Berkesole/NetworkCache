#!/usr/bin/env python3
"""Deterministic design and validator boundary tests for federation-v1."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader
    spec.loader.exec_module(module); return module


gen = load("generate_federation_v1")


def main() -> int:
    for seed in gen.SEEDS:
        repeated_a = gen.make(seed, "repeated"); repeated_b = gen.make(seed, "repeated")
        uniform = gen.make(seed, "uniform")
        assert json.dumps(repeated_a, sort_keys=True) == json.dumps(repeated_b, sort_keys=True)
        measured = [r for r in repeated_a["rows"] if r["phase"] == "measurement"]
        warm = [r for r in repeated_a["rows"] if r["phase"] == "warmup"]
        assert len(measured) == 128 and len(warm) == 64
        assert all(gen.cluster(r["requester"]) == gen.cluster(r["owner"]) for r in warm)
        assert all(gen.cluster(r["requester"]) != gen.cluster(r["owner"]) for r in measured)
        assert {r["key"] for r in warm}.isdisjoint({r["key"] for r in measured})
        assert all(sum(r["round"] == rnd for r in measured) == 16 for rnd in range(1, 9))
        rep_endpoint = [(r["requester"], r["owner"], r["direction"], r["round"]) for r in measured]
        uni_endpoint = [(r["requester"], r["owner"], r["direction"], r["round"])
                        for r in uniform["rows"] if r["phase"] == "measurement"]
        assert rep_endpoint == uni_endpoint
        uniform_measured = [r for r in uniform["rows"] if r["phase"] == "measurement"]
        assert all(1 <= r["key_rank"] <= 32 for r in uniform_measured if r["direction"] == "B_to_A")
        assert all(33 <= r["key_rank"] <= 64 for r in uniform_measured if r["direction"] == "A_to_B")
    assert gen.sha(gen.make(1, "repeated")) != gen.sha(gen.make(2, "repeated"))
    scenario = gen.render_scenario()
    assert "neighbors: [" in scenario and "- id: 64" in scenario
    assert " 33" not in next(line for line in scenario.splitlines()[scenario.splitlines().index("- id: 1")+1:] if "neighbors:" in line)
    print("federation-v1 generator tests passed")
    return 0


if __name__ == "__main__": raise SystemExit(main())
