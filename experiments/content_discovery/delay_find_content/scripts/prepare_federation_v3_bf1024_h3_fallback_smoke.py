#!/usr/bin/env python3
"""Derive a 224-request smoke from the canonical staged-fallback workload."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import generate_federation_v3_bf1024_h3_fallback_pilot as generator


EXP = Path(__file__).resolve().parent.parent
SOURCE_ROOT = EXP / "workloads/federation-v3-bf1024-h3-fallback-pilot"
TARGET_ROOT = EXP / "workloads/federation-v3-bf1024-h3-fallback-pilot-smoke"
SOURCE_SCENARIO = "kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot-catalog512-zipf-nc-seed001"
TARGET_SCENARIO = "kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot-smoke-catalog512-zipf-nc-seed001"


def main() -> int:
    source = SOURCE_ROOT / "mapped" / SOURCE_SCENARIO
    target = TARGET_ROOT / "mapped" / TARGET_SCENARIO
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with (source / "schedule.tsv").open(newline="", encoding="utf-8") as handle:
        original = list(csv.DictReader(handle, delimiter="\t"))
    selected = [dict(row) for row in original if row["phase"] == "pre"][:64]
    selected += [dict(row) for row in original if row["phase"] == "post" and row["bin"] == "1"]
    for index, row in enumerate(selected, 1):
        row["request_index"] = str(index)
    digest_rows = [{key: value for key, value in row.items() if key != "schedule_sha256"} for row in selected]
    digest = hashlib.sha256(json.dumps(digest_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    for row in selected:
        row["schedule_sha256"] = digest
    generator.base.base.write_tsv(target / "schedule.tsv", generator.base.SCHEDULE_COLUMNS, selected)
    shutil.copytree(source / "data", target / "data")
    for name in ("catalog.tsv", "bloom_collision_summary.json"):
        shutil.copy2(source / name, target / name)
    with (source / "isolation_probes.tsv").open(newline="", encoding="utf-8") as handle:
        probes = list(csv.DictReader(handle, delimiter="\t"))
    for row in probes:
        row["schedule_sha256"] = digest
    generator.base.base.write_tsv(target / "isolation_probes.tsv", list(probes[0]), probes)
    activation = json.loads((source / "activation.json").read_text(encoding="utf-8"))
    activation["schedule_sha256"] = digest
    (target / "activation.json").write_text(json.dumps(activation, indent=2, sort_keys=True) + "\n")
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    metadata.update({
        "scenario": TARGET_SCENARIO,
        "slug": "smoke-catalog512-zipf",
        "schedule_sha256": digest,
        "measurement_requests": len(selected),
        "pre_local_requests": 64,
        "post_remote_requests": 128,
        "post_local_control_requests": 32,
        "smoke": True,
    })
    (target / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    scenarios = TARGET_ROOT / "scenarios"
    scenarios.mkdir(parents=True, exist_ok=True)
    yaml = (SOURCE_ROOT / "scenarios" / f"{SOURCE_SCENARIO}.yaml").read_text(encoding="utf-8")
    (scenarios / f"{TARGET_SCENARIO}.yaml").write_text(yaml, encoding="utf-8")
    print(f"prepared {len(selected)}-request staged-fallback smoke at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
