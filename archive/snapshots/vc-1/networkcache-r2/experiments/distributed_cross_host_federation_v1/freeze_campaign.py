#!/usr/bin/env python3
"""Freeze horizon, campaign, and two immutable joint worker shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import generate_workloads
import spec


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
WORKLOAD = REPO / "experiments/content_discovery/delay_find_content/workloads/cross-host-federation-v1"


def tree_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(str(path.name).encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_new_or_identical(path: Path, value: object) -> None:
    content = spec.canonical_json(value)
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f"refusing to rewrite frozen artifact {path}")
        return
    path.write_bytes(content)


def freeze(report_paths: list[Path], image_id: str, accepted_post_batches: int | None = None,
           decision_reason: str | None = None) -> dict:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    if accepted_post_batches is None:
        horizon = spec.choose_horizon(reports)
    else:
        if accepted_post_batches != 64 or not decision_reason:
            raise ValueError("explicit acceptance requires 64 POST batches and a recorded reason")
        if len(reports) != 3 or {int(r['seed']) for r in reports} != {1, 2, 3}:
            raise ValueError("all three calibration reports are required")
        if any(int(r['post_batches_observed']) < accepted_post_batches for r in reports):
            raise ValueError("accepted horizon exceeds calibration observations")
        horizon = accepted_post_batches
    for seed in spec.SEEDS:
        generate_workloads.generate_one(WORKLOAD, seed, horizon, "FORMAL")
        generate_workloads.validate_one(WORKLOAD, seed, horizon)
    scenario = generate_workloads.scenario_name(1, horizon)
    topology_files = []
    with tempfile.TemporaryDirectory() as directory:
        for shard in ("a", "b"):
            lab = Path(directory) / shard
            env = {
                **os.environ, "LAB_DIR": str(lab),
                "SCENARIOS_DIR": str(WORKLOAD / "scenarios"),
                "KATHARA_FEDERATION_PHYSICAL_MODE": "bordered-cross-host",
                "KATHARA_FEDERATION_SHARD": shard, "RSYNC_FEDERATION_METHOD": "nc",
                "RSYNC_FEDERATION_ACTIVATION_MODE": "logical-only",
                "KATHARA_LINK_MTU": "1400",
            }
            completed = subprocess.run(
                [str(REPO / "scripts/kathara/render_ruikekkk_scenario.sh"), scenario],
                env=env, check=False, text=True, capture_output=True,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr)
            topology_files.extend([lab / "lab.conf", lab / "lab.dep"])
            topology_files.extend(sorted((lab / "shared/generated").glob("*.network.sh")))
        topology_hash = tree_hash(topology_files)
    workload_files = []
    for seed in spec.SEEDS:
        name = generate_workloads.scenario_name(seed, horizon)
        workload_files.extend(sorted((WORKLOAD / "mapped" / name).rglob("*")))
        workload_files.append(WORKLOAD / "scenarios" / f"{name}.yaml")
    workload_hash = tree_hash([path for path in workload_files if path.is_file()])
    campaign = spec.frozen_campaign(
        spec.load_config(), horizon, workload_hash, topology_hash, image_id
    )
    campaign.pop("campaign_fingerprint")
    campaign["horizon_decision"] = {
        "mode": "user_accepted_fixed_observation" if accepted_post_batches else "convergence_rule",
        "reason": decision_reason, "post_batches": horizon,
        "reports_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in report_paths},
        "strict_convergence_required": accepted_post_batches is None,
    }
    campaign["observer_policy"] = "cross-host-border-observer-v3-tcp-sequence"
    campaign["validator_policy"] = "cross-host-federation-v1-validator-v5-decomposition-coverage"
    campaign["decomposition_policy"] = "retain_all_end_to_end; missing_components_blank; publish_coverage"
    campaign["network_acceptance"] = "frozen_tc_configuration_and_connectivity; observed_rtt_and_loss_are_diagnostics"
    campaign["orchestrator_source_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.glob('*.py'))
    }
    campaign["campaign_fingerprint"] = spec.sha256_bytes(spec.canonical_json(campaign))
    slots = [slot.__dict__ | {"slot": slot.key} for slot in spec.formal_slots()]
    assignment = {
        "suite": "cross-host-federation-v1", "campaign_fingerprint": campaign["campaign_fingerprint"],
        "joint_selected_slots": 40, "participants": ["domain-a", "domain-b"], "slots": slots,
    }
    assignment["assignment_fingerprint"] = spec.sha256_bytes(spec.canonical_json(assignment))
    write_new_or_identical(HERE / "frozen_campaign.json", campaign)
    write_new_or_identical(HERE / "assignment-global.json", assignment)
    for role in ("domain-a", "domain-b"):
        shard = {
            "role": role, "campaign_fingerprint": campaign["campaign_fingerprint"],
            "assignment_fingerprint": assignment["assignment_fingerprint"],
            "joint_selected_slots": 40, "slots": slots,
        }
        shard["shard_fingerprint"] = spec.sha256_bytes(spec.canonical_json(shard))
        write_new_or_identical(HERE / f"assignment-{role}.json", shard)
    return campaign


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs=3, type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--accepted-post-batches", type=int)
    parser.add_argument("--decision-reason")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("freeze_campaign requires --execute")
    print(json.dumps(freeze(args.reports, args.image_id, args.accepted_post_batches,
                            args.decision_reason), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
