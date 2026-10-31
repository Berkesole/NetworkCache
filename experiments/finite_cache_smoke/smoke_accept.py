#!/usr/bin/env python3
"""Strict per-slot acceptance for the finite-cache smoke (round 7).

Gate model: each gate is `pass` / `rejected` (evidence clearly violates the
threshold) / `incomplete` (cannot be proven). A slot verdict is REJECTED if any
gate is rejected, else EVIDENCE_INCOMPLETE if any gate is incomplete, else PASS.
Missing evidence is never reported as a protocol failure.

NC and TCP Direct are separated: TCP runs no Node cache, so it requires no cache
configuration, eviction or Redis monitoring (and no fabricated window/exit
codes).

Usage: smoke_accept.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re

import strict_audit

NC_REQUIRED = [
    "delivery", "completion_discovery", "traces_strict", "expected_nodes",
    "cache_config", "cache_policy_effective", "replica_replay",
    "resident_within_budget", "gauge_replay", "owner_protected",
    "monitor_acceptance", "no_fatal", "no_backend_error",
]
TCP_REQUIRED = [
    "delivery", "completion_discovery", "tcp_traces_strict", "tcp_evidence",
    "no_fatal", "no_backend_error",
]
MONITOR_MAX_GAP_MS = 600
MONO_CHAIN = "container_monotonic_ns"


def parse_jsonl_strict(path):
    import pathlib
    records, problems = [], []
    path = pathlib.Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return records, [f"unreadable:{path.name}:{exc}"]
    for lineno, raw in enumerate(text.split("\n"), 1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(f"corrupt:{path.name}:{lineno}:{exc.msg}")
            continue
        if not isinstance(rec, dict):
            problems.append(f"non_object:{path.name}:{lineno}")
            continue
        records.append(rec)
    return records, problems


def _req(value):
    """Legal non-negative integer: JSON int or digit string. Rejects bool,
    float, None, negative, and any other text (no loose int() coercion)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    return None


def discover_completions(completions_dir, planned_indexes):
    """Independently enumerate the ACTUAL completion files and compare with the
    plan. Extra/unknown/duplicate/filename-vs-row-index mismatches are rejected;
    unsupported files are rejected rather than skipped."""
    problems = []
    by_index = {}
    for path in sorted(completions_dir.glob("*")):
        if not path.is_file():
            continue
        if path.suffix != ".tsv":
            problems.append(f"unsupported_completion_file:{path.name}")
            continue
        stem = path.stem
        if not re.fullmatch(r"[0-9]+", stem):
            problems.append(f"unsupported_completion_name:{path.name}")
            continue
        by_index.setdefault(stem, []).append(path)
    expected = set(planned_indexes)
    for index in sorted(expected - set(by_index)):
        problems.append(f"completion_missing:{index}")
    for index in sorted(set(by_index) - expected):
        problems.append(f"unknown_completion_file:{index}")
    for index, paths in sorted(by_index.items()):
        if len(paths) > 1:
            problems.append(f"duplicate_completion_files:{index}:{len(paths)}")
    return by_index, problems


def _tcp_evidence(planned, tcp_trace_files):
    problems = []
    by_node = {}
    for node, records in tcp_trace_files.items():
        for rec in records:
            if rec.get("event") == "pilot_wire_send":
                by_node.setdefault(node, []).append(rec)
    for request in planned:
        index, key = request["index"], request["key"]
        requester, owner = int(request["requester"]), int(request["owner"])
        reqs = [r for r in by_node.get(requester, []) if r.get("app_request_id") == index]
        if not reqs:
            problems.append(f"tcp_request_missing:{index}")
        for rec in reqs:
            if rec.get("packet_type") != "REQUEST":
                problems.append(f"tcp_bad_packet_type:{index}")
            if rec.get("key") != key:
                problems.append(f"tcp_key_conflict:{index}")
        datas = [r for r in by_node.get(owner, []) if r.get("app_request_id") == index]
        if not datas:
            problems.append(f"tcp_data_missing:{index}:owner{owner}")
        for rec in datas:
            if rec.get("packet_type") != "DATA":
                problems.append(f"tcp_bad_data_type:{index}")
            if rec.get("key") != key:
                problems.append(f"tcp_data_key_conflict:{index}")
    return problems


