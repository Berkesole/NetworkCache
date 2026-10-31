#!/usr/bin/env python3
"""Deterministic tests for the isolated 2048-bit/3-hash federation pilot."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import generate_federation_v3_bf2048_h3_pilot as gen
import run_federation_v3_bf2048_h3_suite as suite


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]


def read_rows(root: Path, slug: str) -> list[dict[str, str]]:
    scenario = f"kua-dc-v3-federation-v3-bf2048-h3-pilot-{slug}-nc-seed001"
    with (root / "mapped" / scenario / "schedule.tsv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_generation() -> None:
    with tempfile.TemporaryDirectory() as left_name, tempfile.TemporaryDirectory() as right_name:
        left, right = Path(left_name), Path(right_name)
        for config in gen.CONFIGS:
            gen.generate(left, *config)
            gen.generate(right, *config)
        gen.validate(left); gen.validate(right)
        assert digest_tree(left) == digest_tree(right)

        schedules = {slug: read_rows(left, slug) for slug, _, _ in gen.CONFIGS}
        endpoint_fields = ("request_index", "phase", "bin", "request_class", "direction",
                           "requester", "owner")
        endpoints = lambda rows: [tuple(row[field] for field in endpoint_fields) for row in rows]
        assert all(endpoints(rows) == endpoints(schedules["catalog512-zipf"])
                   for rows in schedules.values())
        assert all(len(rows) == 3584 for rows in schedules.values())
        assert all(Counter((row["phase"], row["request_class"]) for row in rows) == {
            ("pre", "local"): 1024,
            ("post", "remote"): 2048,
            ("post", "local_control"): 512,
        } for rows in schedules.values())

        expected_remote_unique = {
            "catalog512-zipf": 704,
            "catalog512-uniform": 891,
            "catalog1000-zipf": 906,
            "catalog1000-uniform": 1295,
        }
        for slug, rows in schedules.items():
            remote = [row for row in rows if row["request_class"] == "remote"]
            assert len({row["key"] for row in remote}) == expected_remote_unique[slug]
            for bin_index in range(1, 17):
                in_bin = [row for row in remote if int(row["bin"]) == bin_index]
                assert Counter(row["direction"] for row in in_bin) == {"A_to_B": 64, "B_to_A": 64}
                for direction in ("A_to_B", "B_to_A"):
                    requesters = [int(row["requester"]) for row in in_bin if row["direction"] == direction]
                    expected = range(1, 33) if direction == "A_to_B" else range(33, 65)
                    assert Counter(requesters) == {node: 2 for node in expected}

        for cluster in ("A", "B"):
            small = {gen.key_name(cluster, rank) for rank in range(1, 513)}
            large = {gen.key_name(cluster, rank) for rank in range(1, 1001)}
            assert small < large


def test_cpp_python_hash_agreement() -> None:
    keys = ["alpha", gen.key_name("A", 1), gen.key_name("B", 1000)]
    with tempfile.TemporaryDirectory() as temporary:
        binary = Path(temporary) / "bloom_hash_positions"
        subprocess.run([
            "c++", "-std=c++17", "-I", str(REPO / "src"),
            str(HERE / "bloom_hash_positions.cpp"), "-o", str(binary),
        ], check=True)
        output = subprocess.check_output([str(binary), *keys], text=True)
    observed = {}
    for line in output.splitlines():
        key, *positions = line.split("\t")
        observed[key] = tuple(map(int, positions))
    assert observed == {key: gen.positions(key) for key in keys}
    assert observed["alpha"] == (1924, 384, 790)


def test_metadata_and_scenario() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        gen.generate(root, *gen.CONFIGS[0])
        slug = gen.CONFIGS[0][0]
        scenario = f"kua-dc-v3-federation-v3-bf2048-h3-pilot-{slug}-nc-seed001"
        target = root / "mapped" / scenario
        metadata = json.loads((target / "metadata.json").read_text())
        assert metadata["bloom_bits"] == 2048
        assert metadata["bloom_hash_count"] == 3
        assert metadata["max_hop_count"] == 12
        assert metadata["catalog_signature_collisions"] == 0
        yaml = (root / "scenarios" / f"{scenario}.yaml").read_text()
        assert "max_hop_count: 12" in yaml


def test_one_bootstrap_replacement() -> None:
    original_results = suite.RESULTS
    try:
        with tempfile.TemporaryDirectory() as temporary:
            suite.RESULTS = Path(temporary)
            pilot = suite.PILOTS[0]
            first = suite.RESULTS / "runs" / pilot.slug / "run001"
            first.mkdir(parents=True)
            (first / "metadata.json").write_text(json.dumps({
                "status": "driver_failed", "controller_status": 97,
            }))
            assert suite.bootstrap_replacement_allowed(suite.attempt_dirs(pilot))
            (first / "request_results.csv").write_text("request_index\n")
            assert not suite.bootstrap_replacement_allowed(suite.attempt_dirs(pilot))
            (first / "request_results.csv").unlink()
            second = first.parent / "run002"
            second.mkdir()
            assert not suite.bootstrap_replacement_allowed(suite.attempt_dirs(pilot))
    finally:
        suite.RESULTS = original_results


def main() -> int:
    test_generation()
    test_cpp_python_hash_agreement()
    test_metadata_and_scenario()
    test_one_bootstrap_replacement()
    print("federation-v3 bf2048-h3 pilot tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
