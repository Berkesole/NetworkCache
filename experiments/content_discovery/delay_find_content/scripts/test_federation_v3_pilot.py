#!/usr/bin/env python3
"""Fast deterministic tests for the large-catalog pilot implementation."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader
    sys.modules[name] = module; spec.loader.exec_module(module)
    return module


gen = load("generate_federation_v3_pilot")


def test_schedule() -> None:
    first = gen.canonical(); second = gen.canonical()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert len(first) == 3584
    assert Counter((row["phase"], row["request_class"]) for row in first) == {
        ("pre", "local"): 1024, ("post", "remote"): 2048, ("post", "local_control"): 512}
    for bin_index in range(1, 17):
        rows = [row for row in first if row["phase"] == "post" and row["bin"] == bin_index]
        assert Counter(row["direction"] for row in rows) == {
            "A_to_B": 64, "B_to_A": 64, "A_local": 16, "B_local": 16}
        for direction in ("A_to_B", "B_to_A"):
            requesters = [row["requester_rel"] for row in rows if row["direction"] == direction]
            assert Counter(requesters) == {node: 2 for node in range(1, 33)}
    slots = {gen.key_name(cluster, rank): rank % 1024 for cluster in ("A", "B") for rank in range(1, 10001)}
    original_descriptor = gen.descriptor
    gen.descriptor = lambda key: {"value_size_bytes": gen.SIZE, "content_seed": 1, "expected_fnv1a64": "fixture"}
    try:
        small = gen.map_schedule(first, 1000, "zipf", slots)
        large = gen.map_schedule(first, 10000, "zipf", slots)
        uniform = gen.map_schedule(first, 10000, "uniform", slots)
    finally:
        gen.descriptor = original_descriptor
    endpoint_fields = ("request_index", "phase", "bin", "request_class", "direction", "requester", "owner")
    endpoints = lambda rows: [tuple(row[field] for field in endpoint_fields) for row in rows]
    assert endpoints(small) == endpoints(large) == endpoints(uniform)
    assert [row["key_u"] for row in small] == [row["key_u"] for row in large] == [row["key_u"] for row in uniform]
    assert all(int(row["requester"]) != int(row["owner"]) for row in small)
    for catalog_size in (1000, 10000):
        owners = Counter(gen.owner_absolute(cluster, rank) for cluster in ("A", "B")
                         for rank in range(1, catalog_size + 1))
        assert max(owners.values()) - min(owners.values()) <= 1
    assert {gen.key_name("A", rank) for rank in range(1, 1001)} < {
        gen.key_name("A", rank) for rank in range(1, 10001)}
    zipf_unique = len({row["key"] for row in large if row["request_class"] == "remote"})
    uniform_unique = len({row["key"] for row in uniform if row["request_class"] == "remote"})
    assert 100 < zipf_unique < uniform_unique


def test_payload_hash() -> None:
    keys = [gen.key_name("A", 1), gen.key_name("B", 10000)]
    gen.prepare_payload_hashes(keys)
    for key in keys:
        seed = gen.content_seed(key)
        assert gen.PAYLOAD_HASHES[seed] == gen.v2.latency.content_hash(gen.SIZE, seed)


def test_fast_cache_snapshot() -> None:
    probe = load("federation_v3_cache_probe")
    def fake_redis(*arguments: str) -> str:
        if arguments[0] == "SCAN": return "0\nowned\ncached"
        if arguments[:2] == ("INFO", "memory"): return "used_memory:150000\n"
        raise AssertionError(arguments)
    probe.redis = fake_redis
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); marker = root / "ready.tsv"; output = root / "out.tsv"
        marker.write_text("ready\tready\t0\n")
        probe.snapshot(1, {"owned"}, marker, output)
        fields = output.read_text().strip().split("\t")
        assert fields[6:12] == ["2", "131072", "1", "65536", "1", "65536"]


def test_trace_window_index() -> None:
    analyzer = load("analyze_federation_v3_trace")
    assert analyzer.overlay_path(2, 40)[0] == 2
    assert analyzer.overlay_path(2, 40)[-1] == 40
    assert 1 in analyzer.overlay_path(2, 40) and 33 in analyzer.overlay_path(2, 40)


def main() -> int:
    test_schedule(); test_payload_hash(); test_fast_cache_snapshot(); test_trace_window_index()
    print("federation-v3 pilot tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
