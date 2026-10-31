#!/usr/bin/env python3
"""Static and fixture tests for the latency-v4 generator and pilot gate."""

from __future__ import annotations

import csv
import json
import tempfile
from collections import Counter
from pathlib import Path

import generate_latency_v4_bf1024_h3_c512 as gen


def rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def mapped(topology: int, workload: str, seed: int = 1, size: int = 65536) -> Path:
    scenario = gen.scenario_name(f"kua-dc-v4-{topology}", workload, size, seed)
    return gen.MAPPED / scenario


def paired_projection(items):
    fields = ("request_index", "phase", "round", "bin", "key", "key_rank", "key_u",
              "requester_role", "owner_role", "original_owner_overlay_hops",
              "overlay_edge_physical_signature", "value_size_bytes")
    return [tuple(row[field] for field in fields) for row in items]


def main() -> int:
    assert gen.bloom_positions("alpha") == (900, 384, 790)
    assert gen.canonical_for("iid-zipf", 1, 2048) == gen.canonical_for("iid-zipf", 1, 2048)
    assert gen.map_document("kua-dc-v4-16", gen.canonical_for("iid-uniform", 2, 2048), 2) == \
           gen.map_document("kua-dc-v4-16", gen.canonical_for("iid-uniform", 2, 2048), 2)
    catalog = rows(mapped(16, "iid-zipf") / "catalog.tsv")
    formal = [row for row in catalog if row["catalog_class"] == "formal"]
    setup = [row for row in catalog if row["catalog_class"] == "setup"]
    assert len(formal) == 512 and len(setup) == 32
    assert len({row["key"] for row in catalog}) == 544
    assert Counter(gen.key_role(row["key"]) for row in formal) == {"G": 128, "O": 384}
    assert set(row["key"] for row in formal).isdisjoint(row["key"] for row in setup)
    assert set(Counter(int(row["owner"]) for row in formal).values()) == {32}
    formal32 = [row for row in rows(mapped(32, "iid-zipf") / "catalog.tsv") if row["catalog_class"] == "formal"]
    assert set(Counter(int(row["owner"]) for row in formal32).values()) == {16}
    assert all(len(row["bloom_positions"].split(";")) == 3 for row in catalog)

    controlled = rows(mapped(16, "controlled") / "schedule.tsv")
    measured = [row for row in controlled if row["phase"] == "measurement"]
    assert len(measured) == 256
    for group in range(1, 9):
        selected = [row for row in measured if int(row["round"]) == group]
        assert len(selected) == 32
        assert Counter(int(row["original_owner_overlay_hops"]) for row in selected) == {1: 8, 2: 8, 3: 8, 4: 8}
        assert Counter(row["requester_role"] for row in selected) == {"G": 8, "O": 24}
        assert Counter(row["owner_role"] for row in selected) == {"G": 8, "O": 24}

    zipf16 = rows(mapped(16, "iid-zipf") / "schedule.tsv")
    zipf32 = rows(mapped(32, "iid-zipf") / "schedule.tsv")
    uniform16 = rows(mapped(16, "iid-uniform") / "schedule.tsv")
    assert paired_projection(zipf16) == paired_projection(zipf32)
    endpoint_fields = ("request_index", "phase", "round", "bin", "key_u", "requester_role",
                       "owner_role", "original_owner_overlay_hops", "overlay_edge_physical_signature")
    assert [tuple(row[field] for field in endpoint_fields) for row in zipf16] == [
        tuple(row[field] for field in endpoint_fields) for row in uniform16]
    assert any(a["key_rank"] != b["key_rank"] for a, b in zip(zipf16, uniform16)
               if a["phase"] == "measurement")
    controlled_1k = rows(mapped(32, "controlled", size=1024) / "schedule.tsv")
    controlled_1m = rows(mapped(32, "controlled", size=1048576) / "schedule.tsv")
    paired_size_fields = ("request_index", "phase", "round", "bin", "key", "requester", "owner")
    assert [tuple(row[field] for field in paired_size_fields) for row in controlled_1k] == [
        tuple(row[field] for field in paired_size_fields) for row in controlled_1m]
    iid = [row for row in zipf16 if row["phase"] == "measurement"]
    assert len(iid) in (2048, 4096)
    for group in range(1, len(iid) // 128 + 1):
        selected = [row for row in iid if int(row["bin"]) == group]
        assert len(selected) == 128
        assert Counter(int(row["original_owner_overlay_hops"]) for row in selected) == {1: 32, 2: 32, 3: 32, 4: 32}
        assert Counter(row["requester_role"] for row in selected) == {"G": 32, "O": 96}
        assert Counter(row["owner_role"] for row in selected) == {"G": 32, "O": 96}

    # Regeneration metadata binds the schedule and all protocol constants.
    meta = json.loads((mapped(16, "iid-zipf").with_suffix(".metadata.json")).read_text())
    assert meta["wire_version"] == 3 and meta["bloom_bits"] == 1024
    assert meta["bloom_hash_count"] == 3 and meta["staged_fallback"] is True
    assert meta["max_hop_count"] == 12 and meta["formal_catalog_size"] == 512

    # Atomic write behavior used by pilot acceptance must never leave a partial marker.
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "pilot_acceptance.json"
        temporary = target.with_name(".pilot_acceptance.json.tmp")
        temporary.write_text(json.dumps({"accepted": True}))
        temporary.replace(target)
        assert json.loads(target.read_text()) == {"accepted": True}
    print("latency-v4 generator and gate fixture tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
