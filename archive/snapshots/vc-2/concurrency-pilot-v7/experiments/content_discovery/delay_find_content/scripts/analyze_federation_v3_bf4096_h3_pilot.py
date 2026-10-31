#!/usr/bin/env python3
"""Create descriptive outputs for the isolated 4096-bit/3-hash pilots."""

import analyze_federation_v3_bf2048_h3_pilot as analysis
import run_federation_v3_bf4096_h3_suite as suite


analysis.PILOTS = suite.base.PILOTS
analysis.RESULTS = suite.base.RESULTS
analysis.run_dir = suite.base.run_dir
analysis.validate = suite.base.validate
analysis.ASSETS = analysis.EXP / "paper_assets/federation-v3-bf4096-h3-pilot"


if __name__ == "__main__":
    raise SystemExit(analysis.main())
