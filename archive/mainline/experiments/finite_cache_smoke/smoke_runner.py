#!/usr/bin/env python3
"""Finite-cache six-slot smoke driver.

Reuses the FROZEN measurement engine (`controller.py`) and lab backend
(`network_backend.py`) from experiments/concurrency_slides_v5, but supplies a
smoke-specific matrix, cache-policy env injection, per-instance Redis monitoring
and per-slot acceptance.

Phases:
  bringup   start the 16-node lab for one slot, prove readiness + cache_config
  run       also execute the frozen 6-key smoke request plan, monitor every
            Redis instance, and apply the slot acceptance gates

Usage:
  smoke_runner.py --root ROOT --inputs INPUTS --image IMG --slot-id ID \
      --scenario SC --cache-policy P --cache-budget B --transport T --rate R \
      --phase run --execute
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

NS = "finite-smoke"
DEFAULT_RATE_SEED = 9173
FROZEN_LIB = "experiments/concurrency_slides_v5"
REDIS_SOCK = "/run/redis/redis.sock"
MONITOR_INTERVAL_S = 0.2
MONITOR_MAX_GAP_MS = 600

COMMON_ENV = {
    "RSYNC_WIRE_VERSION": "3",
    "RSYNC_BLOOM_BITS": "1024",
    "RSYNC_BLOOM_HASH_COUNT": "3",
    "RSYNC_STAGED_FALLBACK": "1",
    "RSYNC_FALLBACK_DELAY_MS": "1500",
    "RSYNC_QUERY_TIMEOUT_MS": "30000",
    "RSYNC_REDIS_TIMEOUT_MS": "1000",
    "RSYNC_REDIS_MAXMEMORY": "0",
    "RSYNC_REDIS_MAXMEMORY_POLICY": "noeviction",
    "RSYNC_REDIS_REQUIRE_EMPTY": "1",
    "RSYNC_ORACLE_UNICAST": "0",
    "RSYNC_LOOP_ITERATIONS": "100000000",
    "RSYNC_EVENT_TRACE": "1",
    "RSYNC_EVENT_TRACE_FLUSH_EVERY": "1",
    "RSYNC_OBSERVABILITY": "0",
    "KATHARA_LINK_DELAY_MS": "2",
    "KATHARA_LINK_RATE_MBIT": "1000",
    "KATHARA_LINK_QUEUE_PACKETS": "1000",
    "KATHARA_LINK_MTU": "1500",
}


sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_plan  # noqa: E402  (run-before arrival plan, frozen generator)
import smoke_accept  # noqa: E402  (strict NC/TCP acceptance)
import monitor_evidence


def out(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()


def derived_window_mono(control: Path, setup_rows) -> dict:
    """Setup release bound and last completion bound in the CONTAINER monotonic
    clock domain (the app writes started/<i>.tsv field 3 and completions/<i>.tsv
    field 8 with steady_ns()). Derived from the run's own request records."""
    setup_indexes = {str(r["request_index"]) for r in setup_rows}
    starts, finishes = [], []
    for path in (control / "started").glob("*.tsv"):
        fields = path.read_text(encoding="utf-8").strip().split("\t")
        if len(fields) >= 3 and (not setup_indexes or fields[0] in setup_indexes):
            value = _req(fields[2])
            if value is not None:
                starts.append(value)
    for path in (control / "completions").glob("*.tsv"):
        fields = path.read_text(encoding="utf-8").strip().split("\t")
        if len(fields) >= 8:
            value = _req(fields[7])
            if value is not None:
                finishes.append(value)
    return {"setup_start_mono_ns": min(starts) if starts else None,
            "last_completion_mono_ns": max(finishes) if finishes else None,
            "sources": {"setup": "control/started/*.tsv:3",
                        "completion": "control/completions/*.tsv:8",
                        "clock": "container steady_ns (CLOCK_MONOTONIC)"}}


