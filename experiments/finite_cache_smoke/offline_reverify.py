#!/usr/bin/env python3
"""Offline re-verification of a completed smoke run with the CURRENT acceptance
rules. Reads the raw run directories (never writes to them) and reports, per
slot and per gate: whether the existing evidence is sufficient, the result, and
whether a rerun is needed.

Classifies monitor gates as "evidence insufficient" (not a protocol failure)
when the old evidence lacks a real monotonic clock / exit codes.

Usage: offline_reverify.py --run-dir RAW_SMOKE_DIR --inputs INPUTS_SINGLE \
           --scenario SC --hosts 16 [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_accept  # noqa: E402
import smoke_runner  # noqa: E402
import smoke_plan  # noqa: E402

SLOTS = [
    ("finite-smoke-n16-r32-nc-bypass0", "bypass", 0, "nc", 32),
    ("finite-smoke-n16-r32-nc-lru1m", "lru", 1048576, "nc", 32),
    ("finite-smoke-n16-r32-tcp-direct", "", 0, "tcp-direct", 32),
    ("finite-smoke-n16-r128-nc-bypass0", "bypass", 0, "nc", 128),
    ("finite-smoke-n16-r128-nc-lru1m", "lru", 1048576, "nc", 128),
    ("finite-smoke-n16-r128-tcp-direct", "", 0, "tcp-direct", 128),
]


def old_monitor_series(path: Path):
    """The s2 monitor format was `epoch|run_id:..;used_memory:..;evicted_keys:..`
    with a wall clock only and no per-sample monotonic time or exit code."""
    series = {}
    for node_path in sorted(path.glob("node*.txt")):
        node = int(re.sub(r"\D", "", node_path.stem))
        rows = []
        for raw in node_path.read_text(encoding="utf-8", errors="replace").split("\n"):
            if "|" not in raw:
                continue
            epoch, _, payload = raw.partition("|")
            fields = {}
            for part in payload.split(";"):
                if ":" in part:
                    k, _, v = part.partition(":")
                    fields[k.strip()] = v.strip()
            rows.append({"node": node, "status": "ok" if "run_id" in fields else "info_unreadable",
                         "ts_epoch_ns": epoch, "used_memory": fields.get("used_memory", ""),
                         "evicted_keys": fields.get("evicted_keys", ""),
                         "redis_run_id": fields.get("run_id", "")})
        series[node] = rows
    return series


def verify_slot(run_dir: Path, inputs: Path, scenario: str, hosts: int, spec):
    slot, policy, budget, transport, rate = spec
    attempt = run_dir / slot / "attempt001"
    plan = json.loads((attempt / "plan.json").read_text(encoding="utf-8"))
    setup, groups, _ = smoke_runner.schedule(inputs, scenario, hosts)
    planned = []
    for r in setup + [row for g in groups for row in g]:
        planned.append({"index": str(r["request_index"]), "node": int(r["requester"]),
                        "requester": str(r["requester"]), "owner": str(r["owner"]),
                        "key": r["key"], "value_bytes": int(r["value_size_bytes"]),
                        "expected_fnv1a64": r["expected_fnv1a64"]})
    planned_set = {r["index"] for r in planned}
    plan_set = {i for g in plan["request_indexes"] for i in g} | set(plan["setup_indexes"])
    evidence = attempt / "evidence"
    control = evidence / "control"
    completion_entries = [(r["index"], control / "completions" / f"{r['index']}.tsv")
                          for r in planned]
    audit_paths = sorted((control / "delivery-audit").glob("*.tsv"))

    trace_files, trace_problems = {}, []
    tcp_trace_files, tcp_trace_problems = {}, []
    traces = evidence / "traces"
    if transport == "nc":
        for path in sorted(traces.glob("node*.jsonl")):
            node = int(re.sub(r"\D", "", path.stem))
            records, problems = smoke_accept.parse_jsonl_strict(path)
            trace_files[node] = records
            trace_problems.extend(problems)
    else:
        for path in sorted(traces.glob("node*.jsonl")):
            node = int(re.sub(r"\D", "", path.stem))
            records, problems = smoke_accept.parse_jsonl_strict(path)
            tcp_trace_files[node] = records
            tcp_trace_problems.extend(problems)

    owner_keys_by_node = {}
    for node in range(1, hosts + 1):
        path = inputs / "mapped" / scenario / "data" / f"node{node}.tsv"
        keys = set()
        if path.is_file():
            lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
            keys = {l.split("\t")[0] for l in lines[1:]}
        owner_keys_by_node[node] = keys

    monitor_series, monitor_problems = {}, ["old_monitor_format_has_no_monotonic_clock"]
    if transport == "nc":
        monitor_series = old_monitor_series(evidence / "monitor")

    verdict = smoke_accept.accept_slot(
        transport=transport, hosts=hosts, planned=planned,
        completion_entries=completion_entries, audit_paths=audit_paths,
        trace_files=trace_files, trace_problems=trace_problems,
        tcp_trace_files=tcp_trace_files, tcp_trace_problems=tcp_trace_problems,
        monitor_series=monitor_series, monitor_exit={}, window=None,
        owner_keys_by_node=owner_keys_by_node, expected_policy=policy,
        expected_budget=budget, control=control)
    gates = verdict["gates"]
    failing = [k for k, v in gates.items() if not v["qualified"]]
    monitor_only = failing and set(failing) <= {"redis_monitor_all_instances"}
    return {
        "slot": slot, "transport": transport, "rate": rate,
        "plan_matches_reconstructed": plan_set == planned_set,
        "gates": {k: v["qualified"] for k, v in gates.items()},
        "failing_gates": failing,
        "evidence_sufficient": not failing,
        "needs_rerun": bool(failing),
        "rerun_reason": ("monitor evidence lacks real monotonic clock/exit codes"
                         if monitor_only else
                         "; ".join(f"{k}: {gates[k]['detail']}" for k in failing[:3])),
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--hosts", type=int, default=16)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    report = [verify_slot(Path(args.run_dir), Path(args.inputs), args.scenario,
                          args.hosts, spec) for spec in SLOTS]
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    for row in report:
        print(f"{row['slot']}: sufficient={row['evidence_sufficient']} "
              f"needs_rerun={row['needs_rerun']} reason={row['rerun_reason']}")
        print(f"  gates={row['gates']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))