#!/usr/bin/env python3
"""Deploy, gate, launch, inspect, and collect Cross-host Federation-v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any

import generate_workloads
import queue_runner
import spec


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
REMOTE_REPO = "/root/zzh/rsync"
REMOTE_ROOT = "/data/networkcache-cross-host-federation-v1"
REMOTE_ORCHESTRATOR = f"{REMOTE_REPO}/experiments/distributed_cross_host_federation_v1"
REMOTE_WORKLOAD = f"{REMOTE_ROOT}/workloads"
REMOTE_RESULTS = f"{REMOTE_ROOT}/results-pre-isolation-v2"
LOCAL_COLLECTION = HERE / "collected-pre-isolation-v2"
SIMPLE_ATTEMPTS = (
    "/home/simpleai/zhengzihao/rsync/experiments/content_discovery/"
    "delay_find_content/results/federation-convergence-pilot/attempts"
)
WORKERS = {"domain-a": "volcengine", "domain-b": "volcengine-1"}


def command(arguments: list[str], *, check: bool = True,
            timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments, text=True, capture_output=True, check=False, timeout=timeout
    )
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def replicate_image(source_host: str, destination_host: str, image: str) -> None:
    source = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", source_host,
         f"docker save {shlex.quote(image)} | gzip -1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert source.stdout is not None
    destination = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", destination_host, "gzip -d | docker load"],
        stdin=source.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    source.stdout.close()
    try:
        destination_stdout, destination_stderr = destination.communicate(timeout=1800)
        source_stderr = source.stderr.read() if source.stderr else b""
        source_status = source.wait(timeout=60)
    except BaseException:
        destination.terminate()
        source.terminate()
        destination.wait(timeout=10)
        source.wait(timeout=10)
        raise
    if source_status or destination.returncode:
        raise RuntimeError(
            (source_stderr + destination_stderr + destination_stdout).decode(errors="replace")
        )


def ssh(host: str, remote: str, *, check: bool = True,
        timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return command(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, remote],
                   check=check, timeout=timeout)


def simple_report() -> dict[str, Any] | None:
    # Attempts are append-only; consume the newest completed report instead of
    # pinning the gate to attempt001 (which may be an intentionally retained
    # failed attempt).
    root = shlex.quote(SIMPLE_ATTEMPTS)
    result = ssh(
        "simple-station",
        f"report=$(find {root} -path '*/convergence_analysis/convergence_report.json' "
        "-type f -print 2>/dev/null | sort | tail -n 1); "
        'test -n "$report" && cat "$report"',
        check=False,
    )
    if result.returncode:
        return None
    value = json.loads(result.stdout)
    return value if isinstance(value, dict) else None


def remote_preflight(role: str) -> dict[str, Any]:
    host = WORKERS[role]
    script = r'''set -eu
role="$1"
old_r2="networkcache-r2-worker-${role#domain-}.service"
old_fed="networkcache-federation-v4-worker-${role#domain-}.service"
printf 'memory_kib=%s\n' "$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
printf 'disk_bytes=%s\n' "$(df -B1 --output=avail /data | tail -1 | tr -d ' ')"
printf 'old_r2=%s\n' "$(systemctl is-active "$old_r2" 2>/dev/null || true)"
printf 'old_federation=%s\n' "$(systemctl is-active "$old_fed" 2>/dev/null || true)"
printf 'new_active_units=%s\n' "$(systemctl list-units "networkcache-cross-host-v1-${role}-*" --state=active --no-legend --no-pager 2>/dev/null | wc -l)"
printf 'kathara_containers=%s\n' "$(docker ps --format '{{.Names}}' | grep -c kathara || true)"
for tool in docker kathara ip tc wg iptables python3; do command -v "$tool" >/dev/null 2>&1 && printf 'tool_%s=ok\n' "$tool" || printf 'tool_%s=missing\n' "$tool"; done
printf 'image_id=%s\n' "$(docker image inspect --format '{{.Id}}' rsync-router:cross-host-federation-v1 2>/dev/null || true)"
printf 'dci_interface=%s\n' "$(ip link show wg-xfed-v1 >/dev/null 2>&1 && echo present || echo missing)"
''' 
    # Pass a fixed script body; no user-controlled shell fragment enters it.
    completed = ssh(host, "role=" + shlex.quote(role) + "; " + script.replace('role="$1"', ':'), check=False)
    values = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    ping = ssh(host, "ping -n -c 7 -i 0.2 -W 2 " +
               ("10.253.0.2" if role == "domain-a" else "10.253.0.1"),
               check=False, timeout=20)
    samples = [float(value) for value in re.findall(r"time=([0-9.]+) ms", ping.stdout)]
    shaped_rtt_ms = statistics.median(samples) if samples else None
    from validate_attempt import dci_shaping_ok
    shaping = ssh(host, "tc -j qdisc show dev wg-xfed-v1", check=False)
    shaping_ok = shaping.returncode == 0 and dci_shaping_ok({"tc": {"stdout": shaping.stdout}})
    ready = (
        completed.returncode == 0
        and int(values.get("memory_kib", "0") or 0) >= 16 * 1024 * 1024
        and int(values.get("disk_bytes", "0") or 0) >= 200 * 1024**3
        and values.get("old_r2") != "active" and values.get("old_federation") != "active"
        and values.get("new_active_units") == "0"
        and all(values.get(f"tool_{tool}") == "ok"
                for tool in ("docker", "kathara", "ip", "tc", "wg", "iptables", "python3"))
        and values.get("image_id", "").startswith("sha256:")
        and values.get("dci_interface") == "present"
        and values.get("kathara_containers") == "0"
        and ping.returncode == 0 and shaped_rtt_ms is not None and shaping_ok
    )
    return {"role": role, "ssh": host, "ready": ready, "checks": values,
            "shaped_dci_rtt_median_ms": shaped_rtt_ms,
            "shaped_dci_samples": len(samples), "dci_shaping_config_ok": shaping_ok,
            "ping_diagnostic": ping.stdout.strip(),
            "stderr": completed.stderr.strip()}


def deploy() -> dict[str, Any]:
    workload = REPO / "experiments/content_discovery/delay_find_content/workloads/cross-host-federation-v1"
    for batches, seeds_to_generate in ((4, [1]), (64, [1, 2, 3])):
        for seed in seeds_to_generate:
            generate_workloads.generate_one(workload, seed, batches, "CALIBRATION")
            generate_workloads.validate_one(workload, seed, batches)
    # Install only the missing host packages and keep all files under the new root.
    for host in WORKERS.values():
        ssh(host, "apt-get update >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard-tools iptables >/dev/null",
            timeout=600)
        ssh(host, f"mkdir -p {REMOTE_ROOT}/secrets {REMOTE_ROOT}/results {REMOTE_ROOT}/workloads")
        command(["rsync", "-a", "--exclude=__pycache__", "--exclude=collected/",
                 "--exclude=derived/", str(HERE) + "/",
                 f"{host}:{REMOTE_ORCHESTRATOR}/"])
        for relative in (
            ".dockerignore", "CMakeLists.txt", "Dockerfile.latency-v3", "main.cpp",
            "Dockerfile.cross-host-federation-v1",
            "app/", "src/", "cmake/", "docker/",
            "scripts/kathara/render_ruikekkk_scenario.sh",
            "scripts/kathara/run_ruikekkk_scenario.sh",
            "scripts/kathara/down_ruikekkk_scenario.sh",
            "experiments/content_discovery/delay_find_content/scripts/latency_v3_tcp_node.py",
            "experiments/content_discovery/delay_find_content/scripts/federation_v3_cache_probe.py",
        ):
            command(["rsync", "-aR", relative, f"{host}:{REMOTE_REPO}/"], timeout=120)
        command(["rsync", "-a", str(workload) + "/", f"{host}:{REMOTE_WORKLOAD}/"], timeout=300)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(secrets.token_hex(32) + "\n")
        token_path = Path(handle.name)
    token_path.chmod(0o600)
    try:
        for host in WORKERS.values():
            command(["scp", str(token_path), f"{host}:{REMOTE_ROOT}/secrets/agent.token"])
            ssh(host, f"chmod 600 {REMOTE_ROOT}/secrets/agent.token")
    finally:
        token_path.unlink(missing_ok=True)
    public_keys = {}
    for role, host in WORKERS.items():
        key = f"{REMOTE_ROOT}/secrets/{role}.key"
        pub = f"{REMOTE_ROOT}/secrets/{role}.pub"
        public_keys[role] = ssh(
            host,
            f"umask 077; test -s {key} || wg genkey >{key}; wg pubkey <{key} >{pub}; cat {pub}",
        ).stdout.strip()
    campaign = spec.load_config()
    for role, host in WORKERS.items():
        peer = "domain-b" if role == "domain-a" else "domain-a"
        peer_endpoint = campaign["workers"][peer]["public_endpoint"]
        ssh(host, " ".join(shlex.quote(value) for value in [
            "/usr/bin/python3", f"{REMOTE_ORCHESTRATOR}/dci_host.py", "--role", role,
            "provision", "--execute", "--private-key", f"{REMOTE_ROOT}/secrets/{role}.key",
            "--peer-public-key", public_keys[peer], "--peer-endpoint", peer_endpoint,
        ]), timeout=120)
    ssh(WORKERS["domain-a"],
        f"cd {REMOTE_REPO} && docker build --no-cache -f Dockerfile.cross-host-federation-v1 "
        f"-t rsync-router:cross-host-federation-v1 .", timeout=3600)
    replicate_image(WORKERS["domain-a"], WORKERS["domain-b"],
                    "rsync-router:cross-host-federation-v1")
    return {"status": "DEPLOYED", "preflight": preflight()}


def preflight() -> dict[str, Any]:
    campaign = spec.load_config()
    report = simple_report()
    workers = [remote_preflight(role) for role in WORKERS]
    image_ids = {item["checks"].get("image_id") for item in workers}
    shared_image = len(image_ids) == 1 and "" not in image_ids
    return {
        "status": "READY" if report is not None and shared_image
        and all(item["ready"] for item in workers)
        else "BLOCKED",
        "simple_station_report": "READY" if report is not None else "PENDING",
        "simple_station_candidate": None if report is None else report.get("earliest_recommended_post_batches"),
        "pre_horizon_fingerprint": spec.pre_horizon_fingerprint(campaign),
        "shared_runtime_image": next(iter(image_ids)) if shared_image else None,
        "workers": workers,
    }


def unit_name(role: str, stage: str) -> str:
    return f"networkcache-cross-host-v1-{role}-{stage}"


def sync_stage_inputs(stage: str, post_batches: int, seeds: list[int],
                      shared_image: str) -> None:
    workload = REPO / "experiments/content_discovery/delay_find_content/workloads/cross-host-federation-v1"
    evidence = "FORMAL" if stage == "formal" else "CALIBRATION"
    for seed in seeds:
        generate_workloads.generate_one(workload, seed, post_batches, evidence)
        generate_workloads.validate_one(workload, seed, post_batches)
    if stage == "formal":
        campaign = json.loads((HERE / "frozen_campaign.json").read_text(encoding="utf-8"))
        if campaign.get("runtime_image_id") != shared_image:
            raise RuntimeError("frozen campaign runtime image does not match both workers")
        for role in WORKERS:
            if not (HERE / f"assignment-{role}.json").is_file():
                raise RuntimeError(f"missing frozen assignment for {role}")
    for host in WORKERS.values():
        command(["rsync", "-a", "--exclude=__pycache__", "--exclude=collected/",
                 "--exclude=derived/", str(HERE) + "/",
                 f"{host}:{REMOTE_ORCHESTRATOR}/"], timeout=120)
        command(["rsync", "-a", str(workload) + "/", f"{host}:{REMOTE_WORKLOAD}/"],
                timeout=300)


def stage_gate_ready(stage: str, readiness: dict[str, Any]) -> bool:
    infrastructure_ready = (
        readiness["shared_runtime_image"] is not None
        and all(item["ready"] for item in readiness["workers"])
    )
    return infrastructure_ready and (
        stage == "smoke" or readiness["simple_station_report"] == "READY"
    )


def start(stage: str, post_batches: int, seeds: list[int]) -> dict[str, Any]:
    readiness = preflight()
    # A four-POST smoke validates only the distributed topology and control
    # path, so it is independent of the convergence-horizon report.  Calibration
    # and formal collection retain the report/frozen-campaign gates.
    if not stage_gate_ready(stage, readiness):
        raise RuntimeError(f"preflight blocked: {json.dumps(readiness, sort_keys=True)}")
    sync_stage_inputs(stage, post_batches, seeds, str(readiness["shared_runtime_image"]))
    attempt_floor = 1
    if stage == "smoke":
        if len(seeds) != 1:
            raise RuntimeError("smoke recovery requires exactly one seed")
        slot = f"nc-seed{seeds[0]:03d}-post{post_batches:03d}"
        indexes = []
        for role, host in WORKERS.items():
            root = f"{REMOTE_RESULTS}/{stage}/{role}/{slot}"
            listed = ssh(
                host,
                f"find {shlex.quote(root)} -mindepth 1 -maxdepth 1 -type d "
                "-name 'attempt[0-9][0-9][0-9]' -printf '%f\\n' 2>/dev/null",
                check=False,
            )
            indexes.extend(
                int(match.group(1)) for name in listed.stdout.splitlines()
                if (match := re.fullmatch(r"attempt([0-9]{3})", name))
            )
        attempt_floor = max(indexes, default=0) + 1
        if attempt_floor > queue_runner.SMOKE_MAX_ATTEMPTS:
            raise RuntimeError(
                f"smoke recovery attempt {attempt_floor} exceeds diagnostic budget "
                f"{queue_runner.SMOKE_MAX_ATTEMPTS}"
            )
    for role, host in reversed(list(WORKERS.items())):
        unit = unit_name(role, stage)
        active = ssh(host, f"systemctl is-active {shlex.quote(unit)}", check=False).stdout.strip()
        if active == "active":
            raise RuntimeError(f"{unit} is already active")
        # Completed/failed systemd-run services are transient.  Clear their
        # retained state before recreating the same stable unit name.
        ssh(host, f"systemctl reset-failed {shlex.quote(unit)}", check=False)
        argv = [
            "/usr/bin/python3", f"{REMOTE_ORCHESTRATOR}/queue_runner.py",
            "--role", role, "--stage", stage, "--post-batches", str(post_batches),
            "--seeds", ",".join(map(str, seeds)), "--workload-root", REMOTE_WORKLOAD,
            "--results-root", REMOTE_RESULTS, "--token-file", f"{REMOTE_ROOT}/secrets/agent.token",
            "--campaign", f"{REMOTE_ORCHESTRATOR}/" + (
                "frozen_campaign.json" if stage == "formal" else "campaign.json"
            ),
            "--execute",
        ]
        if stage == "smoke":
            argv[-1:-1] = ["--attempt-floor", str(attempt_floor)]
        if stage == "formal":
            argv[ -1:-1] = ["--assignment", f"{REMOTE_ORCHESTRATOR}/assignment-{role}.json"]
        remote = "systemd-run --unit=" + shlex.quote(unit) + " --property=Restart=no " + " ".join(
            shlex.quote(value) for value in argv
        )
        ssh(host, remote)
    return status()


def status() -> dict[str, Any]:
    workers = []
    for role, host in WORKERS.items():
        units = {}
        for stage in ("smoke", "calibration", "formal"):
            unit = unit_name(role, stage)
            result = ssh(
                host,
                f"systemctl show {shlex.quote(unit)} --property=ActiveState,SubState,Result,ExecMainStatus --no-pager",
                check=False,
            )
            units[stage] = dict(
                line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
            )
        counts = ssh(
            host,
            f"find {REMOTE_RESULTS} -name pair_result.json -type f -print0 2>/dev/null | "
            "xargs -0 -r grep -l '\"status\": \"ok\"' | wc -l",
            check=False,
        ).stdout.strip()
        workers.append({"role": role, "ssh": host, "units": units,
                        "captured_attempt_files": int(counts or 0)})
    return {"evidence_status": "COLLECTING_NO_PARTIAL_STATS", "workers": workers}


def collect() -> dict[str, Any]:
    LOCAL_COLLECTION.mkdir(parents=True, exist_ok=True)
    copied = []
    for role, host in WORKERS.items():
        destination = LOCAL_COLLECTION / role
        destination.mkdir(parents=True, exist_ok=True)
        completed = command([
            "rsync", "-a", "--ignore-existing",
            f"{host}:{REMOTE_RESULTS}/", str(destination) + "/",
        ])
        copied.append({"role": role, "destination": str(destination),
                       "stdout": completed.stdout.strip()})
    return {"status": "COLLECTED_UNVALIDATED", "copies": copied}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    deploy_parser = sub.add_parser("deploy")
    deploy_parser.add_argument("--execute", action="store_true")
    for name in ("smoke", "calibrate", "run"):
        item = sub.add_parser(name)
        item.add_argument("--execute", action="store_true")
        if name == "calibrate":
            item.add_argument("--post-batches", type=int, default=64, choices=(64, 96))
            item.add_argument("--seeds", default="1,2,3")
    sub.add_parser("status")
    sub.add_parser("collect")
    args = parser.parse_args()
    if args.command == "preflight":
        result = preflight()
    elif args.command == "deploy":
        if not args.execute:
            raise SystemExit("deploy requires --execute")
        result = deploy()
    elif args.command == "status":
        result = status()
    elif args.command == "collect":
        result = collect()
    else:
        if not args.execute:
            raise SystemExit(f"{args.command} requires --execute")
        if args.command == "smoke":
            result = start("smoke", 4, [1])
        elif args.command == "calibrate":
            seeds = [int(value) for value in args.seeds.split(",") if value]
            result = start("calibration", args.post_batches, seeds)
        else:
            frozen = HERE / "frozen_campaign.json"
            if not frozen.is_file():
                raise SystemExit("formal run is blocked until frozen_campaign.json exists")
            campaign = json.loads(frozen.read_text(encoding="utf-8"))
            result = start("formal", int(campaign["formal_post_batches"]), list(spec.SEEDS))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
