#!/usr/bin/env python3
"""Run one side of a paired Cross-host Federation attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import IO


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
UPSTREAM = REPO / "experiments/content_discovery/delay_find_content/scripts"
RUN_SCENARIO = REPO / "scripts/kathara/run_ruikekkk_scenario.sh"
DOWN_SCENARIO = REPO / "scripts/kathara/down_ruikekkk_scenario.sh"
SUITE = "cross-host-federation-v1"
PAIR_RESULT_TIMEOUT_SECONDS = 180
START_NODE = REPO / "kathara/ruikekkk/shared/start-node.sh"
START_SWITCH = REPO / "kathara/ruikekkk/shared/start-switch.sh"


def run(command: list[str], *, env: dict[str, str] | None = None,
        check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command, env=env, text=True, capture_output=True, check=False, timeout=timeout
    )
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def copy_inputs(workload: Path, scenario: str, run_dir: Path) -> tuple[Path, str]:
    source = workload / "mapped" / scenario
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    for name in ("metadata.json", "activation.json", "schedule.tsv", "isolation_probes.tsv"):
        shutil.copy2(source / name, run_dir / ("schedule_metadata.json" if name == "metadata.json" else name))
    shutil.copy2(workload / "scenarios" / f"{scenario}.yaml", run_dir / "scenario.yaml")
    return source, str(metadata["schedule_sha256"])


def install_lab_support(shared: Path) -> None:
    """Install files that the generated ``node*.startup`` scripts execute."""
    for bootstrap in (START_NODE, START_SWITCH):
        if not bootstrap.is_file():
            raise RuntimeError(f"missing Kathara bootstrap: {bootstrap}")
    # Generated startup scripts address bootstraps from the lab-wide /shared
    # root, while suite-specific controller/helpers live one level below it.
    shutil.copy2(START_NODE, shared.parent / "start-node.sh")
    shutil.copy2(START_SWITCH, shared.parent / "start-switch.sh")
    shutil.copy2(UPSTREAM / "latency_v3_tcp_node.py", shared / "tcp_node.py")
    shutil.copy2(UPSTREAM / "federation_v3_cache_probe.py", shared / "cache_probe.py")


def environment(args: argparse.Namespace, lab: Path, control: Path,
                data: Path, schedule_hash: str, run_dir: Path) -> dict[str, str]:
    values = os.environ.copy()
    values.update({
        "LAB_DIR": str(lab),
        "SCENARIOS_DIR": str(args.workload_root / "scenarios"),
        "DOCKERFILE_PATH": str(REPO / "Dockerfile.latency-v3"),
        "IMAGE_TAG": "rsync-router:cross-host-federation-v1",
        "SKIP_BUILD": "1",
        "ENABLE_REDIS": "ON",
        "FETCH_REDIS_DEPS": "ON",
        "KATHARA_FEDERATION_PHYSICAL_MODE": "bordered-cross-host",
        "KATHARA_FEDERATION_SHARD": "a" if args.role == "domain-a" else "b",
        "KATHARA_LINK_DELAY_MS": "2",
        "KATHARA_LINK_RATE_MBIT": "1000",
        "KATHARA_LINK_QUEUE_PACKETS": "1000",
        "KATHARA_LINK_MTU": "1400",
        "KATHARA_NODE_LOOP_SLEEP_MS": "25",
        "RSYNC_WIRE_VERSION": "3",
        "RSYNC_BLOOM_BITS": "1024",
        "RSYNC_BLOOM_HASH_COUNT": "3",
        "RSYNC_STAGED_FALLBACK": "1",
        "RSYNC_FALLBACK_DELAY_MS": "1500",
        "RSYNC_QUERY_TIMEOUT_MS": "7000",
        "RSYNC_REDIS_TIMEOUT_MS": "1000",
        "RSYNC_REDIS_MAXMEMORY": "0",
        "RSYNC_REDIS_MAXMEMORY_POLICY": "noeviction",
        "RSYNC_REDIS_REQUIRE_EMPTY": "1",
        # Long calibration/formal horizons must be controller-terminated, not
        # cut short by the legacy roughly one-hour fixed loop lifetime.
        "RSYNC_LOOP_ITERATIONS": "2147483647",
        "RSYNC_SKIP_APP": "1" if args.method == "tcp-direct" else "0",
        "RSYNC_ORACLE_UNICAST": "0",
        "RSYNC_FEDERATION_METHOD": args.method,
        "RSYNC_FEDERATION_ACTIVATION_MODE": "logical-only",
        "RSYNC_EVENT_TRACE": "1",
        "RSYNC_EVENT_TRACE_FLUSH_EVERY": "1",
        "RSYNC_EVENT_TRACE_RUN_ID": run_dir.name,
        "RSYNC_EXPERIMENT_CONTROL_DIR": f"/shared/{SUITE}/control",
        "RSYNC_EXPERIMENT_DATA_DIR": f"/shared/{SUITE}/data",
        "RSYNC_EXPERIMENT_SCHEDULE_HASH": schedule_hash,
        "RUNTIME_PROVENANCE_FILE": str(run_dir / "runtime_image_id.before_launch"),
    })
    return values


def wait_startup(lab: Path, role: str, method: str, deadline_seconds: int = 300) -> None:
    nodes = range(1, 33) if role == "domain-a" else range(33, 65)
    switches = list(range(101, 109)) + list(range(201, 205)) + [250]
    if role == "domain-b":
        switches = list(range(301, 309)) + list(range(401, 405)) + [450]
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        host_token = (
            "network configured without redis or rsync-node-dev"
            if method == "tcp-direct" else "started rsync-node-dev"
        )
        hosts_ready = sum(
            host_token in (lab / "shared/logs" / f"node{node}.startup.log").read_text(
                encoding="utf-8", errors="replace"
            ) if (lab / "shared/logs" / f"node{node}.startup.log").is_file() else False
            for node in nodes
        )
        switches_ready = sum(
            "configured forwarding switch" in (
                lab / "shared/logs" / f"node{node}.startup.log"
            ).read_text(encoding="utf-8", errors="replace")
            if (lab / "shared/logs" / f"node{node}.startup.log").is_file() else False
            for node in switches
        )
        if hosts_ready == 32 and switches_ready == 13:
            return
        time.sleep(2)
    raise RuntimeError(f"startup timeout: hosts={hosts_ready}/32 network_devices={switches_ready}/13")


def launch_helpers(args: argparse.Namespace, lab: Path, shared: Path,
                   schedule_hash: str, logs: Path) -> list[tuple[subprocess.Popen[str], IO[str]]]:
    nodes = range(1, 33) if args.role == "domain-a" else range(33, 65)
    processes: list[tuple[subprocess.Popen[str], IO[str]]] = []
    for node in nodes:
        log = (logs / f"node{node}.helper.log").open("w", encoding="utf-8")
        if args.method == "tcp-direct":
            inside = [
                "env", "RSYNC_FEDERATION_ACTIVATION_MODE=logical-only",
                "python3", f"/shared/{SUITE}/tcp_node.py", str(node),
                f"/shared/{SUITE}/data/node{node}.tsv", f"/shared/{SUITE}/control",
                schedule_hash, "--trace", f"/shared/{SUITE}/tcp-traces/node{node}.jsonl",
            ]
        else:
            inside = [
                "python3", f"/shared/{SUITE}/cache_probe.py", str(node),
                f"/shared/{SUITE}/data/node{node}.tsv", f"/shared/{SUITE}/control",
            ]
        command = ["kathara", "exec", "-d", str(lab), "--wait", f"node{node}", "--", *inside]
        process = subprocess.Popen(command, text=True, stdout=log, stderr=subprocess.STDOUT)
        processes.append((process, log))
    return processes


def copy_outputs(lab: Path, shared: Path, run_dir: Path) -> None:
    for source_name, destination_name in (
        ("logs", "logs"), ("traces", "traces"), ("generated", "generated_network")
    ):
        source = lab / "shared" / source_name
        if source.is_dir():
            shutil.copytree(source, run_dir / destination_name, dirs_exist_ok=True)
    tcp_traces = shared / "tcp-traces"
    if tcp_traces.is_dir():
        shutil.copytree(tcp_traces, run_dir / "traces", dirs_exist_ok=True)
    activated = shared / "control/activated"
    if activated.is_dir():
        shutil.copytree(activated, run_dir / "activation_acks", dirs_exist_ok=True)
    pair_result = shared / "control/pair/result.json"
    source = shared / 'control/pre-isolation-routes.json'
    if source.is_file():
        shutil.copy2(source, run_dir / source.name)
    completions = shared / 'control/completions'
    if completions.is_dir():
        for source in completions.glob('900*.tsv'):
            target = run_dir / 'isolation-completions' / source.name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
    if pair_result.is_file():
        shutil.copy2(pair_result, run_dir / "pair_result.json")
    for name in ("lab.conf", "lab.dep"):
        if (lab / name).is_file():
            shutil.copy2(lab / name, run_dir / name)
    for name in ("ruikekkk-topology.yaml", "ruikekkk-topology-cluster-a.yaml",
                 "ruikekkk-topology-cluster-b.yaml"):
        if (lab / "shared" / name).is_file():
            shutil.copy2(lab / "shared" / name, run_dir / name)


def snapshot(run_dir: Path, role: str) -> None:
    commands = {
        "wireguard": ["wg", "show", "wg-xfed-v1", "dump"],
        "tc": ["tc", "-j", "qdisc", "show", "dev", "wg-xfed-v1"],
        "link": ["ip", "-j", "link", "show", "wg-xfed-v1"],
        "routes": ["ip", "-j", "route", "show", "table", "main"],
        "image": ["docker", "image", "inspect", "rsync-router:cross-host-federation-v1"],
    }
    value: dict[str, object] = {"role": role, "captured_epoch_ns": time.time_ns()}
    for name, command in commands.items():
        completed = run(command, check=False)
        value[name] = {"returncode": completed.returncode, "stdout": completed.stdout,
                       "stderr": completed.stderr}
    atomic_json(run_dir / "host_snapshot.json", value)


def wait_for_pair_result(path: Path, agent: subprocess.Popen[str],
                         timeout_seconds: int = PAIR_RESULT_TIMEOUT_SECONDS) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if agent.poll() is not None:
            raise RuntimeError("control agent exited before final pair result")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.1)
            continue
        if value.get("status") in {"ok", "failed"}:
            return value
        time.sleep(0.1)
    raise RuntimeError("coordinator did not publish a final pair result")


def notify_failed_peer(url: str, token: str, attempt_id: str, error: str) -> None:
    from distributed_controller import RemoteStore
    remote = RemoteStore(url, token)
    if remote.health().get("attempt_id") != attempt_id:
        raise RuntimeError("peer attempt differs; refusing failure notification")
    remote.write("pair/result.json", json.dumps({"status": "failed", "error": error}) + "\n")
    remote.write("stop", "stop\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=("domain-a", "domain-b"))
    parser.add_argument("--method", required=True, choices=("nc", "tcp-direct"))
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--workload-root", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--assignment", type=Path)
    parser.add_argument("--remote-agent", default="http://10.253.0.2:47881")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("domain_runner requires --execute")
    campaign = json.loads(args.campaign.read_text())
    for filename, expected in campaign.get("orchestrator_source_sha256", {}).items():
        if Path(filename).name != filename or hashlib.sha256((HERE / filename).read_bytes()).hexdigest() != expected:
            raise SystemExit(f"frozen orchestrator source mismatch: {filename}")
    if args.run_dir.exists():
        raise SystemExit(f"raw attempt directory already exists: {args.run_dir}")
    args.run_dir.mkdir(parents=True)
    (args.run_dir / "logs").mkdir()
    source, schedule_hash = copy_inputs(args.workload_root, args.scenario, args.run_dir)
    shutil.copy2(args.campaign, args.run_dir / "campaign_snapshot.json")
    if args.assignment:
        shutil.copy2(args.assignment, args.run_dir / "assignment_snapshot.json")
    provenance = {
        "campaign_sha256": hashlib.sha256(args.campaign.read_bytes()).hexdigest(),
        "assignment_sha256": hashlib.sha256(args.assignment.read_bytes()).hexdigest()
        if args.assignment else None,
    }
    atomic_json(args.run_dir / "provenance.json", provenance)
    lab = Path(f"/data/networkcache-cross-host-federation-v1/labs/{args.role}")
    shared = lab / "shared" / SUITE
    if shared.exists():
        shutil.rmtree(shared)
    (shared / "data").mkdir(parents=True)
    (shared / "control").mkdir()
    (shared / "tcp-traces").mkdir()
    for data_file in source.joinpath("data").glob("node*.tsv"):
        shutil.copy2(data_file, shared / "data" / data_file.name)
    install_lab_support(shared)
    env = environment(args, lab, shared / "control", shared / "data", schedule_hash, args.run_dir)
    started = time.time_ns()
    attempt_id = f"{args.run_dir.parent.name}-{args.run_dir.name}"
    helpers: list[tuple[subprocess.Popen[str], IO[str]]] = []
    background: list[tuple[subprocess.Popen[str], IO[str]]] = []
    status = "FAILED"
    error = ""
    try:
        launched = run([str(RUN_SCENARIO), args.scenario], env=env, timeout=600)
        (args.run_dir / "driver.stdout.log").write_text(launched.stdout, encoding="utf-8")
        (args.run_dir / "driver.stderr.log").write_text(launched.stderr, encoding="utf-8")
        wait_startup(lab, args.role, args.method)
        run([sys.executable, str(HERE / "dci_host.py"), "--role", args.role,
             "attach-border", "--execute"])
        snapshot(args.run_dir, args.role)
        helpers = launch_helpers(args, lab, shared, schedule_hash, args.run_dir / "logs")
        monitor_log = (args.run_dir / "logs/border-monitor.log").open("w", encoding="utf-8")
        monitor = subprocess.Popen([
            sys.executable, str(HERE / "border_monitor.py"), "--role", args.role,
            "--egress-interface", (
                "br-xfed-v1-a" if args.role == "domain-a" else "br-xfed-v1-b"
            ),
            "--output", str(args.run_dir / "border_packets.jsonl"),
            "--stop-file", str(shared / "control/monitor.stop"),
            "--marker-file", str(shared / "control/monitor/current.tsv"),
        ], text=True, stdout=monitor_log, stderr=subprocess.STDOUT)
        background.append((monitor, monitor_log))
        if args.role == "domain-b":
            agent_log = (args.run_dir / "logs/control-agent.log").open("w", encoding="utf-8")
            agent = subprocess.Popen([
                sys.executable, str(HERE / "control_agent.py"), "--root",
                str(shared / "control"), "--token-file", str(args.token_file),
                "--role", "domain-b", "--border-id", "450",
                "--dci-gateway", "172.29.251.1", "--attempt-id", attempt_id,
            ], text=True, stdout=agent_log, stderr=subprocess.STDOUT)
            background.append((agent, agent_log))
            deadline = time.monotonic() + 172800
            while time.monotonic() < deadline and not (shared / "control/stop").exists():
                if agent.poll() is not None:
                    raise RuntimeError("control agent exited before coordinator finished")
                time.sleep(1)
            if not (shared / "control/stop").exists():
                raise RuntimeError("paired attempt timed out")
            pair_result = shared / "control/pair/result.json"
            final_pair = wait_for_pair_result(pair_result, agent)
            if final_pair.get("status") != "ok":
                raise RuntimeError(f"paired coordinator failed: {final_pair}")
        else:
            token = args.token_file.read_text(encoding="utf-8").strip()
            deadline = time.monotonic() + 300
            while True:
                if time.monotonic() >= deadline:
                    raise RuntimeError("domain-b control agent attempt readiness timed out")
                try:
                    request = urllib.request.Request(
                        args.remote_agent + "/health",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    with urllib.request.urlopen(request, timeout=3) as response:
                        health = json.loads(response.read())
                        if health.get("role") == "domain-b" and health.get("attempt_id") == attempt_id:
                            break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("domain-b control agent did not become ready")
                    time.sleep(1)
            controller = run([
                sys.executable, str(HERE / "distributed_controller.py"),
                "--schedule", str(source / "schedule.tsv"),
                "--isolation-probes", str(source / "isolation_probes.tsv"),
                "--local-control", str(shared / "control"),
                "--remote-agent", args.remote_agent, "--agent-token-file", str(args.token_file),
                "--output", str(args.run_dir / "request_results.csv"),
                "--method", args.method,
                "--attempt-id", attempt_id,
            ], check=False, timeout=172800)
            (args.run_dir / "controller.stdout.log").write_text(controller.stdout, encoding="utf-8")
            (args.run_dir / "controller.stderr.log").write_text(controller.stderr, encoding="utf-8")
            if controller.returncode:
                raise RuntimeError(f"controller failed with status {controller.returncode}")
        status = "CAPTURED"
    except Exception as exc:
        error = repr(exc)
        (args.run_dir / "error.txt").write_text(error + "\n", encoding="utf-8")
        if args.role == "domain-a":
            # Also publish failures before controller.main enters its try/finally.
            # The authenticated attempt id prevents stopping a different run.
            try:
                notify_failed_peer(args.remote_agent, args.token_file.read_text().strip(), attempt_id, error)
            except Exception as notification_error:
                (args.run_dir / "peer_notification_error.txt").write_text(repr(notification_error) + "\n")
    finally:
        (shared / "control/monitor.stop").touch()
        (shared / "control/stop").touch()
        for process, handle in helpers:
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.terminate()
            handle.close()
        for process, handle in background:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            handle.close()
        copy_outputs(lab, shared, args.run_dir)
        run([str(DOWN_SCENARIO)], env={**os.environ, "LAB_DIR": str(lab)}, check=False)
        atomic_json(args.run_dir / "domain_result.json", {
            "role": args.role, "method": args.method, "scenario": args.scenario,
            "status": status, "error": error, "started_epoch_ns": started,
            "finished_epoch_ns": time.time_ns(), "schedule_sha256": schedule_hash,
        })
    return 0 if status == "CAPTURED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
