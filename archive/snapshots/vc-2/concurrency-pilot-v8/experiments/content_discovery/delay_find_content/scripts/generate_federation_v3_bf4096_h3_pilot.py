#!/usr/bin/env python3
"""Generate the isolated 4096-bit/3-hash federation pilot workloads."""

from pathlib import Path

import generate_federation_v3_bf2048_h3_pilot as base


base.BLOOM_BITS = 4096
base.DEFAULT_ROOT = base.EXP / "workloads/federation-v3-bf4096-h3-pilot"


if __name__ == "__main__":
    raise SystemExit(base.main())
