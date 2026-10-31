#!/usr/bin/env python3
"""Pilot offline acceptance: recompute every gate and the metrics from the raw
attempt evidence, without trusting any archived status/gate boolean.

Reads: attempt plan.json + monitor-acceptance.json + monitor/ + setup/ +
measurement/requests.csv, and the lab's control/ and traces/. Writes a derived
report to a NEW directory.

Usage:
  pilot_offline_accept.py --attempt-dir DIR --lab-dir DIR --inputs DIR \
      --scenario SC --owner-hashes FILE --matrix FILE --plan-md FILE \
      --frozen-lib DIR [--out REPORT_DIR]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SMOKE = HERE.parent / "finite_cache_smoke"
for path in (str(HERE), str(SMOKE)):
    if path not in sys.path:
        sys.path.insert(0, path)
import smoke_accept  # noqa: E402
import pilot_accept  # noqa: E402
import pilot_metrics  # noqa: E402
import pilot_plan  # noqa: E402


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_ledger(measurement_dir):
    """The controller writes the full per-request ledger to requests.csv."""
    rows = []
    with (measurement_dir / "requests.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
    return rows


def build_owner_evidence(data_dir, hosts, owner_hashes):
    evidence = {}
    for node in range(1, hosts + 1):
        path = data_dir / f"node{node}.tsv"
        expected = owner_hashes.get(str(node))
        if not path.is_file():
            evidence[node] = {"valid": False, "reason": "owner_input_missing"}
            continue
        lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
        header = lines[0].split("\t") if lines else []
        keys = [l.split("\t")[0] for l in lines[1:]]
        if header[:4] != ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"]:
            evidence[node] = {"valid": False, "reason": "owner_schema_mismatch"}
            continue
        if len(keys) != len(set(keys)):
            evidence[node] = {"valid": False, "reason": "owner_duplicate_keys"}
            continue
        digest = sha256(path)
        if expected is not None and digest != expected:
            evidence[node] = {"valid": False, "reason": "owner_hash_mismatch"}
            continue
        evidence[node] = {"valid": True, "keys": keys, "sha256": digest,
                          "expected_sha256": expected}
    return evidence


def monitor_series(monitor_dir):
    series = {}
    for path in sorted(monitor_dir.glob("node*.jsonl")):
        node = int(path.stem.replace("node", ""))
        rows = []
        for raw in path.read_text(encoding="utf-8", errors="replace").split("\n"):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                rows.append({"node": node, "status": "corrupt_raw"})
                continue
            rec = dict(rec, node=node)
            if not rec.get("redis_run_id"):
                rec["redis_run_id"] = rec.get("run_id", "")
            rows.append(rec)
        series[node] = rows
    return series


def verify(attempt_dir, lab_dir, inputs, scenario, owner_hashes, matrix_path,
           plan_md, frozen_lib):
    matrix, problems = pilot_plan.load_matrix(matrix_path, plan_md)
    if problems:
        return {"verdict": "REJECTED", "error": "matrix: " + "; ".join(problems)}
    attempt_dir, lab_dir = Path(attempt_dir), Path(lab_dir)
    plan = json.loads((attempt_dir / "plan.json").read_text(encoding="utf-8"))
    plan_reasons = pilot_plan.verify_plan(plan, Path(frozen_lib))
    hosts = matrix["hosts"]
    setup, measurement, digest = pilot_plan.full_schedule(inputs, scenario, hosts)
    planned = [{"index": str(r["request_index"]), "node": int(r["requester"]),
                "requester": str(r["requester"]), "owner": str(r["owner"]),
                "key": r["key"], "value_bytes": int(r["value_size_bytes"]),
                "expected_fnv1a64": r["expected_fnv1a64"]}
               for r in setup + measurement]
    registered = {i for g in plan["request_identity"] for i in [g[0]]} | \
                 {i for g in plan["setup_identity"] for i in [g[0]]}
    planned_indexes = {r["index"] for r in planned}
    if registered != planned_indexes:
        plan_reasons = list(plan_reasons) + ["reconstructed_indexes_differ"]

    ns = lab_dir / "shared" / "finite-smoke"
    control = ns / "control"
    traces = lab_dir / "shared" / "traces"
    discovered, discovery_problems = smoke_accept.discover_completions(
        control / "completions", planned_indexes)
    audit_paths = sorted((control / "delivery-audit").glob("*.tsv"))
    trace_files, trace_problems = {}, []
    for path in sorted(traces.glob("node*.jsonl")):
        node = int(path.stem.replace("node", ""))
        records, probs = smoke_accept.parse_jsonl_strict(path)
        trace_files[node] = records
        trace_problems.extend(probs)
    acc_path = attempt_dir / "monitor-acceptance.json"
    monitor_acceptance = (json.loads(acc_path.read_text(encoding="utf-8"))
                          if acc_path.is_file() else None)
    series = monitor_series(attempt_dir / "monitor")
    hashes = json.loads(Path(owner_hashes).read_text(encoding="utf-8"))
    per_scenario = hashes.get("scenarios", {})
    node_hashes = per_scenario.get(scenario, hashes.get("owner_hashes", hashes))
    owners = build_owner_evidence(Path(inputs) / "mapped" / scenario / "data",
                                 hosts, node_hashes)
    slot = (matrix["order"] + [{"slot_id": "calibration", "policy": "lru",
                                "budget_bytes": None}])[0]
    policy = json.loads((attempt_dir / "provenance.json").read_text(encoding="utf-8"))["policy"]
    budget = json.loads((attempt_dir / "provenance.json").read_text(encoding="utf-8"))["budget_bytes"]
    require_eviction = policy == "lru" and (budget or 0) <= 4 * 1048576
    verdict = pilot_accept.accept_slot(
        hosts=hosts, planned=planned,
        completion_entries=[(i, control / "completions" / f"{i}.tsv")
                            for i in planned_indexes],
        discovered_by_index=discovered, discovery_problems=discovery_problems,
        audit_paths=audit_paths, trace_files=trace_files,
        trace_problems=trace_problems, monitor_acceptance=monitor_acceptance,
        monitor_series=series, owner_evidence=owners, expected_policy=policy,
        expected_budget=budget, require_eviction=require_eviction, control=control,
        plan_ok=not plan_reasons, plan_reasons=plan_reasons,
        registered_indexes=registered)
    ledger = load_ledger(attempt_dir / "measurement")
    metrics = pilot_metrics.compute_metrics(ledger, plan, hosts)
    metrics["cache"] = pilot_metrics.cache_metrics(verdict["cache_summary"],
                                                   trace_files, metrics)
    return {"verdict": verdict["verdict"], "gates": verdict["gates"],
            "policy": policy, "budget_bytes": budget, "seed": plan["arrival_seed"] - 9172,
            "metrics": metrics, "cache_summary": verdict["cache_summary"]}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--attempt-dir", required=True)
    ap.add_argument("--lab-dir", required=True)
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--owner-hashes", required=True)
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--plan-md", required=True)
    ap.add_argument("--frozen-lib", required=True)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    result = verify(args.attempt_dir, args.lab_dir, args.inputs, args.scenario,
                    args.owner_hashes, args.matrix, args.plan_md, args.frozen_lib)
    report = {"validator_sha256": sha256(Path(__file__)),
              "matrix_sha256": sha256(args.matrix),
              "plan_md_sha256": sha256(args.plan_md),
              "attempt": str(args.attempt_dir), "result": result}
    text = json.dumps(report, indent=2)
    if args.out:
        out = Path(args.out)
        if out.exists():
            print(f"refusing to overwrite {out}", file=sys.stderr)
            return 2
        out.mkdir(parents=True)
        (out / "pilot-offline-acceptance.json").write_text(text + "\n", encoding="utf-8")
    gates = result.get("gates", {})
    print(f"verdict={result['verdict']} policy={result.get('policy')} "
          f"budget={result.get('budget_bytes')}")
    for name, gate in gates.items():
        if gate["state"] != "pass":
            print(f"  {gate['state']}: {name}: {gate['detail'][:120]}")
    m = result.get("metrics")
    if m:
        print(f"  completed={m['completed_measurement']} failed={m['failed_measurement']} "
              f"window_tput={m['window_throughput_per_s']} drain={m['drain_seconds_after_last_arrival']}")
        print(f"  evicts={m['cache']['totals']['evicts']} "
              f"competing_nodes={len(m['cache']['competing_nodes'])}")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))