def accept_slot(*, transport, hosts, planned, completion_entries,
                discovered_by_index, discovery_problems, audit_paths,
                trace_files, trace_problems, tcp_trace_files, tcp_trace_problems,
                monitor_acceptance, monitor_series, owner_evidence,
                expected_policy, expected_budget, control, plan_ok=True,
                plan_reasons=None, registered_indexes=None):
    gates = {}

    def gate(name, state, detail=""):
        gates[name] = {"state": state, "detail": str(detail)}

    def verdict_name():
        states = {g["state"] for g in gates.values()}
        if "rejected" in states:
            return "REJECTED"
        if "incomplete" in states:
            return "EVIDENCE_INCOMPLETE"
        return "PASS"

    expected_indexes = {r["index"] for r in planned}
    if registered_indexes is not None and set(registered_indexes) != expected_indexes:
        gate("plan_identity", "rejected",
             f"registered={len(registered_indexes)} expected={len(expected_indexes)}")
    elif not plan_ok:
        gate("plan_identity", "rejected", "; ".join(plan_reasons or []))

    disc_ok = not discovery_problems
    gate("completion_discovery", "pass" if disc_ok else "rejected",
         "; ".join(discovery_problems[:5]) if discovery_problems
         else f"files={len(discovered_by_index)} expected={len(expected_indexes)}")
    registered = {i for i, _ in completion_entries}
    if disc_ok and registered != expected_indexes:
        gate("completion_discovery", "rejected",
             f"registered={len(registered)} expected={len(expected_indexes)}")

    resolved = []
    for index in sorted(expected_indexes):
        paths = discovered_by_index.get(index, [])
        if len(paths) == 1:
            resolved.append((index, paths[0]))
    ok, problems, counts = strict_audit.check_delivery(planned, resolved, audit_paths)
    gate("delivery", "pass" if ok else "rejected",
         "; ".join(problems[:5]) if problems
         else f"completion={counts['completion_ok']} audit={counts['audit_ok']}")
    gate("no_fatal", "rejected" if list((control / "fatal").glob("*")) else "pass",
         str([p.name for p in (control / "fatal").glob("*")]))
    backend_errors = [f"node{n}:{r.get('reason')}"
                      for n, recs in list(trace_files.items()) + list(tcp_trace_files.items())
                      for r in recs if r.get("event") == "cache_backend_error"]
    gate("no_backend_error", "rejected" if backend_errors else "pass",
         "; ".join(backend_errors[:4]))

    if transport == "nc":
        expected_nodes = set(range(1, hosts + 1))
        gate("traces_strict", "pass" if not trace_problems else "rejected",
             "; ".join(trace_problems[:4]))
        gate("expected_nodes", "pass" if set(trace_files) == expected_nodes else "rejected",
             f"traces={sorted(trace_files)}")

        configs = {n: [r for r in recs if r.get("event") == "cache_config"]
                   for n, recs in trace_files.items()}
        modes = {c.get("budget_mode") for v in configs.values() for c in v}
        budgets = {c.get("budget_bytes") for v in configs.values() for c in v}
        gate("cache_config",
             "pass" if set(configs) == expected_nodes
             and all(len(v) == 1 for v in configs.values()) else "rejected",
             f"nodes={len(configs)} multi={[n for n, v in configs.items() if len(v) != 1]}")
        gate("cache_policy_effective",
             "pass" if modes == {expected_policy} and budgets == {str(expected_budget)}
             else "rejected", f"modes={modes} budgets={budgets}")

        replay_problems, gauge_problems = [], []
        final_state = {}
        admits_total = evicts_total = 0
        for node in sorted(trace_files):
            resident = {}
            resident_bytes = resident_objects = 0
            admits = evicts = 0
            last_op_mono = 0
            last_gauge = None
            for rec in trace_files[node]:
                event = rec.get("event")
                mono = _req(rec.get("ts_mono_ns"))
                if event in ("replica_admit", "replica_evict"):
                    if mono is None:
                        replay_problems.append(f"node{node}:{event}:missing_time")
                        continue
                    key = rec.get("key")
                    size = _req(rec.get("value_bytes"))
                    after_b = _req(rec.get("resident_bytes"))
                    after_o = _req(rec.get("resident_objects"))
                    if not key or size is None or after_b is None or after_o is None:
                        replay_problems.append(f"node{node}:{event}:missing_field")
                        continue
                    if mono < last_op_mono:
                        replay_problems.append(f"node{node}:operation_time_regression")
                    last_op_mono = max(last_op_mono, mono)
                    if event == "replica_admit":
                        if key in resident:
                            replay_problems.append(f"node{node}:double_admit:{key}")
                        resident[key] = size
                        resident_bytes += size
                        resident_objects += 1
                        admits += 1
                    else:
                        if key not in resident:
                            replay_problems.append(f"node{node}:phantom_evict:{key}")
                        else:
                            if resident[key] != size:
                                replay_problems.append(f"node{node}:evict_wrong_size:{key}")
                            resident_bytes -= resident[key]
                            resident_objects -= 1
                            del resident[key]
                        evicts += 1
                    if resident_bytes < 0 or resident_objects < 0:
                        replay_problems.append(f"node{node}:negative_state")
                    if after_b != resident_bytes:
                        replay_problems.append(
                            f"node{node}:{event}_snapshot_bytes:{after_b}!={resident_bytes}")
                    if after_o != resident_objects:
                        replay_problems.append(
                            f"node{node}:{event}_snapshot_objects:{after_o}!={resident_objects}")
                    if expected_budget and resident_bytes > expected_budget:
                        replay_problems.append(f"node{node}:over_budget:{resident_bytes}")
                elif event == "cache_gauge":
                    if mono is None:
                        gauge_problems.append(f"node{node}:gauge_missing_time")
                        continue
                    if mono < last_op_mono:
                        gauge_problems.append(f"node{node}:gauge_before_last_op")
                    if _req(rec.get("resident_bytes")) != resident_bytes:
                        gauge_problems.append(f"node{node}:gauge_bytes_mismatch")
                    if _req(rec.get("resident_objects")) != resident_objects:
                        gauge_problems.append(f"node{node}:gauge_objects_mismatch")
                    last_gauge = {"mono": mono, "bytes": resident_bytes,
                                  "objects": resident_objects}
                elif event == "owner_evict":
                    replay_problems.append(f"node{node}:owner_evict")
            final_state[node] = {"resident_bytes": resident_bytes,
                                 "resident_objects": resident_objects,
                                 "last_op_mono": last_op_mono, "last_gauge": last_gauge}
            admits_total += admits
            evicts_total += evicts
        gate("replica_replay", "pass" if not replay_problems else "rejected",
             "; ".join(replay_problems[:4]) if replay_problems
             else f"admits={admits_total} evicts={evicts_total}")
        gate("resident_within_budget",
             "pass" if (all(v["resident_bytes"] <= expected_budget
                            for v in final_state.values()) if expected_budget
                        else all(v["resident_bytes"] == 0 for v in final_state.values()))
             else "rejected",
             f"final={ {n: v['resident_bytes'] for n, v in final_state.items()} } "
             f"budget={expected_budget}")
        if expected_policy == "bypass":
            gate("replica_zero_ops",
                 "pass" if admits_total == 0 and evicts_total == 0
                 and all(v["resident_bytes"] == 0 and v["resident_objects"] == 0
                         for v in final_state.values()) else "rejected",
                 f"admits={admits_total} evicts={evicts_total}")
        else:
            gate("replica_evict_gt_0",
                 "pass" if evicts_total > 0 else "rejected", f"evicts={evicts_total}")

        final_problems = []
        for node, state in final_state.items():
            g = state["last_gauge"]
            if g is None:
                final_problems.append(f"node{node}:no_gauge")
                continue
            if g["mono"] < state["last_op_mono"]:
                final_problems.append(f"node{node}:gauge_before_last_op")
            if g["bytes"] != state["resident_bytes"]:
                final_problems.append(f"node{node}:gauge_bytes")
            if g["objects"] != state["resident_objects"]:
                final_problems.append(f"node{node}:gauge_objects")
        gate("gauge_replay", "pass" if not gauge_problems and not final_problems
             else "rejected", "; ".join((gauge_problems + final_problems)[:4]))

        if owner_evidence is None:
            gate("owner_protected", "incomplete", "owner evidence not supplied")
        else:
            owner_problems, owner_incomplete = [], []
            for node in sorted(expected_nodes):
                evidence = owner_evidence.get(node)
                if evidence is None:
                    owner_problems.append(f"node{node}:owner_input_missing")
                    continue
                if not evidence.get("valid"):
                    owner_problems.append(f"node{node}:{evidence.get('reason', 'invalid')}")
                    continue
                if evidence.get("expected_sha256") is None:
                    owner_incomplete.append(f"node{node}:no_frozen_hash")
                keys = set(evidence.get("keys", []))
                for rec in trace_files.get(node, []):
                    if rec.get("event") in ("replica_admit", "replica_evict") \
                            and rec.get("key") in keys:
                        owner_problems.append(f"node{node}:own_owner_key:{rec.get('key')}")
            if owner_problems:
                gate("owner_protected", "rejected", "; ".join(owner_problems[:4]))
            elif owner_incomplete:
                gate("owner_protected", "incomplete", "; ".join(owner_incomplete[:4]))
            else:
                gate("owner_protected", "pass", f"nodes={len(owner_evidence)}")

        if monitor_acceptance is None:
            gate("monitor_acceptance", "incomplete",
                 "no sealed monitor acceptance record (round<7 evidence)")
        else:
            problems = []
            expected_instances = monitor_acceptance.get("expected_instances", [])
            if set(expected_instances) != expected_nodes:
                problems.append(f"expected_instances={expected_instances}")
            instances = monitor_acceptance.get("instances", {})
            baseline = monitor_acceptance.get("baseline", {})
            exit_codes = monitor_acceptance.get("exit_codes", {})
            window = monitor_acceptance.get("window") or {}
            for node in sorted(expected_nodes):
                info = instances.get(str(node))
                if info is None:
                    problems.append(f"node{node}:instance_missing")
                    continue
                if info.get("instance_identity") != f"node{node}":
                    problems.append(f"node{node}:identity_mismatch")
                if not info.get("redis_run_id"):
                    problems.append(f"node{node}:no_run_id")
                if not info.get("raw_file"):
                    problems.append(f"node{node}:no_raw_sample_ref")
                if str(node) not in baseline and node not in baseline:
                    problems.append(f"node{node}:no_baseline")
                else:
                    base = baseline.get(str(node), baseline.get(node))
                    if (not isinstance(base, dict) or base.get("status") != "ok"
                            or _req(base.get("used_memory")) is None
                            or _req(base.get("evicted_keys")) != 0
                            or not base.get("redis_run_id")
                            or base.get("redis_run_id") != info.get("redis_run_id")
                            or not monitor_series.get(node)
                            or base != monitor_series[node][0]):
                        problems.append(f"node{node}:invalid_baseline")
                code = exit_codes.get(str(node), exit_codes.get(node))
                if code is None:
                    problems.append(f"node{node}:no_exit_code")
                elif type(code) is not int or code != 0:
                    problems.append(f"node{node}:monitor_exit={code}")
            for field in ("setup_start_epoch_ns", "last_completion_epoch_ns"):
                if _req(window.get(field)) is None:
                    problems.append(f"window_{field}_missing_or_invalid")
            if _req(window.get("setup_start_mono_ns")) is None:
                problems.append("window_setup_start_mono_missing_or_invalid")
            if _req(window.get("last_completion_mono_ns")) is None:
                problems.append("window_last_completion_mono_missing_or_invalid")
            if not problems:
                if window.get("mono_clock") != MONO_CHAIN:
                    problems.append(f"window_mono_clock={window.get('mono_clock')}")
                if window["last_completion_mono_ns"] < window["setup_start_mono_ns"]:
                    problems.append("window_boundaries_reversed")
                if not (monitor_acceptance.get("barrier") or {}).get("executed"):
                    problems.append("barrier_not_recorded")
                for node in sorted(expected_nodes):
                    rows = monitor_series.get(node, [])
                    if not rows:
                        problems.append(f"node{node}:no_raw_samples")
                        continue
                    first_mono = _req(rows[0].get("ts_mono_ns"))
                    last_mono = _req(rows[-1].get("ts_mono_ns"))
                    if first_mono is None or last_mono is None:
                        problems.append(f"node{node}:bad_sample_time")
                        continue
                    if first_mono > window["setup_start_mono_ns"]:
                        problems.append(f"node{node}:no_baseline_coverage")
                    if last_mono < window["last_completion_mono_ns"]:
                        problems.append(f"node{node}:no_tail_coverage")
                    run_id = None
                    prev = None
                    for rec in rows:
                        if (not rec.get("redis_run_id") or rec.get("redis_run_id")
                                != instances[str(node)].get("redis_run_id")):
                            problems.append(f"node{node}:sample_run_id_mismatch")
                        if rec.get("status") != "ok":
                            problems.append(f"node{node}:{rec.get('status')}")
                        mono = _req(rec.get("ts_mono_ns"))
                        if mono is None:
                            problems.append(f"node{node}:bad_sample_time")
                            continue
                        if prev is not None:
                            if mono < prev:
                                problems.append(f"node{node}:clock_regression")
                            elif (mono - prev) / 1e6 > MONITOR_MAX_GAP_MS:
                                problems.append(f"node{node}:gap")
                        prev = mono
                        if _req(rec.get("used_memory")) is None or \
                                _req(rec.get("evicted_keys")) is None:
                            problems.append(f"node{node}:bad_number")
                        elif _req(rec.get("evicted_keys")) > 0:
                            problems.append(f"node{node}:evicted")
                        if run_id is None:
                            run_id = rec.get("redis_run_id")
                        elif rec.get("redis_run_id") != run_id:
                            problems.append(f"node{node}:restart")
            gate("monitor_acceptance", "pass" if not problems else "rejected",
                 "; ".join(problems[:5]) if problems
                 else f"instances={len(instances)} samples="
                      f"{sum(len(v) for v in monitor_series.values())}")
    else:
        gate("tcp_traces_strict",
             "pass" if not tcp_trace_problems else "rejected",
             "; ".join(tcp_trace_problems[:4]))
        problems = _tcp_evidence(planned, tcp_trace_files)
        gate("tcp_evidence", "pass" if not problems else "rejected",
             "; ".join(problems[:4]) if problems else f"nodes={len(tcp_trace_files)}")

    required = NC_REQUIRED if transport == "nc" else TCP_REQUIRED
    missing = [name for name in required if name not in gates]
    gate("required_gates_present", "pass" if not missing else "rejected",
         "all present" if not missing else f"missing={missing}")
    return {"verdict": verdict_name(), "gates": gates}


