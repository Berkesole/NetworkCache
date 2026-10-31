#!/usr/bin/env python3
"""Provision one host side of the fixed WireGuard DCI and border uplink."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


INTERFACE = "wg-xfed-v1"
LISTEN_PORT = 51871


ROLES = {
    "domain-a": {
        "wg_address": "10.253.0.1/30",
        "peer_wg": "10.253.0.2",
        "allowed_ips": "10.253.0.2/32," + ",".join(
            f"10.90.0.{node}/32" for node in range(33, 65)
        ),
        "bridge_network": "xfed-v1-uplink-a",
        "bridge_name": "br-xfed-v1-a",
        "bridge_subnet": "172.29.250.0/29",
        "bridge_gateway": "172.29.250.1",
        "border_ip": "172.29.250.2",
        "border_id": 250,
        "local_nodes": range(1, 33),
        "remote_nodes": range(33, 65),
    },
    "domain-b": {
        "wg_address": "10.253.0.2/30",
        "peer_wg": "10.253.0.1",
        "allowed_ips": "10.253.0.1/32," + ",".join(
            f"10.90.0.{node}/32" for node in range(1, 33)
        ),
        "bridge_network": "xfed-v1-uplink-b",
        "bridge_name": "br-xfed-v1-b",
        "bridge_subnet": "172.29.251.0/29",
        "bridge_gateway": "172.29.251.1",
        "border_ip": "172.29.251.2",
        "border_id": 450,
        "local_nodes": range(33, 65),
        "remote_nodes": range(1, 33),
    },
}


def run(arguments: list[str], *, check: bool = True, input_text: str | None = None) -> str:
    completed = subprocess.run(
        arguments, input=input_text, text=True, capture_output=True, check=False
    )
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def require_tools() -> None:
    missing = [name for name in ("docker", "ip", "tc", "wg", "iptables") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"missing required tools: {missing}")


def interface_exists() -> bool:
    return subprocess.run(["ip", "link", "show", INTERFACE], capture_output=True).returncode == 0


def provision(role: str, private_key: Path, peer_public_key: str,
              peer_endpoint: str) -> dict[str, Any]:
    require_tools()
    values = ROLES[role]
    if not private_key.is_file():
        private_key.parent.mkdir(parents=True, exist_ok=True)
        key = run(["wg", "genkey"])
        temporary = private_key.with_name(f".{private_key.name}.tmp.{os.getpid()}")
        temporary.write_text(key + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(private_key)
    if not interface_exists():
        run(["ip", "link", "add", INTERFACE, "type", "wireguard"])
    run(["ip", "address", "replace", str(values["wg_address"]), "dev", INTERFACE])
    run(["ip", "link", "set", "dev", INTERFACE, "mtu", "1420"])
    run([
        "wg", "set", INTERFACE, "listen-port", str(LISTEN_PORT),
        "private-key", str(private_key), "peer", peer_public_key,
        "endpoint", peer_endpoint, "allowed-ips", str(values["allowed_ips"]),
        "persistent-keepalive", "25",
    ])
    run(["ip", "link", "set", "dev", INTERFACE, "up"])
    run([
        "tc", "qdisc", "replace", "dev", INTERFACE, "root", "netem",
        "delay", "2ms", "rate", "1000mbit", "limit", "1000",
    ])
    run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    if subprocess.run(
        ["docker", "network", "inspect", str(values["bridge_network"])],
        capture_output=True,
    ).returncode:
        run([
            "docker", "network", "create", "--driver", "bridge",
            "--subnet", str(values["bridge_subnet"]),
            "--gateway", str(values["bridge_gateway"]),
            "--opt", f"com.docker.network.bridge.name={values['bridge_name']}",
            "--opt", "com.docker.network.driver.mtu=1400",
            str(values["bridge_network"]),
        ])
    run(["ip", "link", "set", "dev", str(values["bridge_name"]), "mtu", "1400"])
    # Docker commonly installs a FORWARD policy that would otherwise block
    # the host-routed border/WireGuard path.  Add only the two experiment
    # interface pairs, never a blanket forwarding rule.
    bridge_name = str(values["bridge_name"])
    for incoming, outgoing in ((bridge_name, INTERFACE), (INTERFACE, bridge_name)):
        check_rule = ["iptables", "-C", "FORWARD", "-i", incoming, "-o", outgoing, "-j", "ACCEPT"]
        if subprocess.run(check_rule, capture_output=True).returncode:
            run(["iptables", "-I", "FORWARD", "1", "-i", incoming, "-o", outgoing,
                 "-j", "ACCEPT"])
    for node in values["remote_nodes"]:
        run(["ip", "route", "replace", f"10.90.0.{node}/32", "dev", INTERFACE])
    return status(role)


def border_container(border_id: int) -> str:
    names = run(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
    pattern = re.compile(rf"(?:^|_)node{border_id}(?:_|$)")
    matches = [name for name in names if pattern.search(name)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one node{border_id} container, found {matches}")
    return matches[0]


def attach_border(role: str) -> dict[str, Any]:
    values = ROLES[role]
    container = border_container(int(values["border_id"]))
    networks = json.loads(run(["docker", "inspect", container, "--format", "{{json .NetworkSettings.Networks}}"] ))
    if values["bridge_network"] not in networks:
        run([
            "docker", "network", "connect", "--ip", str(values["border_ip"]),
            str(values["bridge_network"]), container,
        ])
    uplink = run([
        "docker", "exec", container, "sh", "-c",
        f"ip -o -4 addr show | awk '$4 ~ /^{str(values['border_ip']).replace('.', r'\.')}(\\/|$)/ {{print $2}}'",
    ])
    if not uplink:
        raise RuntimeError("unable to identify border DCI uplink")
    run(["docker", "exec", container, "ip", "link", "set", "dev", uplink, "mtu", "1400"])
    run(["docker", "exec", container, "sysctl", "-w", "net.ipv4.ip_forward=1"])
    # Docker's uplink default route otherwise bypasses PRE isolation.
    from distributed_controller import LocalStore
    control = Path('/data/networkcache-cross-host-federation-v1/labs') / role / 'shared/cross-host-federation-v1/control'
    LocalStore(control, role, int(values['border_id']),
               str(values['bridge_gateway'])).deactivate()
    for node in values["local_nodes"]:
        run([
            "ip", "route", "replace", f"10.90.0.{node}/32", "via",
            str(values["border_ip"]), "dev", str(values["bridge_name"]),
        ])
    return {"status": "READY", "role": role, "border_container": container,
            "border_ip": values["border_ip"]}


def status(role: str) -> dict[str, Any]:
    values = ROLES[role]
    link = run(["ip", "-j", "link", "show", INTERFACE], check=False)
    wg = run(["wg", "show", INTERFACE, "dump"], check=False) if shutil.which("wg") else ""
    network = run([
        "docker", "network", "inspect", str(values["bridge_network"]),
        "--format", "{{.Id}}",
    ], check=False) if shutil.which("docker") else ""
    return {
        "role": role,
        "interface": INTERFACE,
        "interface_present": bool(link),
        "wireguard_dump_present": bool(wg),
        "bridge_network": values["bridge_network"],
        "bridge_present": bool(network),
        "configuration": {
            key: list(value) if isinstance(value, range) else value
            for key, value in values.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=tuple(ROLES))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    provision_parser = sub.add_parser("provision")
    provision_parser.add_argument("--execute", action="store_true")
    provision_parser.add_argument("--private-key", type=Path, required=True)
    provision_parser.add_argument("--peer-public-key", required=True)
    provision_parser.add_argument("--peer-endpoint", required=True)
    attach_parser = sub.add_parser("attach-border")
    attach_parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(status(args.role), indent=2, sort_keys=True))
        return 0
    if not args.execute:
        raise SystemExit(f"{args.command} requires --execute")
    result = (
        provision(args.role, args.private_key, args.peer_public_key, args.peer_endpoint)
        if args.command == "provision" else attach_border(args.role)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