def _req(value):
    """Legal non-negative integer field: a JSON integer or a JSON string of
    decimal digits. Rejects bool, float, None, negative and any other text (no
    loose int() coercion)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    return None


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.rename(path)


# --------------------------------------------------------------------------- #
def read_tsv(path: Path) -> list[dict]:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    header = lines[0].split("\t")
    return [dict(zip(header, l.split("\t"))) for l in lines[1:]]


def schedule(inputs: Path, scenario: str, hosts: int):
    """Frozen smoke request plan: 6 formal keys, 4 measurement groups of 4.

    The setup rows come from the ORIGINAL schedule.tsv (as in pilot.input_rows),
    not from catalog.tsv."""
    mapped = inputs / "mapped" / scenario
    rows = [l.split("\t") for l in (mapped / "catalog.tsv").read_text().splitlines()]
    header = rows[0]
    keys = [dict(zip(header, r)) for r in rows[1:]
            if len(r) == len(header) and r[2] == "formal"
            and int(r[1]) >= hosts // 2 + 1][:6]
    assert len(keys) == 6, f"expected 6 eligible keys, got {len(keys)}"
    specs = [[(1, k) for k in keys[:4]], [(1, keys[4])] * 4,
             [(n, keys[5]) for n in (1, 2, 3, 4)], [(1, keys[0])] * 4]
    groups, index = [], 500000
    for g, spec in enumerate(specs, 1):
        group = []
        for node, key in spec:
            index += 1
            group.append(dict(key, request_index=str(index), requester=str(node),
                              phase="measurement", bin=str(g), round="0"))
        groups.append(group)
    setup = [r for r in read_tsv(mapped / "schedule.tsv") if r.get("phase") == "setup"]
    assert setup, "no setup rows in schedule.tsv"
    digest = hashlib.sha256(json.dumps([setup, groups], sort_keys=True).encode()).hexdigest()
    for r in setup + [r for g in groups for r in g]:
        r["schedule_sha256"] = digest
    return setup, groups, digest


def prepare(args, lab: Path, control: Path, data_dir: Path) -> None:
    if lab.exists():
        raise SystemExit(f"refusing to overwrite existing lab: {lab}")
    for sub in ("commands", "started", "completions", "ready", "stopped",
                "delivery-audit", "quiesce", "quiesced", "fatal"):
        (control / sub).mkdir(parents=True, exist_ok=True)
    (control / "async-v2.enabled").write_text("enabled\n", encoding="utf-8")
    if args.transport == "nc":
        (control / "tcp-connection-reuse.enabled").write_text("enabled\n", encoding="utf-8")
    shared = lab / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    src = Path(args.root) / "source" / "kathara" / "ruikekkk" / "shared"
    for name in ("start-node.sh", "start-switch.sh"):
        shutil.copy2(src / name, shared / name)
    shutil.copytree(Path(args.inputs) / "mapped" / args.scenario / "data", data_dir)
    shutil.copy2(Path(args.root) / "source" / FROZEN_LIB / "tcp_node.py",
                 data_dir.parent / "tcp_node.py")


def build_env(args, lab: Path, control: Path, data_dir: Path, digest: str) -> dict:
    env = dict(os.environ)
    env.update(COMMON_ENV)
    env.update({
        "LAB_DIR": str(lab),
        "SCENARIOS_DIR": str(Path(args.inputs) / "scenarios"),
        "IMAGE_TAG": args.image,
        "SKIP_BUILD": "1",
        "RSYNC_LOOP_SLEEP_MS": "25",
        "RSYNC_TOPOLOGY_CONFIG": "/shared/ruikekkk-topology.yaml",
        "RSYNC_TRANSPORT": "tcp",
        "RSYNC_EXPERIMENT_CONTROL_DIR": f"/shared/{NS}/control",
        "RSYNC_EXPERIMENT_DATA_DIR": f"/shared/{NS}/data",
        "RSYNC_EXPERIMENT_SCHEDULE_HASH": digest,
        "RSYNC_EVENT_TRACE_RUN_ID": args.slot_id,
        "RSYNC_SKIP_APP": "1" if args.transport == "tcp-direct" else "0",
    })
    if args.transport == "nc":
        env["RSYNC_CACHE_POLICY"] = args.cache_policy
        env["RSYNC_REPLICA_BUDGET_BYTES"] = str(args.cache_budget)
    env["PATH"] = str(Path(args.root) / "bin") + os.pathsep + env.get("PATH", "")
    return env


def wait_ready(control: Path, hosts: int, deadline_s: int = 300) -> None:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        ready = {int(p.stem.removeprefix("node"))
                 for p in (control / "ready").glob("node*.ready")}
        if ready == set(range(1, hosts + 1)):
            return
        time.sleep(0.5)
    raise SystemExit("readiness timeout: " + repr(sorted(
        p.name for p in (control / "ready").glob("*"))))


# --------------------------------------------------------------------------- #
class MultiRedisMonitor:
    """Strict per-instance Redis monitor.

    One independent `docker exec` python loop per NC node: it samples INFO on
    its OWN connection at a low frequency (never on the DATA path) and records a
    REAL in-container monotonic time plus the wall clock. INFO failures, non-zero
    redis-cli exits and unparsable output become explicit failing samples instead
    of being skipped. Each instance keeps its own series in collection order.
    """

    SCRIPT = r"""
