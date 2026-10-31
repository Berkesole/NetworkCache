#!/usr/bin/env python3
"""Capacity-screening pilot runner (32-node Zipf, fixed rate).

Reuses the accepted smoke machinery (lab-scoped container selection, process
health, per-instance Redis monitor with a startup barrier, sealed monitor
acceptance record) and the frozen controller/network_backend, but runs the FULL
frozen schedule (32 setup + 4096 measurement) with the capacity policy/budget the
matrix assigns to the slot.

Phases:
  calibration  seed001 x {0.25 MiB, unlimited} length calibration
  screening    one matrix slot

Usage:
  pilot_runner.py --root ROOT --inputs INPUTS --image IMG --matrix pilot_matrix.json \
      --plan-file pilot_matrix.json --plan-md CAPACITY_SCREENING_PLAN.md \
      --owner-hashes FILE --slot-id ID --phase screening --attempt 001 --execute
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SMOKE = HERE.parent / "finite_cache_smoke"
for path in (str(HERE), str(SMOKE)):
    if path not in sys.path:
        sys.path.insert(0, path)
import smoke_runner  # noqa: E402  (lab scoping, monitor, health, env)
import smoke_plan  # noqa: E402
import pilot_accept  # noqa: E402
import pilot_metrics  # noqa: E402
import pilot_plan  # noqa: E402

FROZEN_LIB = "experiments/concurrency_slides_v5"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--inputs", default="")
    ap.add_argument("--image", default="")
    ap.add_argument("--matrix", default="")
    ap.add_argument("--plan-md", default="")
    ap.add_argument("--owner-hashes", default="")
    ap.add_argument("--slot-id", default="")
    ap.add_argument("--tier", default="")
    ap.add_argument("--phase", default="screening", choices=("calibration", "screening"))
    ap.add_argument("--attempt", default="001")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return smoke_runner._scope_self_test()
    if not args.execute:
        raise SystemExit("--execute required")
    for name in ("root", "inputs", "image", "matrix", "plan_md", "owner_hashes", "slot_id"):
        if not getattr(args, name):
            raise SystemExit(f"--{name.replace('_', '-')} required")

    root = Path(args.root)
    frozen_lib = root / "source" / FROZEN_LIB

    matrix, problems = pilot_plan.load_matrix(args.matrix, args.plan_md)
    if problems:
        raise SystemExit("refusing to start: matrix invalid: " + "; ".join(problems))
    if args.phase == "calibration":
        # calibration uses the matrix TIERS and SEEDS but its own (seed001,
        # tier) selection; it never enters the screening order.
        tier = next((t for t in matrix["tiers"] if t["name"] == args.tier), None)
        if tier is None:
            raise SystemExit(f"tier {args.tier} not in the frozen matrix")
        calib = matrix["calibration"]
        seed = calib["seed"]
        slot = {"slot_id": args.slot_id, "tier": tier["name"], "policy": tier["policy"],
                "budget_bytes": tier["budget_bytes"], "seed": seed,
                "block": "calibration"}
        if tier["name"] not in calib["tiers"]:
            raise SystemExit(f"tier {tier['name']} is not a calibration tier")
    else:
        slot = next((s for s in matrix["order"] if s["slot_id"] == args.slot_id), None)
        if slot is None:
            raise SystemExit(f"slot {args.slot_id} not in the frozen matrix")
    seed = slot["seed"]
    scenario = matrix["scenario_template"].format(seed=seed)
    hosts = matrix["hosts"]
    rate = matrix["rate"]
    arrival_seed = 9172 + seed

    lab = root / "lab" / args.phase / args.slot_id / f"attempt{args.attempt}"
    # smoke_runner.build_env / the renderer / the monitor script all use the
    # module-level NS, so the pilot must use the SAME namespace directory.
    ns_dir = lab / "shared" / smoke_runner.NS
    control = ns_dir / "control"
    data_dir = ns_dir / "data"
    raw = root / "raw" / args.phase / args.slot_id / f"attempt{args.attempt}"
    if raw.exists():
        raise SystemExit("refusing to overwrite existing attempt: " + str(raw))
    raw.mkdir(parents=True)

    inputs_single = Path(args.inputs) / "single" if (Path(args.inputs) / "single").is_dir() \
        else Path(args.inputs)
    setup, measurement, schedule_digest = pilot_plan.full_schedule(inputs_single, scenario, hosts)
    if len(measurement) != matrix["measurement_requests"] or len(setup) != matrix["setup_requests"]:
        raise SystemExit("schedule does not match the frozen matrix counts")
    plan = pilot_plan.build_plan(frozen_lib, [slot], setup, measurement, rate,
                                 model=matrix["model"], arrival_seed=arrival_seed,
                                 warmup_fraction=matrix["warmup_fraction"])
    plan_problems = pilot_plan.verify_plan(plan, frozen_lib)
    if plan_problems:
        raise SystemExit("refusing to start: plan invalid: " + "; ".join(plan_problems))
    smoke_runner.write_json(raw / "plan.json", plan)
    loaded = json.loads((raw / "plan.json").read_text(encoding="utf-8"))
    if pilot_plan.verify_plan(loaded, frozen_lib) or loaded["plan_sha256"] != plan["plan_sha256"]:
        raise SystemExit("refusing to start: reloaded plan mismatch")

    planned = [{"index": str(r["request_index"]), "node": int(r["requester"]),
                "requester": str(r["requester"]), "owner": str(r["owner"]),
                "key": r["key"], "value_bytes": int(r["value_size_bytes"]),
                "expected_fnv1a64": r["expected_fnv1a64"]}
               for r in setup + measurement]
    planned_indexes = {r["index"] for r in planned}

    ns = argparse.Namespace(root=str(root), inputs=str(inputs_single), image=args.image,
                            scenario=scenario, transport="nc", hosts=hosts,
                            slot_id=args.slot_id, cache_policy=slot["policy"],
                            cache_budget=slot["budget_bytes"] if slot["budget_bytes"]
                            is not None else 0)
    smoke_runner.prepare(ns, lab, control, data_dir)
    digest = schedule_digest
    env = smoke_runner.build_env(ns, lab, control, data_dir, digest)
    if slot["policy"] == "unlimited":
        env.pop("RSYNC_REPLICA_BUDGET_BYTES", None)   # unlimited carries no budget
    smoke_runner.write_json(raw / "environment.json",
                            {k: v for k, v in env.items() if k.startswith("RSYNC_")})
    smoke_runner.write_json(raw / "provenance.json", {
        "slot_id": args.slot_id, "phase": args.phase, "attempt": args.attempt,
        "run_root": str(root), "seed": seed, "scenario": scenario,
        "scenario_file": f"inputs/single/scenarios/{scenario}.yaml",
        "matrix_sha256": matrix["matrix_sha256"], "plan_file_sha256": plan["plan_sha256"],
        "schedule_digest": digest, "policy": slot["policy"],
        "budget_bytes": slot["budget_bytes"], "rate": rate,
        "arrival_seed": arrival_seed, "image_tag": matrix["image_tag"],
        "image_id": matrix["image_id"]})

    result = {"slot_id": args.slot_id, "phase": args.phase, "attempt": args.attempt,
              "seed": seed, "policy": slot["policy"], "budget_bytes": slot["budget_bytes"],
              "rate": rate, "status": "infrastructure_failure"}

    begin = time.monotonic()
    with (raw / "driver.log").open("w") as handle:
        proc = smoke_runner.subprocess.run(
            [str(root / "source" / "scripts" / "kathara" / "run_ruikekkk_scenario.sh"),
             scenario], env=env, stdout=handle, stderr=smoke_runner.subprocess.STDOUT,
            timeout=1200)
    result["render_exit"] = proc.returncode
    if proc.returncode != 0:
        smoke_runner.write_json(raw / "result.json", result)
        raise SystemExit(f"scenario start failed rc={proc.returncode}")
    result["startup_seconds"] = round(time.monotonic() - begin, 1)
    smoke_runner.wait_ready(control, hosts)

    container_ids, name_by_node, _ = smoke_runner.lab_containers(lab, hosts)
    inspected = json.loads(smoke_runner.out(["docker", "inspect", *container_ids]))
    sys.path.insert(0, str(root / "source" / FROZEN_LIB))
    import controller as ctl
    import network_backend
    network_backend.prepare_lab(raw, inspected)
    network_backend.verify_lab(root, lab, raw, inspected)

    monitor = smoke_runner.MultiRedisMonitor(name_by_node, raw / "monitor", ns_dir)
    monitor.start()
    baselines = monitor.baseline_barrier(hosts)
    smoke_runner.write_json(raw / "monitor-baseline.json",
                            {str(n): baselines[n] for n in sorted(baselines)})
    health = smoke_runner.ProcessHealth(ctl, "nc", name_by_node, [], raw)

    ledger, audit_ok = [], False
    window = {"setup_start_epoch_ns": time.time_ns(), "last_completion_epoch_ns": 0}
    try:
        res, rows = ctl.execute(setup, control, raw / "setup", "nc", "setup",
                                arrival=list(plan["setup_arrival_ns"]), group="setup",
                                health=health, nodes=hosts)
        ledger += rows
        if res["status"] == "ok":
            res, rows = ctl.execute(measurement, control, raw / "measurement", "nc",
                                    plan["model"], plan["rate"],
                                    arrival=list(plan["measurement_arrival_ns"]),
                                    group="measurement", health=health, nodes=hosts,
                                    arrival_seed=arrival_seed)
            ledger += rows
            if res["status"] == "ok":
                ctl.validate_audit(control, ledger, set())
                audit_ok = True
        window["last_completion_epoch_ns"] = time.time_ns()
        result["measurement_status"] = res["status"]
    except Exception as exc:
        result["measurement_error"] = repr(exc)
    finally:
        try:
            ctl.atomic(control / "stop", "stop\n")
        except Exception:
            pass
        tail_deadline = time.monotonic() + 5
        series_after = {}
        while time.monotonic() < tail_deadline:
            series_after, _ = monitor.records()
            if series_after and all(
                    any((smoke_runner._req(rec.get("ts_epoch_ns")) or 0)
                        >= window["last_completion_epoch_ns"] for rec in rows)
                    for rows in series_after.values()):
                break
            time.sleep(0.2)
        monitor.stop()
        mono_window = smoke_runner.derived_window_mono(control, setup)
        acc = {"schema": "finite-cache-monitor-acceptance", "version": 1,
               "run_id": args.slot_id, "slot_id": args.slot_id,
               "attempt": args.attempt, "transport": "nc",
               "expected_instances": sorted(range(1, hosts + 1)),
               "instances": {str(n): {"instance_identity": f"node{n}",
                                      "redis_run_id": (baselines.get(n) or {}).get("run_id", ""),
                                      "raw_file": f"monitor/node{n}.jsonl",
                                      "baseline": baselines.get(n),
                                      "first_sample": (series_after.get(n) or [None])[0],
                                      "last_sample": (series_after.get(n) or [None])[-1]}
                             for n in range(1, hosts + 1)},
               "baseline": {str(n): baselines.get(n) for n in range(1, hosts + 1)},
               "exit_codes": {str(n): monitor.exit_status.get(n) for n in range(1, hosts + 1)},
               "window": {"setup_start_epoch_ns": window["setup_start_epoch_ns"],
                          "last_completion_epoch_ns": window["last_completion_epoch_ns"],
                          "epoch_clock": "unix_epoch_ns",
                          "mono_clock": "container_monotonic_ns",
                          "source": "pilot_runner", **mono_window},
               "barrier": {"executed": True, "instances": sorted(baselines),
                           "timeout_s": 90}}
        smoke_runner.write_json(raw / "monitor-acceptance.json", acc)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if len(list((control / "stopped").glob("node*.stopped"))) == hosts:
                break
            time.sleep(0.3)
        smoke_runner.subprocess.run(["docker", "stop", "--time", "5", *container_ids],
                                    capture_output=True, check=False, timeout=300)

    traces = lab / "shared" / "traces"
    trace_files, trace_problems = {}, []
    for path in sorted(traces.glob("node*.jsonl")):
        node = int(re.sub(r"\D", "", path.stem))
        records, probs = smoke_runner.smoke_accept.parse_jsonl_strict(path)
        trace_files[node] = records
        trace_problems.extend(probs)
    discovered, discovery_problems = smoke_runner.smoke_accept.discover_completions(
        control / "completions", planned_indexes)
    audit_paths = sorted((control / "delivery-audit").glob("*.tsv"))
    owner_hashes = {}
    if Path(args.owner_hashes).is_file():
        loaded_hashes = json.loads(Path(args.owner_hashes).read_text(encoding="utf-8"))
        # supports {owner_hashes:{...}}, {scenarios:{<scenario>:{...}}} or a flat map
        if "scenarios" in loaded_hashes:
            owner_hashes = loaded_hashes["scenarios"].get(scenario, {})
        else:
            owner_hashes = loaded_hashes.get("owner_hashes", loaded_hashes)
    owner_evidence = {}
    for node in range(1, hosts + 1):
        path = data_dir / f"node{node}.tsv"
        expected = owner_hashes.get(str(node))
        if not path.is_file():
            owner_evidence[node] = {"valid": False, "reason": "owner_input_missing"}
            continue
        lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
        keys = [l.split("\t")[0] for l in lines[1:]]
        digest_ok = True
        if expected is not None:
            import hashlib
            digest_ok = hashlib.sha256(path.read_bytes()).hexdigest() == expected
        if not digest_ok:
            owner_evidence[node] = {"valid": False, "reason": "owner_hash_mismatch"}
        elif len(keys) != len(set(keys)):
            owner_evidence[node] = {"valid": False, "reason": "owner_duplicate_keys"}
        else:
            owner_evidence[node] = {"valid": True, "keys": keys,
                                    "expected_sha256": expected}
    monitor_series, _ = monitor.records()
    acceptance_path = raw / "monitor-acceptance.json"
    monitor_acceptance = (json.loads(acceptance_path.read_text(encoding="utf-8"))
                          if acceptance_path.is_file() else None)
    plan_reasons = pilot_plan.verify_plan(plan, frozen_lib)
    plan_ok = not plan_reasons
    require_eviction = slot["policy"] == "lru" and (slot["budget_bytes"] or 0) <= 4 * 1048576
    verdict = pilot_accept.accept_slot(
        hosts=hosts, planned=planned, completion_entries=[
            (i, control / "completions" / f"{i}.tsv") for i in planned_indexes],
        discovered_by_index=discovered, discovery_problems=discovery_problems,
        audit_paths=audit_paths, trace_files=trace_files, trace_problems=trace_problems,
        monitor_acceptance=monitor_acceptance, monitor_series=monitor_series,
        owner_evidence=owner_evidence, expected_policy=slot["policy"],
        expected_budget=slot["budget_bytes"], require_eviction=require_eviction,
        control=control, plan_ok=plan_ok, plan_reasons=plan_reasons,
        registered_indexes=planned_indexes)
    metrics = pilot_metrics.compute_metrics(ledger, plan, hosts)
    metrics["cache"] = pilot_metrics.cache_metrics(verdict["cache_summary"], trace_files,
                                                   metrics)
    result.update({"acceptance": verdict, "metrics": metrics, "audit_ok": audit_ok,
                   "stopped": len(list((control / "stopped").glob("node*.stopped"))),
                   "total_wall_seconds": round(time.monotonic() - begin, 1)})
    result["status"] = "ok" if (verdict["verdict"] == "PASS" and audit_ok) else "failed"

    archive = raw / "evidence"
    archive.mkdir(parents=True, exist_ok=True)
    for src_dir, label in ((control, "control"), (lab / "shared" / "logs", "node-logs"),
                           (raw / "monitor", "monitor")):
        if src_dir.exists():
            smoke_runner.shutil.copytree(src_dir, archive / label, dirs_exist_ok=True)
    if traces.exists():
        smoke_runner.shutil.copytree(traces, archive / "traces", dirs_exist_ok=True)
    smoke_runner.write_json(raw / "result.json", result)
    smoke_runner.write_json(raw / "metrics.json", metrics)
    with (raw / "cleanup.log").open("w") as handle:
        smoke_runner.subprocess.run(["kathara", "lclean", "-d", str(lab)], stdout=handle,
                                    stderr=smoke_runner.subprocess.STDOUT, check=False,
                                    timeout=600, env=env)
    print(json.dumps({"slot_id": result["slot_id"], "status": result["status"],
                      "verdict": verdict["verdict"], "wall": result["total_wall_seconds"],
                      "gates": {k: v["state"] for k, v in verdict["gates"].items()}}, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())