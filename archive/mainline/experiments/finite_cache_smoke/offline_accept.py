#!/usr/bin/env python3
"""Unified, independently runnable offline acceptance for a smoke selection.

It reads the sealed evidence plus an EXPLICIT input manifest and recomputes every
gate from the raw files. It never trusts the archived `status`, gate booleans, or
any caller-supplied success flag. Verdicts are reported per slot as PASS /
REJECTED (evidence violates a threshold) / EVIDENCE_INCOMPLETE (cannot be
proven). The command exits non-zero unless every slot is PASS.

Reads are read-only; the derived report is written to a NEW directory.

Usage:
  offline_accept.py --selection DIR --inputs INPUTS_SINGLE --scenario SC \
      --owner-hashes FILE --frozen-lib DIR [--out REPORT_DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_accept  # noqa: E402
import smoke_plan  # noqa: E402
import smoke_runner  # noqa: E402
import monitor_evidence
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "concurrency_finite_cache_v1"))
import evidence_archive


def verify_selection(root):
    """Verify covered bytes first, then the exact registered slot set/identity."""
    ok, problems = evidence_archive.verify(root, root / "evidence-manifest.txt")
    if not ok:
        return [], problems
    try:
        rows = json.loads((root / "selection.json").read_text())["slots"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("empty selection")
        names = [r["slot"] for r in rows]
        if any(not isinstance(n, str) or Path(n).name != n or n in (".", "..") for n in names):
            raise ValueError("invalid slot name")
        if len(set(names)) != len(names):
            raise ValueError("duplicate slot")
        actual = {p.name for p in (root / "slots").iterdir() if p.is_dir()}
        if actual != set(names):
            raise ValueError(f"slot_set_mismatch:missing={set(names)-actual},extra={actual-set(names)}")
        for row in rows:
            path = root / "slots" / row["slot"]
            result = json.loads((path / "result.json").read_text())
            plan = json.loads((path / "plan.json").read_text())
            for field in ("slot_id", "transport", "rate"):
                expected = row["slot"] if field == "slot_id" else row[field]
                if result.get(field) != expected or plan.get(field) != expected:
                    raise ValueError(f"slot_identity:{row['slot']}:{field}")
            if plan.get("plan_sha256") != row.get("plan_sha256"):
                raise ValueError(f"selection_plan_hash:{row['slot']}")
            if not row.get("run") or not re.fullmatch(r"attempt[0-9]+", row.get("attempt", "")):
                raise ValueError("missing run/attempt identity")
        return rows, []
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return [], [f"selection_invalid:{exc}"]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def derived_window(path):
    """Supplementary: setup/last-completion bounds in the container monotonic
    domain, derived from the run's own started/completions records. Reported as
    derived, never substituted for a sealed runtime record."""
    control = path / "evidence" / "control"
    starts, finishes = [], []
    for p in (control / "started").glob("*.tsv"):
        f = p.read_text(encoding="utf-8").strip().split("\t")
        if len(f) >= 3 and f[2].isdigit():
            starts.append(int(f[2]))
    for p in (control / "completions").glob("*.tsv"):
        f = p.read_text(encoding="utf-8").strip().split("\t")
        if len(f) >= 8 and f[7].isdigit():
            finishes.append(int(f[7]))
    return {"setup_start_mono_ns": min(starts) if starts else None,
            "last_completion_mono_ns": max(finishes) if finishes else None,
            "source": "derived from control/started/*.tsv:3 and "
                      "control/completions/*.tsv:8 (container CLOCK_MONOTONIC)",
            "exact_match_to_original_gate": False}


def monitor_series(path):
    series = {}
    for node_path in sorted(path.glob("node*.jsonl")):
        node = int(re.sub(r"\D", "", node_path.stem))
        rows = []
        for raw in node_path.read_text(encoding="utf-8", errors="replace").split("\n"):
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


def owner_evidence_from(path, hosts, owner_hashes):
    evidence = {}
    for node in range(1, hosts + 1):
        file = path / f"node{node}.tsv"
        expected = owner_hashes.get(str(node))
        if not file.is_file():
            evidence[node] = {"valid": False, "reason": "owner_input_missing"}
            continue
        lines = [l for l in file.read_text(encoding="utf-8").split("\n") if l.strip()]
        header = lines[0].split("\t") if lines else []
        keys = [l.split("\t")[0] for l in lines[1:]]
        if header[:4] != ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"]:
            evidence[node] = {"valid": False, "reason": "owner_schema_mismatch"}
            continue
        if len(keys) != len(set(keys)):
            evidence[node] = {"valid": False, "reason": "owner_duplicate_keys"}
            continue
        digest = sha256(file)
        if expected is not None and digest != expected:
            evidence[node] = {"valid": False, "reason": "owner_hash_mismatch"}
            continue
        evidence[node] = {"valid": True, "keys": keys, "sha256": digest,
                          "expected_sha256": expected, "source": f"data/node{node}.tsv"}
    return evidence


def verify_slot(slot_dir, inputs, scenario, hosts, owner_hashes, frozen_lib, registration=None):
    result = json.loads((slot_dir / "result.json").read_text(encoding="utf-8"))
    transport = result["transport"]
    plan = json.loads((slot_dir / "plan.json").read_text(encoding="utf-8"))
    setup, groups, digest = smoke_runner.schedule(inputs, scenario, hosts)
    planned = [{"index": str(r["request_index"]), "node": int(r["requester"]),
                "requester": str(r["requester"]), "owner": str(r["owner"]),
                "key": r["key"], "value_bytes": int(r["value_size_bytes"]),
                "expected_fnv1a64": r["expected_fnv1a64"]}
               for r in setup + [row for g in groups for row in g]]
    planned_indexes = {r["index"] for r in planned}
    plan_indexes = {i for g in plan["request_indexes"] for i in g} | set(plan["setup_indexes"])
    registered_indexes = set(plan_indexes)
    plan_ok, plan_reasons = smoke_plan.verify_plan(plan, frozen_lib)
    if plan_indexes != planned_indexes:
        plan_ok = False
        plan_reasons = list(plan_reasons) + ["reconstructed_indexes_differ"]

    control = slot_dir / "evidence" / "control"
    discovered, discovery_problems = smoke_accept.discover_completions(
        control / "completions", planned_indexes)
    audit_paths = sorted((control / "delivery-audit").glob("*.tsv"))

    trace_files, trace_problems = {}, []
    tcp_trace_files, tcp_trace_problems = {}, []
    traces = slot_dir / "evidence" / "traces"
    target = trace_files if transport == "nc" else tcp_trace_files
    problems = trace_problems if transport == "nc" else tcp_trace_problems
    for path in sorted(traces.glob("node*.jsonl")):
        node = int(re.sub(r"\D", "", path.stem))
        records, rec_problems = smoke_accept.parse_jsonl_strict(path)
        target[node] = records
        problems.extend(rec_problems)

    series = monitor_series(slot_dir / "monitor") if transport == "nc" else {}
    acc_path = slot_dir / "monitor-acceptance.json"
    monitor_acceptance = (json.loads(acc_path.read_text(encoding="utf-8"))
                          if acc_path.is_file() else None)
    monitor_problems = []
    if transport == "nc" and monitor_acceptance is not None:
        registration = registration or {}
        # The existing runner's run_id is the slot id, not the outer archive run.
        identity = {"run_id": slot_dir.name, "slot_id": slot_dir.name,
                    "attempt": registration.get("attempt", "attempt0")[7:],
                    "transport": transport}
        series, monitor_problems = monitor_evidence.load(slot_dir, monitor_acceptance, identity)
    owners = owner_evidence_from(inputs / "mapped" / scenario / "data", hosts,
                                 owner_hashes) if transport == "nc" else None

    acceptance = smoke_accept.accept_slot(
        transport=transport, hosts=hosts, planned=planned,
        completion_entries=[(i, control / "completions" / f"{i}.tsv")
                            for i in planned_indexes],
        discovered_by_index=discovered, discovery_problems=discovery_problems,
        audit_paths=audit_paths, trace_files=trace_files,
        trace_problems=trace_problems, tcp_trace_files=tcp_trace_files,
        tcp_trace_problems=tcp_trace_problems, monitor_acceptance=monitor_acceptance,
        monitor_series=series, owner_evidence=owners,
        expected_policy=result.get("cache_policy", ""),
        expected_budget=int(result.get("cache_budget", 0)),
        control=control, plan_ok=plan_ok, plan_reasons=plan_reasons,
        registered_indexes=registered_indexes)
    if monitor_problems:
        acceptance["verdict"] = "REJECTED"
        acceptance["gates"]["monitor_input_binding"] = {
            "state": "rejected", "detail": "; ".join(monitor_problems)}
    return {"slot": slot_dir.name, "transport": transport,
            "rate": result.get("rate"), "verdict": acceptance["verdict"],
            "gates": acceptance["gates"],
            "derived_window": derived_window(slot_dir) if transport == "nc" else None,
            "sealed_monitor_record": monitor_acceptance is not None}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selection", required=True)
    ap.add_argument("--inputs", required=True, help="frozen inputs/single directory")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--owner-hashes", required=True)
    ap.add_argument("--frozen-lib", required=True)
    ap.add_argument("--hosts", type=int, default=16)
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    selection = Path(args.selection)
    manifest = selection / "evidence-manifest.txt"
    if not (selection / "selection.json").is_file():
        print("REJECTED: selection.json missing", file=sys.stderr)
        return 2
    if not manifest.is_file():
        print("REJECTED: evidence manifest missing", file=sys.stderr)
        return 2
    owner_hashes = json.loads(Path(args.owner_hashes).read_text(encoding="utf-8"))
    hashes = owner_hashes.get("owner_hashes", {})

    registrations, problems = verify_selection(selection)
    if problems:
        print("REJECTED: " + "; ".join(problems[:8]), file=sys.stderr)
        return 2
    report = []
    for registration in registrations:
        slot_dir = selection / "slots" / registration["slot"]
        try:
            report.append(verify_slot(slot_dir, Path(args.inputs), args.scenario,
                                      args.hosts, hashes, Path(args.frozen_lib), registration))
        except Exception as exc:
            report.append({"slot": slot_dir.name, "verdict": "REJECTED",
                           "error": repr(exc), "gates": {}})

    out = {
        "validator_sha256": sha256(Path(__file__)),
        "smoke_accept_sha256": sha256(Path(smoke_accept.__file__)),
        "strict_audit_sha256": sha256(Path(smoke_accept.strict_audit.__file__)),
        "selection_manifest_sha256": sha256(manifest),
        "selection_json_sha256": sha256(selection / "selection.json"),
        "owner_provenance": {k: owner_hashes.get(k) for k in
                             ("campaign_fingerprint", "commit", "image_id")},
        "owner_inputs_dir": str(Path(args.inputs).parent),
        "slots": report,
    }
    text = json.dumps(out, indent=2)
    if args.out:
        out_dir = Path(args.out)
        if out_dir.exists():
            print(f"refusing to overwrite {out_dir}", file=sys.stderr)
            return 2
        out_dir.mkdir(parents=True)
        (out_dir / "offline-acceptance.json").write_text(text + "\n", encoding="utf-8")
    for row in report:
        print(f"{row['slot']}: {row['verdict']}"
              + ("" if row.get("gates") else f" error={row.get('error')}"))
    bad = [r for r in report if r["verdict"] != "PASS"]
    print(f"slots={len(report)} pass={len(report) - len(bad)} non_pass={len(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
