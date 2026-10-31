#!/usr/bin/env python3
"""Pilot acceptance and metrics.

Correctness gates mirror the accepted smoke rules (delivery, discovery, strict
traces, cache_config, replica replay, gauge replay, owner protection, sealed
monitor acceptance, no fatal/backend error). The pilot adds an explicit
`require_eviction` flag: only the small LRU tiers must show real evictions —
16 MiB and unlimited may legitimately never evict, and that is NOT a correctness
failure. A low-capacity tier showing no competition is a screening observation,
not a forced pass/fail.

Usage: pilot_accept.py --self-test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SMOKE = HERE.parent / "finite_cache_smoke"
if str(SMOKE) not in sys.path:
    sys.path.insert(0, str(SMOKE))
import smoke_accept  # noqa: E402
import strict_audit  # noqa: E402

NC_REQUIRED = [
    "delivery", "completion_discovery", "traces_strict", "expected_nodes",
    "cache_config", "cache_policy_effective", "replica_replay",
    "resident_within_budget", "gauge_replay", "owner_protected",
    "monitor_acceptance", "no_fatal", "no_backend_error",
]


def accept_slot(*, hosts, planned, completion_entries, discovered_by_index,
                discovery_problems, audit_paths, trace_files, trace_problems,
                monitor_acceptance, monitor_series, owner_evidence,
                expected_policy, expected_budget, require_eviction, control,
                plan_ok=True, plan_reasons=None, registered_indexes=None):
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
    backend_errors = [f"node{n}:{r.get('reason')}" for n, recs in trace_files.items()
                      for r in recs if r.get("event") == "cache_backend_error"]
    gate("no_backend_error", "rejected" if backend_errors else "pass",
         "; ".join(backend_errors[:4]))

    expected_nodes = set(range(1, hosts + 1))
    gate("traces_strict", "pass" if not trace_problems else "rejected",
         "; ".join(trace_problems[:4]))
    gate("expected_nodes", "pass" if set(trace_files) == expected_nodes else "rejected",
         f"traces={sorted(trace_files)}")

    configs = {n: [r for r in recs if r.get("event") == "cache_config"]
               for n, recs in trace_files.items()}
    modes = {c.get("budget_mode") for v in configs.values() for c in v}
    budgets = {c.get("budget_bytes") for v in configs.values() for c in v}
    gate("cache_config", "pass" if set(configs) == expected_nodes
         and all(len(v) == 1 for v in configs.values()) else "rejected",
         f"nodes={len(configs)} multi={[n for n, v in configs.items() if len(v) != 1]}")
    expected_budget_text = "unset" if expected_budget is None else str(expected_budget)
    if expected_policy == "unlimited":
        budget_ok = True  # unlimited carries no budget field
    else:
        budget_ok = budgets == {expected_budget_text}
    gate("cache_policy_effective", "pass" if modes == {expected_policy} and budget_ok
         else "rejected", f"modes={modes} budgets={budgets} want={expected_budget_text}")

    replay_problems, gauge_problems = [], []
    final_state, admits_total, evicts_total, bypass_total = {}, 0, 0, 0
    for node in sorted(trace_files):
        resident = {}
        resident_bytes = resident_objects = 0
        admits = evicts = 0
        last_op_mono, last_gauge = 0, None
        for rec in trace_files[node]:
            event = rec.get("event")
            mono = smoke_accept._req(rec.get("ts_mono_ns"))
            if event == "replica_bypass":
                bypass_total += 1
            if event in ("replica_admit", "replica_evict"):
                if mono is None:
                    replay_problems.append(f"node{node}:{event}:missing_time")
                    continue
                key = rec.get("key")
                size = smoke_accept._req(rec.get("value_bytes"))
                after_b = smoke_accept._req(rec.get("resident_bytes"))
                after_o = smoke_accept._req(rec.get("resident_objects"))
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
                if after_b != resident_bytes or after_o != resident_objects:
                    replay_problems.append(f"node{node}:{event}_snapshot_mismatch")
                if expected_budget is not None and resident_bytes > expected_budget:
                    replay_problems.append(f"node{node}:over_budget:{resident_bytes}")
            elif event == "cache_gauge":
                if mono is None:
                    gauge_problems.append(f"node{node}:gauge_missing_time")
                    continue
                if mono < last_op_mono:
                    gauge_problems.append(f"node{node}:gauge_before_last_op")
                if smoke_accept._req(rec.get("resident_bytes")) != resident_bytes:
                    gauge_problems.append(f"node{node}:gauge_bytes_mismatch")
                if smoke_accept._req(rec.get("resident_objects")) != resident_objects:
                    gauge_problems.append(f"node{node}:gauge_objects_mismatch")
                last_gauge = {"mono": mono, "bytes": resident_bytes,
                              "objects": resident_objects}
            elif event == "owner_evict":
                replay_problems.append(f"node{node}:owner_evict")
        final_state[node] = {"resident_bytes": resident_bytes,
                             "resident_objects": resident_objects,
                             "last_op_mono": last_op_mono, "last_gauge": last_gauge,
                             "admits": admits, "evicts": evicts}
        admits_total += admits
        evicts_total += evicts
    gate("replica_replay", "pass" if not replay_problems else "rejected",
         "; ".join(replay_problems[:4]) if replay_problems
         else f"admits={admits_total} evicts={evicts_total} bypasses={bypass_total}")

    budget_ok = (all(v["resident_bytes"] <= expected_budget
                     for v in final_state.values()) if expected_budget is not None
                 else all(v["resident_bytes"] >= 0 for v in final_state.values()))
    gate("resident_within_budget", "pass" if budget_ok else "rejected",
         f"final_bytes={ {n: v['resident_bytes'] for n, v in final_state.items()} } "
         f"budget={expected_budget}")

    if expected_policy == "bypass":
        gate("replica_zero_ops",
             "pass" if admits_total == 0 and evicts_total == 0
             and all(v["resident_bytes"] == 0 and v["resident_objects"] == 0
                     for v in final_state.values()) else "rejected",
             f"admits={admits_total} evicts={evicts_total}")
    elif require_eviction:
        gate("replica_evict_gt_0", "pass" if evicts_total > 0 else "rejected",
             f"evicts={evicts_total}")
    else:
        # large/unlimited tiers may legitimately never evict; this is recorded as
        # an observation, never as a correctness failure.
        gate("replica_evict_observed", "pass",
             f"evicts={evicts_total} (not required for this tier)")

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
        gate("monitor_acceptance", "incomplete", "no sealed monitor acceptance record")
    else:
        problems = []
        instances = monitor_acceptance.get("instances", {})
        baseline = monitor_acceptance.get("baseline", {})
        exit_codes = monitor_acceptance.get("exit_codes", {})
        window = monitor_acceptance.get("window") or {}
        if set(monitor_acceptance.get("expected_instances", [])) != expected_nodes:
            problems.append(f"expected_instances={monitor_acceptance.get('expected_instances')}")
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
            code = exit_codes.get(str(node), exit_codes.get(node))
            if code is None:
                problems.append(f"node{node}:no_exit_code")
            elif code != 0:
                problems.append(f"node{node}:monitor_exit={code}")
        mono = lambda k: smoke_accept._req(window.get(k))  # noqa: E731
        for field in ("setup_start_epoch_ns", "last_completion_epoch_ns"):
            if smoke_accept._req(window.get(field)) is None:
                problems.append(f"window_{field}_missing_or_invalid")
        for field in ("setup_start_mono_ns", "last_completion_mono_ns"):
            if mono(field) is None:
                problems.append(f"window_{field}_missing_or_invalid")
        if not problems:
            if window.get("mono_clock") != smoke_accept.MONO_CHAIN:
                problems.append(f"window_mono_clock={window.get('mono_clock')}")
            if mono("last_completion_mono_ns") < mono("setup_start_mono_ns"):
                problems.append("window_boundaries_reversed")
            if not (monitor_acceptance.get("barrier") or {}).get("executed"):
                problems.append("barrier_not_recorded")
            for node in sorted(expected_nodes):
                rows = monitor_series.get(node, [])
                if not rows:
                    problems.append(f"node{node}:no_raw_samples")
                    continue
                if smoke_accept._req(rows[0].get("ts_mono_ns")) is None or \
                        smoke_accept._req(rows[-1].get("ts_mono_ns")) is None:
                    problems.append(f"node{node}:bad_sample_time")
                    continue
                if smoke_accept._req(rows[0]["ts_mono_ns"]) > mono("setup_start_mono_ns"):
                    problems.append(f"node{node}:no_baseline_coverage")
                if smoke_accept._req(rows[-1]["ts_mono_ns"]) < mono("last_completion_mono_ns"):
                    problems.append(f"node{node}:no_tail_coverage")
                run_id, prev = None, None
                for rec in rows:
                    if rec.get("status") != "ok":
                        problems.append(f"node{node}:{rec.get('status')}")
                    value = smoke_accept._req(rec.get("ts_mono_ns"))
                    if value is None:
                        problems.append(f"node{node}:bad_sample_time")
                        continue
                    if prev is not None:
                        if value < prev:
                            problems.append(f"node{node}:clock_regression")
                        elif (value - prev) / 1e6 > smoke_accept.MONITOR_MAX_GAP_MS:
                            problems.append(f"node{node}:gap")
                    prev = value
                    used = smoke_accept._req(rec.get("used_memory"))
                    evicted = smoke_accept._req(rec.get("evicted_keys"))
                    if used is None or evicted is None:
                        problems.append(f"node{node}:bad_number")
                    elif evicted > 0:
                        problems.append(f"node{node}:redis_evicted={evicted}")
                    if run_id is None:
                        run_id = rec.get("redis_run_id")
                    elif rec.get("redis_run_id") != run_id:
                        problems.append(f"node{node}:restart")
        gate("monitor_acceptance", "pass" if not problems else "rejected",
             "; ".join(problems[:5]) if problems
             else f"instances={len(instances)} samples="
                  f"{sum(len(v) for v in monitor_series.values())}")

    missing = [name for name in NC_REQUIRED if name not in gates]
    gate("required_gates_present", "pass" if not missing else "rejected",
         "all present" if not missing else f"missing={missing}")
    summary = {"admits": admits_total, "evicts": evicts_total,
               "bypasses": bypass_total,
               "final_bytes": {n: v["resident_bytes"] for n, v in final_state.items()},
               "peak_bytes": {n: v["resident_bytes"] for n, v in final_state.items()}}
    return {"verdict": verdict_name(), "gates": gates, "cache_summary": summary}


def _self_test() -> int:
    import tempfile
    from pathlib import Path as P
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        print(f"{name}: {'PASS' if ok else 'FAIL'}{' ' + detail if detail and not ok else ''}")
        if not ok:
            failures += 1

    h = "0123456789abcdef"
    tmp = P(tempfile.mkdtemp(prefix="pilot-accept-"))
    comp = tmp / "completions"; comp.mkdir()
    audit = tmp / "delivery-audit"; audit.mkdir()
    row = "\t".join(["1", "1", "k1", "ok", "65536", h]) + "\n"
    (comp / "1.tsv").write_text(row, encoding="utf-8")
    (audit / "node1.tsv").write_text(row, encoding="utf-8")
    planned = [{"index": "1", "node": 1, "requester": "1", "owner": "2", "key": "k1",
                "value_bytes": 65536, "expected_fnv1a64": h}]
    series = {1: [{"node": 1, "status": "ok", "ts_mono_ns": 5, "ts_epoch_ns": 1000,
                   "used_memory": "1", "evicted_keys": "0", "redis_run_id": "r"},
                  {"node": 1, "status": "ok", "ts_mono_ns": 40, "ts_epoch_ns": 2000,
                   "used_memory": "1", "evicted_keys": "0", "redis_run_id": "r"}]}
    acc = {"expected_instances": [1],
           "instances": {"1": {"instance_identity": "node1", "redis_run_id": "r",
                               "raw_file": "monitor/node1.jsonl"}},
           "baseline": {"1": {"used_memory": "1"}}, "exit_codes": {"1": 0},
           "window": {"setup_start_epoch_ns": 1000, "last_completion_epoch_ns": 2000,
                      "setup_start_mono_ns": 5, "last_completion_mono_ns": 40,
                      "mono_clock": smoke_accept.MONO_CHAIN},
           "barrier": {"executed": True}}
    owners = {1: {"keys": ["own1"], "valid": True, "expected_sha256": "a" * 64}}

    def traces(policy="lru", budget="1048576", evicts=1):
        recs = [{"event": "cache_config", "budget_mode": policy, "budget_bytes": budget}]
        resident_b, resident_o = 0, 0
        for i in range(evicts):
            key = f"r{i}"
            resident_b += 65536; resident_o += 1
            recs.append({"event": "replica_admit", "key": key, "value_bytes": "65536",
                         "resident_bytes": str(resident_b),
                         "resident_objects": str(resident_o), "ts_mono_ns": 10 + 2 * i})
            resident_b -= 65536; resident_o -= 1
            recs.append({"event": "replica_evict", "key": key, "value_bytes": "65536",
                         "resident_bytes": str(resident_b),
                         "resident_objects": str(resident_o), "ts_mono_ns": 11 + 2 * i})
        recs.append({"event": "cache_gauge", "resident_bytes": str(resident_b),
                     "resident_objects": str(resident_o), "ts_mono_ns": 100})
        return {1: recs}

    def inv(**over):
        kw = dict(hosts=1, planned=planned,
                  completion_entries=[("1", comp / "1.tsv")],
                  discovered_by_index={"1": [comp / "1.tsv"]}, discovery_problems=[],
                  audit_paths=[audit / "node1.tsv"], trace_files=traces(),
                  trace_problems=[], monitor_acceptance=acc, monitor_series=series,
                  owner_evidence=owners, expected_policy="lru",
                  expected_budget=1048576, require_eviction=True,
                  control=tmp / "nocontrol")
        kw.update(over)
        return kw

    r = accept_slot(**inv())
    check("lru_positive", r["verdict"] == "PASS",
          str({k: v for k, v in r["gates"].items() if v["state"] != "pass"}))
    check("cache_summary", r["cache_summary"]["evicts"] == 1)

    # unlimited / 16MiB without eviction must NOT fail correctness
    r = accept_slot(**inv(trace_files=traces(policy="unlimited", budget="unset", evicts=0),
                          expected_policy="unlimited", expected_budget=None,
                          require_eviction=False))
    check("unlimited_without_eviction_pass", r["verdict"] == "PASS",
          str({k: v for k, v in r["gates"].items() if v["state"] != "pass"}))
    r = accept_slot(**inv(trace_files=traces(policy="lru", budget="16777216", evicts=0),
                          expected_budget=16777216, require_eviction=False))
    check("large_lru_without_eviction_pass", r["verdict"] == "PASS",
          str({k: v for k, v in r["gates"].items() if v["state"] != "pass"}))
    # the SAME evidence must fail when the tier requires real eviction
    r = accept_slot(**inv(trace_files=traces(policy="lru", budget="1048576", evicts=0),
                          require_eviction=True))
    check("small_lru_without_eviction_rejected", r["verdict"] == "REJECTED")

    r = accept_slot(**inv(trace_files=traces(policy="bypass", budget="0", evicts=0),
                          expected_policy="bypass", expected_budget=0,
                          require_eviction=False))
    check("bypass_positive", r["verdict"] == "PASS",
          str({k: v for k, v in r["gates"].items() if v["state"] != "pass"}))
    r = accept_slot(**inv(trace_files=traces(policy="bypass", budget="0", evicts=1),
                          expected_policy="bypass", expected_budget=0,
                          require_eviction=False))
    check("bypass_with_evict_rejected", r["verdict"] == "REJECTED")

    bad = traces(); bad[1][1]["resident_bytes"] = "1"
    check("snapshot_mismatch_rejected",
          accept_slot(**inv(trace_files=bad))["verdict"] == "REJECTED")
    check("no_monitor_record_incomplete",
          accept_slot(**inv(monitor_acceptance=None))["verdict"] == "EVIDENCE_INCOMPLETE")
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
    raise SystemExit(main(sys.argv[1:]))