def _self_test() -> int:
    import tempfile
    from pathlib import Path
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        print(f"{name}: {'PASS' if ok else 'FAIL'}{' ' + detail if detail and not ok else ''}")
        if not ok:
            failures += 1

    h = "0123456789abcdef"
    planned = [{"index": "1", "node": 1, "requester": "1", "owner": "2", "key": "k1",
                "value_bytes": 65536, "expected_fnv1a64": h}]

    tmp = Path(tempfile.mkdtemp(prefix="smoke-accept7-"))
    comp_dir = tmp / "completions"; comp_dir.mkdir()
    audit_dir = tmp / "delivery-audit"; audit_dir.mkdir()
    row = "\t".join(["1", "1", "k1", "ok", "65536", h]) + "\n"
    (comp_dir / "1.tsv").write_text(row, encoding="utf-8")
    (audit_dir / "node1.tsv").write_text(row, encoding="utf-8")

    def traces(over=None):
        recs = [{"event": "cache_config", "budget_mode": "lru", "budget_bytes": "1048576"},
                {"event": "replica_admit", "key": "r1", "value_bytes": "65536",
                 "resident_bytes": "65536", "resident_objects": "1", "ts_mono_ns": 10},
                {"event": "cache_gauge", "resident_bytes": "65536",
                 "resident_objects": "1", "ts_mono_ns": 15},
                {"event": "replica_evict", "key": "r1", "value_bytes": "65536",
                 "resident_bytes": "0", "resident_objects": "0", "ts_mono_ns": 20},
                {"event": "cache_gauge", "resident_bytes": "0",
                 "resident_objects": "0", "ts_mono_ns": 30}]
        if over:
            recs = over(recs)
        return {1: recs}

    def monitor_acc(over=None):
        acc = {
            "expected_instances": [1],
            "instances": {"1": {"instance_identity": "node1", "redis_run_id": "r",
                                "raw_file": "monitor/node1.jsonl"}},
            "baseline": {"1": dict(series[1][0])},
            "exit_codes": {"1": 0},
            "window": {"setup_start_epoch_ns": 1000, "last_completion_epoch_ns": 2000,
                       "setup_start_mono_ns": 5, "last_completion_mono_ns": 40,
                       "mono_clock": MONO_CHAIN, "source": "runner"},
            "barrier": {"executed": True, "instances": [1]},
        }
        if over:
            acc = over(acc)
        return acc

    series = {1: [{"node": 1, "status": "ok", "ts_mono_ns": 5, "ts_epoch_ns": 1000,
                   "used_memory": "1", "evicted_keys": "0", "redis_run_id": "r"},
                  {"node": 1, "status": "ok", "ts_mono_ns": 40, "ts_epoch_ns": 2000,
                   "used_memory": "1", "evicted_keys": "0", "redis_run_id": "r"}]}
    owner_ok = {1: {"keys": ["own1"], "valid": True, "expected_sha256": "a" * 64}}

    def inv(**over):
        kw = dict(transport="nc", hosts=1, planned=planned,
                  completion_entries=[("1", comp_dir / "1.tsv")],
                  discovered_by_index={"1": [comp_dir / "1.tsv"]},
                  discovery_problems=[], audit_paths=[audit_dir / "node1.tsv"],
                  trace_files=traces(), trace_problems=[], tcp_trace_files={},
                  tcp_trace_problems=[], monitor_acceptance=monitor_acc(),
                  monitor_series=series, owner_evidence=owner_ok,
                  expected_policy="lru", expected_budget=1048576,
                  control=tmp / "nocontrol")
        kw.update(over)
        return kw

    r = accept_slot(**inv())
    check("nc_positive", r["verdict"] == "PASS",
          str({k: v for k, v in r["gates"].items() if v["state"] != "pass"}))

    check("nc_no_monitor_record_incomplete",
          accept_slot(**inv(monitor_acceptance=None))["verdict"] == "EVIDENCE_INCOMPLETE")
    check("nc_empty_window_rejected",
          accept_slot(**inv(monitor_acceptance=monitor_acc(
              lambda a: dict(a, window={}))))["verdict"] == "REJECTED")
    check("nc_reversed_window_rejected",
          accept_slot(**inv(monitor_acceptance=monitor_acc(
              lambda a: dict(a, window=dict(a["window"], last_completion_mono_ns=1)))))
          ["verdict"] == "REJECTED")
    check("nc_missing_exit_code_rejected",
          accept_slot(**inv(monitor_acceptance=monitor_acc(
              lambda a: dict(a, exit_codes={}))))["verdict"] == "REJECTED")
    check("nc_identity_mismatch_rejected",
          accept_slot(**inv(monitor_acceptance=monitor_acc(
              lambda a: dict(a, instances={"1": {"instance_identity": "node9",
                                                 "redis_run_id": "r",
                                                 "raw_file": "x"}}))))["verdict"]
          == "REJECTED")
    check("nc_missing_baseline_rejected",
          accept_slot(**inv(monitor_acceptance=monitor_acc(
              lambda a: dict(a, baseline={}))))["verdict"] == "REJECTED")

    def gauge_bytes_bad(recs):
        recs = [dict(x) for x in recs]
        recs[2]["resident_bytes"] = "1"   # middle cache_gauge
        return recs
    check("mid_gauge_bytes_rejected",
          accept_slot(**inv(trace_files=traces(gauge_bytes_bad)))["verdict"] == "REJECTED")

    def gauge_objects_bad(recs):
        recs = [dict(x) for x in recs]
        recs[2]["resident_objects"] = "9"  # middle cache_gauge
        return recs
    check("mid_gauge_objects_rejected",
          accept_slot(**inv(trace_files=traces(gauge_objects_bad)))["verdict"] == "REJECTED")

    def final_objects_bad(recs):
        recs = [dict(x) for x in recs]
        recs[-1]["resident_objects"] = "999"
        return recs
    check("final_gauge_objects_rejected",
          accept_slot(**inv(trace_files=traces(final_objects_bad)))["verdict"] == "REJECTED")

    def op_time_missing(recs):
        recs = [dict(x) for x in recs]
        del recs[3]["ts_mono_ns"]
        return recs
    check("op_missing_time_rejected",
          accept_slot(**inv(trace_files=traces(op_time_missing)))["verdict"] == "REJECTED")

    def gauge_before_op(recs):
        recs = [dict(x) for x in recs]
        recs[-1]["ts_mono_ns"] = 1
        return recs
    check("final_gauge_before_op_rejected",
          accept_slot(**inv(trace_files=traces(gauge_before_op)))["verdict"] == "REJECTED")

    def gauge_field_missing(recs):
        recs = [dict(x) for x in recs]
        recs[2].pop("resident_bytes", None)  # middle cache_gauge
        return recs
    check("gauge_field_missing_rejected",
          accept_slot(**inv(trace_files=traces(gauge_field_missing)))["verdict"]
          == "REJECTED")

    def bypass_traces(recs):
        return [{"event": "cache_config", "budget_mode": "bypass", "budget_bytes": "0"},
                {"event": "cache_gauge", "resident_bytes": "0",
                 "resident_objects": "0", "ts_mono_ns": 10}]
    r = accept_slot(**inv(trace_files=traces(bypass_traces), expected_policy="bypass",
                          expected_budget=0))
    check("bypass_zero_positive", r["verdict"] == "PASS",
          str({k: v for k, v in r["gates"].items() if v["state"] != "pass"}))

    check("owner_missing_node_rejected",
          accept_slot(**inv(owner_evidence={}))["verdict"] == "REJECTED")
    check("owner_invalid_rejected",
          accept_slot(**inv(owner_evidence={1: {"keys": [], "valid": False,
                                                "reason": "corrupt"}}))["verdict"]
          == "REJECTED")
    check("owner_without_frozen_hash_incomplete",
          accept_slot(**inv(owner_evidence={1: {"keys": ["own1"], "valid": True,
                                                "expected_sha256": None}}))["verdict"]
          == "EVIDENCE_INCOMPLETE")
    check("owner_set_missing_incomplete",
          accept_slot(**inv(owner_evidence=None))["verdict"] == "EVIDENCE_INCOMPLETE")

    check("extra_completion_rejected",
          accept_slot(**inv(discovered_by_index={"1": [comp_dir / "1.tsv"],
                                                 "999": [tmp / "999.tsv"]},
                            discovery_problems=["unknown_completion_file:999"]))["verdict"]
          == "REJECTED")
    check("missing_completion_rejected",
          accept_slot(**inv(discovered_by_index={},
                            discovery_problems=["completion_missing:1"]))["verdict"]
          == "REJECTED")

    def tcp_traces(over=None):
        recs = {1: [{"event": "pilot_wire_send", "packet_type": "REQUEST",
                     "app_request_id": "1", "key": "k1", "node": 1}],
                2: [{"event": "pilot_wire_send", "packet_type": "DATA",
                     "app_request_id": "1", "key": "k1", "node": 2}]}
        if over:
            over(recs)
        return recs
    r = accept_slot(**inv(transport="tcp-direct", hosts=1, trace_files={},
                          tcp_trace_files=tcp_traces(), monitor_acceptance=None,
                          monitor_series={}, owner_evidence=None,
                          expected_policy="", expected_budget=0))
    check("tcp_positive_no_cache_or_monitor", r["verdict"] == "PASS",
          str({k: v for k, v in r["gates"].items() if v["state"] != "pass"}))
    r = accept_slot(**inv(transport="tcp-direct", hosts=1, trace_files={},
                          tcp_trace_files={}, monitor_acceptance=None,
                          monitor_series={}, owner_evidence=None,
                          expected_policy="", expected_budget=0))
    check("tcp_missing_transport_rejected", r["verdict"] == "REJECTED")

    r = accept_slot(**inv(plan_ok=False, plan_reasons=["plan_arrival_not_frozen_generator"]))
    check("plan_mismatch_rejected", r["verdict"] == "REJECTED")
    return 0 if failures == 0 else 1


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    ap.error("choose --self-test")
    return 3


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
