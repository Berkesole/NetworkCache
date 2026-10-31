#!/usr/bin/env python3
"""Campaign-lock creation, attachment and tamper tests."""

from __future__ import annotations

import json
import hashlib
import tempfile
from pathlib import Path

import formal_campaign as campaign
import run_federation_v4_bf1024_h3_formal_suite as federation
import run_latency_v4_bf1024_h3_c512_suite as latency


def main() -> int:
    assert campaign.ROOT.name == "formal-bf1024-h3-campaign-r2"
    assert latency.RESULTS_ROOT.name == "latency-v4-bf1024-h3-c512-formal-r2"
    assert latency.PILOT_RESULTS_ROOT.name == "latency-v4-bf1024-h3-c512"
    assert federation.RESULTS.name == "federation-v4-bf1024-h3-formal-r2"
    assert latency.RESULTS_ROOT != latency.PILOT_RESULTS_ROOT

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.py"
        workload = root / "schedule.tsv"
        source.write_text("source-v1\n")
        workload.write_text("schedule-v1\n")
        campaign.ROOT = root / "lock"
        campaign.LOCK = campaign.ROOT / "campaign_lock.json"
        campaign.SOURCE_MANIFEST = campaign.ROOT / "source_manifest.csv"
        campaign.WORKLOAD_MANIFEST = campaign.ROOT / "workload_manifest.csv"
        campaign.source_paths = lambda: [source]
        campaign.workload_paths = lambda: [workload]
        campaign.runtime_image_id = lambda: "sha256:test-runtime-image"
        campaign.rows = lambda paths: [
            {"path": path.name, "bytes": path.stat().st_size,
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in paths
        ]
        assert campaign.create() == 0
        fingerprint = json.loads(campaign.LOCK.read_text())["campaign_fingerprint"]
        run = root / "run001"
        assert campaign.attach(run) == 0
        assert json.loads((run / "campaign_lock.json").read_text())["campaign_fingerprint"] == fingerprint
        source.write_text("source-v2\n")
        try:
            campaign.verify()
        except SystemExit as error:
            assert "source_fingerprint" in str(error)
        else:
            raise AssertionError("campaign source tamper was not rejected")
    print("formal campaign tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
