#!/usr/bin/env python3
"""Deterministic checks for the 4096-bit/3-hash federation pilot."""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import generate_federation_v3_bf4096_h3_pilot as wrapper


gen = wrapper.base


def rows(root: Path, slug: str) -> list[dict[str, str]]:
    scenario = f"kua-dc-v3-federation-v3-bf4096-h3-pilot-{slug}-nc-seed001"
    with (root / "mapped" / scenario / "schedule.tsv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def main() -> int:
    assert gen.BLOOM_BITS == 4096
    assert gen.suite_name() == "federation-v3-bf4096-h3-pilot"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for config in gen.CONFIGS:
            gen.generate(root, *config)
        gen.validate(root)
        schedules = {slug: rows(root, slug) for slug, _, _ in gen.CONFIGS}
        endpoint_fields = ("request_index", "phase", "bin", "request_class", "direction",
                           "requester", "owner", "key_u")
        baseline = [tuple(row[field] for field in endpoint_fields)
                    for row in schedules["catalog512-zipf"]]
        assert all([tuple(row[field] for field in endpoint_fields) for row in candidate] == baseline
                   for candidate in schedules.values())
        expected_unique = {"catalog512-zipf": 704, "catalog512-uniform": 891,
                           "catalog1000-zipf": 906, "catalog1000-uniform": 1295}
        for slug, candidate in schedules.items():
            remote = [row for row in candidate if row["request_class"] == "remote"]
            assert len({row["key"] for row in remote}) == expected_unique[slug]
            assert all(0 <= int(position) < 4096 for row in candidate
                       for position in row["bloom_positions"].split(";"))
        scenario = "kua-dc-v3-federation-v3-bf4096-h3-pilot-catalog512-zipf-nc-seed001"
        metadata = json.loads((root / "mapped" / scenario / "metadata.json").read_text())
        assert metadata["bloom_bits"] == 4096
        assert metadata["bloom_hash_count"] == 3
        assert metadata["max_hop_count"] == 12
        assert metadata["catalog_signature_collisions"] == 0
    print("federation-v3 bf4096-h3 pilot tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