import json,os,subprocess,time
sock="/run/redis/redis.sock"; stop="/shared/%NS%/monitor.stop"
while not os.path.exists(stop):
    rec={"ts_mono_ns":time.monotonic_ns(),"ts_epoch_ns":time.time_ns(),"status":"ok"}
    try:
        p=subprocess.run(["redis-cli","-s",sock,"INFO"],capture_output=True,text=True,timeout=5)
        rec["info_exit_code"]=p.returncode
        if p.returncode!=0:
            rec["status"]="info_exit_%d"%p.returncode; rec["stderr"]=p.stderr.strip()[:200]
        else:
            fields={}
            for line in p.stdout.replace("\r","").split("\n"):
                if ":" in line:
                    k,_,v=line.partition(":"); fields[k.strip()]=v.strip()
            for k in ("run_id","used_memory","evicted_keys"):
                if k in fields: rec[k]=fields[k]
            if "run_id" not in fields or "used_memory" not in fields or "evicted_keys" not in fields:
                rec["status"]="info_unparsed"
    except Exception as exc:
        rec["status"]="info_error"; rec["error"]=repr(exc)[:200]
    print(json.dumps(rec),flush=True)
    time.sleep(%INTERVAL%)
"""

    def __init__(self, containers, raw_dir, ns_dir):
        self.containers = containers
        self.raw_dir = raw_dir
        self.ns_dir = ns_dir
        self.stop_host = ns_dir / "monitor.stop"
        self.procs = {}
        self.handles = []
        self.exit_status = {}

    def start(self):
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.ns_dir.mkdir(parents=True, exist_ok=True)
        self.stop_host.unlink(missing_ok=True)
        script = (self.SCRIPT.replace("%NS%", NS)
                  .replace("%INTERVAL%", str(MONITOR_INTERVAL_S)))
        for node, container in sorted(self.containers.items()):
            handle = (self.raw_dir / f"node{node}.jsonl").open("w")
            self.handles.append(handle)
            self.procs[node] = subprocess.Popen(
                ["docker", "exec", container, "python3", "-c", script],
                stdout=handle, stderr=subprocess.STDOUT)

    def baseline_barrier(self, hosts, timeout_s=90):
        """Block until EVERY expected instance has produced a valid baseline
        sample (identity + run_id + legal used_memory/evicted_keys + healthy
        monitor process). Returns the per-instance baselines; raises on timeout
        or a dead monitor process."""
        import time as _time
        deadline = _time.monotonic() + timeout_s
        while _time.monotonic() < deadline:
            series, _ = self.records()
            ready = {n: v[0] for n, v in series.items()
                     if v and v[0].get("status") == "ok"
                     and _req(v[0].get("used_memory")) is not None
                     and _req(v[0].get("evicted_keys")) is not None
                     and v[0].get("redis_run_id")}
            dead = [n for n, p in self.procs.items() if p.poll() is not None]
            if dead:
                raise RuntimeError(f"monitor process died before baseline: {dead}")
            if set(ready) == set(range(1, hosts + 1)):
                return ready
            _time.sleep(0.2)
        raise RuntimeError("baseline barrier timeout: "
                           + repr(sorted(set(range(1, hosts + 1)) - set(ready))))

    def stop(self):
        self.stop_host.write_text("stop\n", encoding="utf-8")
        for node, proc in self.procs.items():
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5)
            self.exit_status[node] = proc.returncode
        for handle in self.handles:
            handle.close()

    def records(self):
        """Strict parse, preserving collection order. A malformed line is kept
        as an explicit failing sample (never dropped)."""
        series = {}
        problems = []
        for node in sorted(self.containers):
            path = self.raw_dir / f"node{node}.jsonl"
            rows = []
            if not path.is_file():
                series[node] = rows
                problems.append(f"node{node}:no_raw_output")
                continue
            for lineno, raw in enumerate(path.read_text(encoding="utf-8",
                                                       errors="replace").split("\n"), 1):
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    problems.append(f"node{node}:corrupt:{lineno}")
                    rows.append({"node": node, "status": "corrupt_raw"})
                    continue
                if not isinstance(rec, dict):
                    problems.append(f"node{node}:non_object:{lineno}")
                    rows.append({"node": node, "status": "non_object_raw"})
                    continue
                rec = dict(rec, node=node)
                rec["instance_identity"] = f"node{node}"
                # the in-container script emits `run_id`; normalise the field
                # name the acceptance/barrier read.
                if not rec.get("redis_run_id"):
                    rec["redis_run_id"] = rec.get("run_id", "")
                rows.append(rec)
            series[node] = rows
        return series, problems



def _scope_self_test() -> int:
    """Deterministic two-labs fixture: the selected set must contain ONLY this
    lab's containers, and node coverage must be exact."""
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        print(f"{name}: {'PASS' if ok else 'FAIL'}{' ' + detail if detail and not ok else ''}")
        if not ok:
            failures += 1

    def container(cid, name, shared):
        return {"Id": cid, "Mounts": [{"Source": shared}],
                "Config": {"Labels": {"name": name}}}

    mine = "/lab/A/shared"; other = "/lab/B/shared"
    inspect_list = [
        container("a-switch", "node101", mine),
        *[container(f"a{n}", f"node{n}", mine) for n in range(1, 17)],
        *[container(f"b{n}", f"node{n}", other) for n in range(1, 17)],
        container("b-switch", "node201", other),
    ]
    ids, nodes, others = scope_containers(inspect_list, mine, 16)
    check("two_labs_selects_only_this_lab", all(i.startswith("a") for i in ids), str(ids[:3]))
    check("two_labs_excludes_other_lab", not any(i.startswith("b") for i in ids))
    check("protocol_nodes_exactly_1_16", set(nodes) == set(range(1, 17)))
    check("switches_kept_for_cleanup", others == ["a-switch"], str(others))

    try:
        scope_containers([c for c in inspect_list if c["Id"].startswith("b")], mine, 16)
        check("wrong_lab_rejected", False, "accepted empty scope")
    except RuntimeError:
        check("wrong_lab_rejected", True)

    missing = [c for c in inspect_list if c["Id"] != "a7"]
    try:
        scope_containers(missing, mine, 16)
        check("missing_node_rejected", False, "accepted missing node")
    except RuntimeError:
        check("missing_node_rejected", True)

    dup = inspect_list + [container("a7b", "node7", mine)]
    try:
        scope_containers(dup, mine, 16)
        check("duplicate_node_rejected", False, "accepted duplicate node")
    except RuntimeError:
        check("duplicate_node_rejected", True)
    return 0 if failures == 0 else 1


