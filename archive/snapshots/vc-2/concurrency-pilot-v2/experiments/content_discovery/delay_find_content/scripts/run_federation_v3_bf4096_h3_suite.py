#!/usr/bin/env python3
"""Run the isolated 4096-bit/3-hash federation pilot suite."""

import run_federation_v3_bf2048_h3_suite as base


base.BLOOM_BITS = 4096
base.BLOOM_HASH_COUNT = 3
base.SUITE = "federation-v3-bf4096-h3-pilot"
base.RESULTS = base.EXP / "results/federation-v3-bf4096-h3-pilot"
base.GENERATOR = "generate_federation_v3_bf4096_h3_pilot.py"
base.RUN_ONCE = "run_federation_v3_bf4096_h3_once.sh"
base.ANALYZER = "analyze_federation_v3_bf4096_h3_pilot.py"


if __name__ == "__main__":
    raise SystemExit(base.main())
