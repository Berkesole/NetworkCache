#!/usr/bin/env python3
"""Generate the isolated 1024-bit/3-hash staged-fallback pilot workloads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import generate_federation_v3_bf2048_h3_pilot as base


SUITE = "federation-v3-bf1024-h3-fallback-pilot"
DEFAULT_ROOT = base.EXP / f"workloads/{SUITE}"


def configure() -> None:
    base.BLOOM_BITS = 1024
    base.DEFAULT_ROOT = DEFAULT_ROOT
    base.suite_name = lambda: SUITE


def generate(root: Path, slug: str, catalog_size: int, distribution: str) -> None:
    configure()
    base.generate(root, slug, catalog_size, distribution)
    scenario = f"kua-dc-v3-{SUITE}-{slug}-nc-seed001"
    metadata_path = root / "mapped" / scenario / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update({
        "wire_version": 3,
        "staged_fallback": True,
        "fallback_delay_ms": 1500,
        "query_table_timeout_ms": 7000,
        "controller_timeout_ms": 8000,
        "request_codec_frame_bytes_single_signature": 162,
        "request_tcp_frame_bytes_single_signature": 166,
    })
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate(root: Path) -> None:
    configure()
    base.validate(root)
    for slug, catalog_size, distribution in base.CONFIGS:
        scenario = f"kua-dc-v3-{SUITE}-{slug}-nc-seed001"
        metadata = json.loads((root / "mapped" / scenario / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["suite"] == SUITE
        assert metadata["catalog_size_per_cluster"] == catalog_size
        assert metadata["distribution"] == distribution
        assert metadata["bloom_bits"] == 1024
        assert metadata["bloom_hash_count"] == 3
        assert metadata["wire_version"] == 3
        assert metadata["staged_fallback"] is True
        assert metadata["fallback_delay_ms"] == 1500
        assert metadata["query_table_timeout_ms"] == 7000
        assert metadata["controller_timeout_ms"] == 8000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    for config in base.CONFIGS:
        generate(args.output_root, *config)
    validate(args.output_root)
    print(f"generated {SUITE} workloads under {args.output_root}")
    return 0


configure()


if __name__ == "__main__":
    raise SystemExit(main())