# --------------------------------------------------------------------------- #
# lab scoping + real process health
# --------------------------------------------------------------------------- #
def scope_containers(inspect_list: list, lab_shared: str, hosts: int):
    """Pure filter over `docker inspect` output.

    A container belongs to the lab iff it mounts THIS lab's shared directory.
    Protocol nodes must be exactly node1..node<hosts> with no duplicates; any
    other Kathara container (another lab) is never selected."""
    scoped = [c for c in inspect_list
              if any(m.get("Source") == lab_shared for m in c.get("Mounts", []))]
    nodes = {}
    others = []
    for c in scoped:
        name = c.get("Config", {}).get("Labels", {}).get("name", "")
        match = re.fullmatch(r"node(\d+)", name)
        if match and 1 <= int(match.group(1)) <= hosts:
            nodes.setdefault(int(match.group(1)), []).append(c["Id"])
        else:
            others.append(c["Id"])  # switches (node101..) and anything else
    missing = sorted(set(range(1, hosts + 1)) - set(nodes))
    extra = sorted(set(nodes) - set(range(1, hosts + 1)))
    duplicated = sorted(n for n, ids in nodes.items() if len(ids) != 1)
    if not scoped:
        raise RuntimeError("lab scope empty: no container mounts " + lab_shared)
    if missing or extra or duplicated:
        raise RuntimeError(f"lab scope invalid: missing={missing} extra={extra} "
                           f"duplicated={duplicated}")
    return ([c["Id"] for c in scoped],
            {n: ids[0] for n, ids in nodes.items()},
            list(others))


def lab_containers(lab: Path, hosts: int):
    ids = out(["docker", "ps", "-aq", "--filter", "label=app=kathara"]).splitlines()
    inspect_list = json.loads(out(["docker", "inspect", *ids])) if ids else []
    return scope_containers(inspect_list, str(lab / "shared"), hosts)


class ProcessHealth:
    """Real process health (modelled on the frozen pilot Health): raise
    ctl.ProcessFailure if a node application disappears/is replaced or a
    tcp-direct helper exits. Never a no-op."""

    def __init__(self, ctl, method, node_containers, helpers, raw):
        self.ctl = ctl
        self.method = method
        self.helpers = helpers
        self.raw = raw
        self.pids = {}
        self.last = 0.0
        if method == "nc":
            for node, container in sorted(node_containers.items()):
                text = out(["docker", "top", container, "-eo", "pid,args"])
                candidates = [l.split(None, 1)[0] for l in text.splitlines()[1:]
                              if len(l.split(None, 1)) == 2
                              and l.split(None, 1)[1].startswith("/app/build/rsync-node-dev")]
                if len(candidates) != 1:
                    raise RuntimeError("cannot locate node host PID for node"
                                       + str(node))
                pid = candidates[0]
                self.pids[pid] = Path("/proc", pid, "stat").read_text().split()[21]
            if len(self.pids) != len(node_containers):
                raise RuntimeError("NC PID inventory mismatch")

    def __call__(self):
        now = time.monotonic()
        if now - self.last < 0.2:
            return
        self.last = now
        if any(p.poll() is not None for p in self.helpers):
            raise self.ctl.ProcessFailure("tcp-direct helper exited")
        for pid, start in self.pids.items():
            try:
                fields = Path("/proc", pid, "stat").read_text().split()
                if fields[21] != start or fields[2] == "Z":
                    raise self.ctl.ProcessFailure("node process exited/replaced " + pid)
            except FileNotFoundError:
                raise self.ctl.ProcessFailure("node process disappeared " + pid)

# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--inputs", default="")
    ap.add_argument("--image", default="")
    ap.add_argument("--scenario", default="")
    ap.add_argument("--slot-id", default="")
    ap.add_argument("--cache-policy", default="")
    ap.add_argument("--cache-budget", default="0")
    ap.add_argument("--transport", default="nc", choices=("nc", "tcp-direct"))
    ap.add_argument("--rate", type=int, default=32)
    ap.add_argument("--hosts", type=int, default=16)
    ap.add_argument("--phase", default="run", choices=("bringup", "run"))
    ap.add_argument("--attempt", default="001")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--owner-hashes", default="")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _scope_self_test()
    if not args.execute:
        raise SystemExit("--execute required")
    for name in ("root", "inputs", "image", "scenario", "slot_id"):
        if not getattr(args, name):
            raise SystemExit(f"--{name.replace('_', '-')} required")

    root = Path(args.root)
    lab = root / "lab" / "smoke" / args.slot_id / f"attempt{args.attempt}"
    control = lab / "shared" / NS / "control"
    data_dir = lab / "shared" / NS / "data"
    ns_dir = lab / "shared" / NS
    raw = root / "raw" / "smoke" / args.slot_id / f"attempt{args.attempt}"
    raw.mkdir(parents=True, exist_ok=True)

    setup, groups, digest = schedule(Path(args.inputs), args.scenario, args.hosts)
    planned = sum(len(g) for g in groups)
    # The arrival process is frozen BEFORE the run and loaded from plan.json, so
    # the runner cannot regenerate a different array. r32 and r128 therefore use
    # genuinely different Poisson offsets, and the three methods at one rate
    # share byte-identical arrays.
    frozen_lib = root / "source" / FROZEN_LIB
    plan = smoke_plan.build_plan(frozen_lib, args.slot_id, args.transport,
                                 groups, setup, args.rate, DEFAULT_RATE_SEED)
    plan_ok, plan_reasons = smoke_plan.verify_plan(plan, frozen_lib)
    if not plan_ok:
        raise SystemExit("refusing to start: plan failed verification: "
                         + "; ".join(plan_reasons))
    plan_path = raw / "plan.json"
    if plan_path.exists():
        previous = json.loads(plan_path.read_text(encoding="utf-8"))
        if smoke_plan.plan_sha256(previous) != plan["plan_sha256"]:
            raise SystemExit("refusing to start: existing plan.json differs")
    else:
        write_json(plan_path, plan)
    loaded = json.loads(plan_path.read_text(encoding="utf-8"))
    loaded_ok, loaded_reasons = smoke_plan.verify_plan(loaded, frozen_lib)
    if not loaded_ok or loaded["plan_sha256"] != plan["plan_sha256"]:
        raise SystemExit("refusing to start: loaded plan mismatch: "
                         + "; ".join(loaded_reasons))
    result = {"slot_id": args.slot_id, "phase": args.phase,
              "status": "infrastructure_failure", "cache_policy": args.cache_policy,
              "cache_budget": args.cache_budget, "transport": args.transport,
              "rate": args.rate, "hosts": args.hosts, "schedule_digest": digest,
              "setup": len(setup), "measurement": planned, "image": args.image}
    write_json(raw / "schedule.json", result)

    sys.path.insert(0, str(root / "source" / FROZEN_LIB))
    import controller as ctl
    import network_backend

    prepare(args, lab, control, data_dir)
    env = build_env(args, lab, control, data_dir, digest)
    write_json(raw / "environment.json",
               {k: v for k, v in env.items() if k.startswith("RSYNC_")})

    begin = time.monotonic()
    with (raw / "driver.log").open("w") as out_file:
        proc = subprocess.run(
            [str(root / "source" / "scripts" / "kathara" / "run_ruikekkk_scenario.sh"),
             args.scenario], env=env, stdout=out_file, stderr=subprocess.STDOUT, timeout=900)
    result["render_exit"] = proc.returncode
    if proc.returncode != 0:
        write_json(raw / "result.json", result)
        raise SystemExit(f"scenario start failed rc={proc.returncode}")
    result["startup_seconds"] = round(time.monotonic() - begin, 1)
    helpers: list = []
    helper_handles: list = []
    if args.transport == "tcp-direct":
        # TCP Direct does not run the Node cache: its nodes are driven by
        # tcp_node.py, which also provides readiness and the transport trace.
        tcp_traces = ns_dir / "tcp-traces"
        tcp_traces.mkdir(parents=True, exist_ok=True)
        for node in range(1, args.hosts + 1):
            handle = (raw / f"tcp-helper-node{node}.log").open("w")
            helper_handles.append(handle)
            helpers.append(subprocess.Popen(
                ["kathara", "exec", "-d", str(lab), "--wait", f"node{node}", "--",
                 "python3", f"/shared/{NS}/tcp_node.py", str(node),
                 f"/shared/{NS}/data/node{node}.tsv", f"/shared/{NS}/control", digest,
                 "--trace", f"/shared/{NS}/tcp-traces/node{node}.jsonl"],
                stdout=handle, stderr=subprocess.STDOUT))
    try:
        wait_ready(control, args.hosts)
    except SystemExit:
        for proc_h in helpers:
            proc_h.terminate()
        raise

    # Scope strictly to THIS lab: container identity is the mount of this lab's
    # shared directory, and the protocol nodes must be exactly node1..node16.
    container_ids, name_by_node, switch_ids = lab_containers(lab, args.hosts)
    inspected = json.loads(out(["docker", "inspect", *container_ids]))
    network_backend.prepare_lab(raw, inspected)
    network_backend.verify_lab(root, lab, raw, inspected)

    monitor = MultiRedisMonitor(name_by_node, raw / "monitor", ns_dir)
    health = ProcessHealth(ctl, args.transport, name_by_node, helpers, raw)
    baselines = {}
    if args.transport == "nc":
        monitor.start()
        # Startup barrier: no request is released until EVERY expected instance
        # has a valid baseline sample and its monitor process is healthy.
        baselines = monitor.baseline_barrier(args.hosts)
        write_json(raw / "monitor-baseline.json",
                   {str(n): baselines[n] for n in sorted(baselines)})

    ledger: list = []
    audit_ok = False
    window = {"setup_start_epoch_ns": time.time_ns(), "last_completion_epoch_ns": 0}
    try:
        if args.phase == "run":
            res, rows = ctl.execute(setup, control, raw / "setup", args.transport, "setup",
                                    arrival=list(plan["setup_arrival_ns"]),
                                    group="setup", health=health, nodes=args.hosts)
            ledger += rows
            if res["status"] == "ok":
                for i, group in enumerate(groups, 1):
                    res, rows = ctl.execute(
                        group, control, raw / f"measurement-{i}", args.transport,
                        plan["model"], plan["rate"],
                        arrival=list(plan["measurement_arrival_ns"][i - 1]),
                        group=f"measurement-{i}", health=health,
                        nodes=args.hosts, arrival_seed=plan["seed"])
                    ledger += rows
                    if res["status"] != "ok":
                        break
            if res["status"] == "ok":
                ctl.validate_audit(control, ledger, set())
                audit_ok = True
            window["last_completion_epoch_ns"] = time.time_ns()
        else:
            audit_ok = True
    except Exception as exc:  # keep evidence even when the measurement fails
        result["measurement_error"] = repr(exc)
    finally:
        try:
            ctl.atomic(control / "stop", "stop\n")
        except Exception:
            pass
        if args.transport == "nc":
            # Tail coverage: do not stop the monitor until every instance has
            # produced a sample at/after the window end (last completion).
            tail_deadline = time.monotonic() + 5
            while time.monotonic() < tail_deadline:
                series, _ = monitor.records()
                if series and all(
                        any((_req(rec.get("ts_epoch_ns")) or 0)
                            >= window["last_completion_epoch_ns"]
                            for rec in rows)
                        for rows in series.values()):
                    break
                time.sleep(0.2)
            monitor.stop()
            series_after, _ = monitor.records()
            mono_window = derived_window_mono(control, setup)
            acc = {
                "schema": "finite-cache-monitor-acceptance",
                "version": 1,
                "run_id": args.slot_id, "slot_id": args.slot_id,
                "attempt": args.attempt, "transport": args.transport,
                "expected_instances": sorted(range(1, args.hosts + 1)),
                "instances": {
                    str(n): {"instance_identity": f"node{n}",
                             "redis_run_id": (baselines.get(n) or {}).get("run_id", ""),
                             "raw_file": f"monitor/node{n}.jsonl",
                             "baseline": baselines.get(n),
                             "first_sample": (series_after.get(n) or [None])[0],
                             "last_sample": (series_after.get(n) or [None])[-1]}
                    for n in range(1, args.hosts + 1)},
                "baseline": {str(n): baselines.get(n) for n in range(1, args.hosts + 1)},
                "exit_codes": {str(n): monitor.exit_status.get(n)
                               for n in range(1, args.hosts + 1)},
                "window": {
                    "setup_start_epoch_ns": window["setup_start_epoch_ns"],
                    "last_completion_epoch_ns": window["last_completion_epoch_ns"],
                    "epoch_clock": "unix_epoch_ns",
                    "mono_clock": "container_monotonic_ns",
                    "source": "smoke_runner",
                    **mono_window},
                "barrier": {"executed": True, "instances": sorted(baselines),
                            "timeout_s": 90, "executed_epoch_ns": window["setup_start_epoch_ns"]},
            }
            write_json(raw / "monitor-acceptance.json", acc)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if len(list((control / "stopped").glob("node*.stopped"))) == args.hosts:
                break
            time.sleep(0.3)
        for proc_h in helpers:
            try:
                proc_h.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc_h.terminate()
                proc_h.wait(timeout=5)
        for handle in helper_handles:
            handle.close()
        subprocess.run(["docker", "stop", "--time", "5", *container_ids],
                       capture_output=True, check=False, timeout=180)

    result["stopped"] = len(list((control / "stopped").glob("node*.stopped")))
    traces = lab / "shared" / "traces"
    planned = []
    for r in setup + [row for g in groups for row in g]:
        planned.append({"index": str(r["request_index"]),
                        "node": int(r["requester"]),
                        "requester": str(r["requester"]),
                        "owner": str(r["owner"]),
                        "key": r["key"],
                        "value_bytes": int(r["value_size_bytes"]),
                        "expected_fnv1a64": r["expected_fnv1a64"]})
    planned_indexes = {r["index"] for r in planned}
    completion_entries = [(r["index"], control / "completions" / f"{r['index']}.tsv")
                          for r in planned]
    discovered_by_index, discovery_problems = smoke_accept.discover_completions(
        control / "completions", planned_indexes)
    audit_paths = sorted((control / "delivery-audit").glob("*.tsv"))

    trace_files = {}
    trace_problems = []
    if args.transport == "nc":
        for path in sorted(traces.glob("node*.jsonl")):
            node = int(re.sub(r"\D", "", path.stem))
            records, problems = smoke_accept.parse_jsonl_strict(path)
            trace_files[node] = records
            trace_problems.extend(problems)
    tcp_trace_files = {}
    tcp_trace_problems = []
    if args.transport == "tcp-direct":
        for path in sorted((control.parent / "tcp-traces").glob("node*.jsonl")):
            node = int(re.sub(r"\D", "", path.stem))
            records, problems = smoke_accept.parse_jsonl_strict(path)
            tcp_trace_files[node] = records
            tcp_trace_problems.extend(problems)

    owner_hashes = {}
    if args.owner_hashes and Path(args.owner_hashes).is_file():
        loaded_owner_hashes = json.loads(
            Path(args.owner_hashes).read_text(encoding="utf-8"))
        # the frozen provenance file nests the per-node map under `owner_hashes`
        owner_hashes = loaded_owner_hashes.get("owner_hashes", loaded_owner_hashes)
    owner_evidence = {}
    data_dir_actual = ns_dir / "data"
    for node in range(1, args.hosts + 1):
        path = data_dir_actual / f"node{node}.tsv"
        expected = owner_hashes.get(str(node))
        if not path.is_file():
            owner_evidence[node] = {"valid": False, "reason": "owner_input_missing"}
            continue
        lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
        header = lines[0].split("\t") if lines else []
        keys = [l.split("\t")[0] for l in lines[1:]]
        if header[:4] != ["key", "value_size_bytes", "content_seed", "expected_fnv1a64"]:
            owner_evidence[node] = {"valid": False, "reason": "owner_schema_mismatch"}
            continue
        if len(keys) != len(set(keys)):
            owner_evidence[node] = {"valid": False, "reason": "owner_duplicate_keys"}
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected is not None and digest != expected:
            owner_evidence[node] = {"valid": False, "reason": "owner_hash_mismatch"}
            continue
        owner_evidence[node] = {"valid": True, "keys": keys, "sha256": digest,
                                "expected_sha256": expected,
                                "source": f"data/node{node}.tsv"}

    monitor_series, monitor_problems = ({}, [])
    if args.transport == "nc":
        monitor_series, monitor_problems = monitor.records()
    acceptance_path = raw / "monitor-acceptance.json"
    monitor_acceptance = (json.loads(acceptance_path.read_text(encoding="utf-8"))
                          if acceptance_path.is_file() else None)
    if args.transport == "nc" and monitor_acceptance is not None:
        monitor_series, sealed_problems = monitor_evidence.load(raw, monitor_acceptance, {
            "run_id": args.slot_id, "slot_id": args.slot_id,
            "attempt": args.attempt, "transport": args.transport})
        monitor_problems.extend(sealed_problems)

    plan_ok, plan_reasons = smoke_plan.verify_plan(plan, frozen_lib)

    verdict = smoke_accept.accept_slot(
        transport=args.transport, hosts=args.hosts, planned=planned,
        completion_entries=completion_entries,
        discovered_by_index=discovered_by_index,
        discovery_problems=discovery_problems, audit_paths=audit_paths,
        trace_files=trace_files, trace_problems=trace_problems,
        tcp_trace_files=tcp_trace_files, tcp_trace_problems=tcp_trace_problems,
        monitor_acceptance=monitor_acceptance, monitor_series=monitor_series,
        owner_evidence=owner_evidence if args.transport == "nc" else None,
        expected_policy=args.cache_policy, expected_budget=int(args.cache_budget),
        control=control, plan_ok=plan_ok, plan_reasons=plan_reasons,
        registered_indexes=planned_indexes)
    if monitor_problems:
        verdict["gates"]["raw_monitor_records"] = {
            "state": "rejected", "detail": "; ".join(monitor_problems[:4])}
        verdict["verdict"] = "REJECTED"
    result["acceptance"] = verdict
    result["status"] = "ok" if (verdict["verdict"] == "PASS" and audit_ok
                                and args.phase == "run") else (
        "ok" if args.phase == "bringup" and not list((control / "fatal").glob("*"))
        else "failed")
    result["total_wall_seconds"] = round(time.monotonic() - begin, 1)

    archive = raw / "evidence"
    archive.mkdir(parents=True, exist_ok=True)
    for src_dir, label in ((control, "control"), (lab / "shared" / "logs", "node-logs")):
        if src_dir.exists():
            shutil.copytree(src_dir, archive / label, dirs_exist_ok=True)
    trace_src = (control.parent / "tcp-traces") if args.transport == "tcp-direct" else traces
    if trace_src.exists():
        shutil.copytree(trace_src, archive / "traces", dirs_exist_ok=True)
    monitor_dir = raw / "monitor"
    if monitor_dir.exists():
        shutil.copytree(monitor_dir, archive / "monitor", dirs_exist_ok=True)
    write_json(raw / "result.json", result)

    with (raw / "cleanup.log").open("w") as handle:
        subprocess.run(["kathara", "lclean", "-d", str(lab)], stdout=handle,
                       stderr=subprocess.STDOUT, check=False, timeout=300, env=env)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
