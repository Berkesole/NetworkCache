#!/usr/bin/env python3
"""Global closed-loop controller over two domain-local control directories."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterable


CONTROL_DIRS = (
    "commands", "completions", "ready", "quiesce", "quiesced", "stopped",
    "activation", "activated", "occupancy-markers", "occupancy",
    "protocol-state-markers", "protocol-state", "cancel", "cancelled",
    "monitor",
    "pair",
)
RESULT_COLUMNS = [
    "request_index", "phase", "bin", "round", "request_class", "direction", "key",
    "key_rank", "owner_local_rank", "key_u", "bloom_slot", "bloom_positions",
    "requester", "owner", "requester_cluster", "owner_cluster",
    "original_owner_overlay_hops", "overlay_edge_physical_signature",
    "expected_value_bytes", "expected_fnv1a64", "status", "actual_value_bytes",
    "actual_fnv1a64", "request_start_mono_ns", "app_receive_mono_ns",
    "end_to_end_latency_ns", "packet_request_id", "via", "schedule_sha256",
    "quiescence_ack_count", "quiescence_stable_ms",
]


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in columns} for row in rows)


class LocalStore:
    def __init__(self, root: Path, role: str, border_id: int, dci_gateway: str) -> None:
        self.root = root
        self.role = role
        self.border_id = border_id
        self.dci_gateway = dci_gateway
        for name in CONTROL_DIRS:
            (root / name).mkdir(parents=True, exist_ok=True)

    def write(self, path: str, text: str) -> None:
        atomic_write(self.root / path, text)

    def read(self, path: str) -> str | None:
        target = self.root / path
        try:
            return target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def exists(self, path: str) -> bool:
        return (self.root / path).exists()

    def list(self, path: str, pattern: str = "*") -> list[str]:
        target = self.root / path
        return sorted(child.name for child in target.glob(pattern)) if target.is_dir() else []

    def unlink(self, paths: list[str]) -> None:
        for path in paths:
            (self.root / path).unlink(missing_ok=True)

    def _container(self) -> str:
        names = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"], check=True,
            text=True, capture_output=True,
        ).stdout.splitlines()
        pattern = re.compile(rf"(?:^|_)node{self.border_id}(?:_|$)")
        matches = [name for name in names if pattern.search(name)]
        if len(matches) != 1:
            raise RuntimeError(f"expected one node{self.border_id} container, found {matches}")
        return matches[0]

    def activate(self) -> dict[str, Any]:
        container = self._container()
        remote = range(33, 65) if self.role == "domain-a" else range(1, 33)
        for node in remote:
            completed = subprocess.run(
                ["docker", "exec", container, "ip", "route", "replace",
                 f"10.90.0.{node}/32", "via", self.dci_gateway],
                check=False, text=True, capture_output=True,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or "border activation failed")
        now = time.monotonic_ns()
        self.write("border-activation.tsv", f"{self.role}\t{now}\tactive\n")
        return {"status": "ok", "role": self.role, "monotonic_ns": now}

    def deactivate(self) -> dict[str, Any]:
        container = self._container()
        remote = range(33, 65) if self.role == "domain-a" else range(1, 33)
        for node in remote:
            subprocess.run(
                ["docker", "exec", container, "ip", "route", "replace",
                 "unreachable", f"10.90.0.{node}/32"], check=True, capture_output=True,
            )
        routes = json.loads(subprocess.run(
            ["docker", "exec", container, "ip", "-j", "route", "show"],
            check=True, capture_output=True, text=True).stdout)
        blocked = {row['dst'].removesuffix('/32') for row in routes
                   if row.get('type') == 'unreachable'}
        if not {f'10.90.0.{node}' for node in remote} <= blocked:
            raise RuntimeError('incomplete PRE border isolation routes')
        self.write('pre-isolation-routes.json', json.dumps(routes, sort_keys=True))
        now = time.monotonic_ns()
        self.write("border-activation.tsv", f"{self.role}\t{now}\tinactive\n")
        return {"status": "ok", "role": self.role, "monotonic_ns": now}

    def network_proof(self) -> dict[str, Any]:
        requester, target = 2, 34
        names = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"], check=True,
            text=True, capture_output=True,
        ).stdout.splitlines()
        result = {}
        for label, node in (("requester", requester), ("border", self.border_id)):
            pattern = re.compile(rf"(?:^|_)node{node}(?:_|$)")
            matches = [name for name in names if pattern.search(name)]
            if len(matches) != 1:
                raise RuntimeError(f"expected one node{node} container")
            completed = subprocess.run(
                ["docker", "exec", matches[0], "ip", "route", "get", f"10.90.0.{target}"],
                check=False, text=True, capture_output=True,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or f"{label} route proof failed")
            result[label] = completed.stdout.strip()
        return {"status": "ok", "role": self.role, "requester": requester,
                "target": target, "routes": result}


class RemoteStore:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def call(self, endpoint: str, value: dict[str, Any] | None = None,
             method: str = "POST") -> dict[str, Any]:
        body = None if value is None else json.dumps(value).encode()
        request = urllib.request.Request(
            self.url + endpoint, data=body, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def health(self) -> dict[str, Any]:
        return self.call("/health", method="GET")

    def write(self, path: str, text: str) -> None:
        self.call("/write", {"path": path, "text": text})

    def read(self, path: str) -> str | None:
        result = self.call("/read", {"path": path})
        return result["text"] if result["exists"] else None

    def exists(self, path: str) -> bool:
        return bool(self.call("/exists", {"path": path})["exists"])

    def list(self, path: str, pattern: str = "*") -> list[str]:
        return list(self.call("/list", {"path": path, "pattern": pattern})["entries"])

    def unlink(self, paths: list[str]) -> None:
        self.call("/unlink", {"paths": paths})

    def activate(self) -> dict[str, Any]:
        return self.call("/activate", {})

    def deactivate(self) -> dict[str, Any]:
        return self.call("/deactivate", {})

    def network_proof(self) -> dict[str, Any]:
        return self.call("/network-proof", {})


def store_for_node(node: int, local: LocalStore, remote: RemoteStore):
    return local if node <= 32 else remote


def wait_for(store: Any, path: str, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if store.exists(path):
            return True
        time.sleep(0.02)
    return False


def completion(text: str) -> dict[str, str]:
    fields = text.rstrip("\n").split("\t")
    if len(fields) < 11:
        raise RuntimeError("invalid completion row")
    names = [
        "request_index", "requester", "key", "status", "actual_value_bytes",
        "actual_fnv1a64", "request_start_mono_ns", "app_receive_mono_ns",
        "packet_request_id", "via", "schedule_sha256", "provider_hit_mono_ns",
    ]
    return dict(zip(names, fields))


def quiesce(local: LocalStore, remote: RemoteStore, index: int,
            timeout_ms: int, stable_ms: int) -> bool:
    marker = f"quiesce/{index:06d}.marker"
    local.write(marker, f"{index}\n")
    remote.write(marker, f"{index}\n")
    expected = [
        (store_for_node(node, local, remote), f"quiesced/{index:06d}-node{node}.ack")
        for node in range(1, 65)
    ]

    def snapshot() -> list[str] | None:
        values = []
        for store, path in expected:
            value = store.read(path)
            if value is None:
                return None
            values.append(value)
        return values

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        first = snapshot()
        if first is not None:
            time.sleep(stable_ms / 1000)
            second = snapshot()
            if second is not None and first == second:
                local.unlink([marker] + [path for store, path in expected if store is local])
                remote.unlink([marker] + [path for store, path in expected if store is remote])
                return True
        time.sleep(0.02)
    return False


def isolation(probes: list[dict[str, str]], method: str, local: LocalStore,
              remote: RemoteStore, output: Path, timeout_ms: int,
              quiescence_timeout_ms: int, stable_ms: int) -> None:
    rows = []
    columns = [
        "probe_index", "direction", "requester", "owner", "key", "status",
        "timeout_ms", "completion_present", "quiescence_ack_count",
    ]
    for probe in probes:
        index = 900000 + int(probe["probe_index"])
        requester = int(probe["requester"])
        store = store_for_node(requester, local, remote)
        command = "\t".join([
            str(index), method, probe["key"], probe["owner"],
            probe["value_size_bytes"], probe["expected_fnv1a64"],
            probe["schedule_sha256"],
        ]) + "\n"
        store.write(f"commands/node{requester}/{index:06d}.tsv", command)
        path = f"completions/{index}.tsv"
        present = wait_for(store, path, time.monotonic() + timeout_ms / 1000)
        unexpected = False
        if present:
            actual = completion(store.read(path) or "")
            unexpected = actual["status"] == "ok" or int(actual["actual_value_bytes"] or 0) > 0
        elif method == "nc":
            store.write(f"cancel/{index}-node{requester}.marker", f"{index}\n")
            if not wait_for(store, f"cancelled/{index}-node{requester}.tsv", time.monotonic() + 5):
                raise RuntimeError(f"isolation cancel timeout for node{requester}")
        quiet = quiesce(local, remote, index, quiescence_timeout_ms, stable_ms)
        row = {key: probe.get(key, "") for key in columns}
        row.update({
            "status": "unexpected_data" if unexpected else ("ok" if quiet else "quiescence_timeout"),
            "timeout_ms": str(timeout_ms),
            "completion_present": "1" if present else "0",
            "quiescence_ack_count": "64" if quiet else "0",
        })
        rows.append(row)
        if unexpected or not quiet:
            write_csv(output, columns, rows)
            raise RuntimeError(f"pre-activation isolation failed: {row}")
    write_csv(output, columns, rows)


def activate(local: LocalStore, remote: RemoteStore, schedule_hash: str,
             stable_ms: int) -> dict[str, Any]:
    issued_epoch_ns = time.time_ns()
    border_a = local.activate()
    border_b = remote.activate()
    local.write(
        "activation/node1.tsv",
        f"33\t10.90.0.33\t44033\truntime-logical-bootstrap\texternal-border-route\t{schedule_hash}\n",
    )
    remote.write(
        "activation/node33.tsv",
        f"1\t10.90.0.1\t44001\truntime-logical-bootstrap\texternal-border-route\t{schedule_hash}\n",
    )
    acknowledgements = []
    for node, store in ((1, local), (33, remote)):
        path = f"activated/node{node}.tsv"
        if not wait_for(store, path, time.monotonic() + 30):
            raise RuntimeError(f"logical activation timeout node{node}")
        fields = (store.read(path) or "").rstrip("\n").split("\t")
        if len(fields) < 4 or fields[2] != "ok":
            raise RuntimeError(f"logical activation rejected node{node}: {fields}")
        acknowledgements.append({"node": node, "local_monotonic_ns": int(fields[3])})
    time.sleep(stable_ms / 1000)
    return {
        "activation_issued_epoch_ns": issued_epoch_ns,
        "border_acknowledgements": [border_a, border_b],
        "logical_acknowledgements": acknowledgements,
        "stable_window_ms": stable_ms,
        "route_proofs": [local.network_proof(), remote.network_proof()],
        "cross_host_duration_not_computed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--isolation-probes", required=True, type=Path)
    parser.add_argument("--local-control", required=True, type=Path)
    parser.add_argument("--remote-agent", default="http://10.253.0.2:47881")
    parser.add_argument("--agent-token-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--method", choices=("nc", "tcp-direct"), required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--timeout-ms", type=int, default=8000)
    parser.add_argument("--quiescence-timeout-ms", type=int, default=20000)
    parser.add_argument("--stable-ms", type=int, default=500)
    args = parser.parse_args()
    schedule = read_tsv(args.schedule)
    probes = read_tsv(args.isolation_probes)
    local = LocalStore(args.local_control, "domain-a", 250, "172.29.250.1")
    token = args.agent_token_file.read_text(encoding="utf-8").strip()
    remote = RemoteStore(args.remote_agent, token)
    health = remote.health()
    if health.get("role") != "domain-b" or health.get("attempt_id") != args.attempt_id:
        raise RuntimeError("remote agent does not belong to this paired attempt")
    for node in range(1, 65):
        store = store_for_node(node, local, remote)
        if not wait_for(store, f"ready/node{node}.ready", time.monotonic() + 300):
            raise RuntimeError(f"ready timeout node{node}")

    rows: list[dict[str, Any]] = []
    failed = False
    failure_reason = ""
    previous_end = {"A": 0, "B": 0}
    activation_record: dict[str, Any] | None = None
    try:
        for request in schedule:
            if request["phase"] == "post" and activation_record is None:
                isolation(
                    probes, args.method, local, remote,
                    args.output.parent / "isolation_results.csv", args.timeout_ms,
                    args.quiescence_timeout_ms, args.stable_ms,
                )
                activation_record = activate(
                    local, remote, request["schedule_sha256"], args.stable_ms
                )
            index = int(request["request_index"])
            requester = int(request["requester"])
            store = store_for_node(requester, local, remote)
            marker = "\t".join([
                str(index), args.method, request["phase"], request["request_class"],
                request["direction"], request["key"], request["requester"],
                request["owner"], request["schedule_sha256"],
            ]) + "\n"
            # The experiment is globally closed loop, so one marker safely tags
            # every DCI packet without changing the application protocol.
            local.write("monitor/current.tsv", marker)
            remote.write("monitor/current.tsv", marker)
            store.write(
                f"commands/node{requester}/{index:06d}.tsv",
                "\t".join([
                    str(index), args.method, request["key"], request["owner"],
                    request["value_size_bytes"], request["expected_fnv1a64"],
                    request["schedule_sha256"],
                ]) + "\n",
            )
            row: dict[str, Any] = dict(request)
            row["expected_value_bytes"] = request["value_size_bytes"]
            path = f"completions/{index}.tsv"
            if not wait_for(store, path, time.monotonic() + args.timeout_ms / 1000):
                row["status"] = "timeout"
                rows.append(row)
                failed = True
                break
            actual = completion(store.read(path) or "")
            row.update(actual)
            start = int(actual["request_start_mono_ns"])
            end = int(actual["app_receive_mono_ns"])
            row["end_to_end_latency_ns"] = str(end - start)
            domain = "A" if requester <= 32 else "B"
            if previous_end[domain] and start < previous_end[domain]:
                row["status"] = "overlap"
            previous_end[domain] = end
            rows.append(row)
            if row["status"] != "ok" or actual["schedule_sha256"] != request["schedule_sha256"]:
                failed = True
                break
            if not quiesce(local, remote, index, args.quiescence_timeout_ms, args.stable_ms):
                row["status"] = "quiescence_timeout"
                failed = True
                break
            row["quiescence_ack_count"] = "64"
            row["quiescence_stable_ms"] = str(args.stable_ms)
            local.unlink(["monitor/current.tsv"])
            remote.unlink(["monitor/current.tsv"])
            time.sleep(0.1)
    except Exception as exc:
        failed = True
        failure_reason = repr(exc)
        atomic_write(args.output.parent / "controller-error.txt", failure_reason + "\n")
    finally:
        try:
            local.unlink(["monitor/current.tsv"])
            remote.unlink(["monitor/current.tsv"])
        except Exception:
            failed = True
        local.write("stop", "stop\n")
        remote.write("stop", "stop\n")
        for store in (local, remote):
            try:
                store.deactivate()
            except Exception:
                failed = True
        deadline = time.monotonic() + 60
        for node in range(1, 65):
            store = store_for_node(node, local, remote)
            if not wait_for(store, f"stopped/node{node}.stopped", deadline):
                failed = True
        write_csv(args.output, RESULT_COLUMNS, rows)
        atomic_write(
            args.output.parent / "activation.json",
            json.dumps(activation_record or {}, indent=2, sort_keys=True) + "\n",
        )
        completed = sum(row.get("status") == "ok" for row in rows)
        atomic_write(
            args.output.parent / "completion_summary.json",
            json.dumps({
                "scheduled": len(schedule), "issued": len(rows), "completed": completed,
                "completion_rate_schedule": completed / len(schedule),
            }, indent=2, sort_keys=True) + "\n",
        )
        pair_value = {
            "status": "ok" if not failed and len(rows) == len(schedule) else "failed",
            "requests": len(rows),
            "scheduled": len(schedule),
            "schedule_sha256": schedule[0]["schedule_sha256"] if schedule else "",
            "error": failure_reason,
        }
        pair_result = json.dumps(pair_value, sort_keys=True) + "\n"
        # Publish only after both domains have emitted every stopped ACK.  This
        # is the final remote operation, allowing Domain B to keep its control
        # agent alive throughout Domain A's shutdown validation.
        local.write("pair/result.json", pair_result)
        try:
            remote.write("pair/result.json", pair_result)
        except Exception as exc:
            failed = True
            pair_value["status"] = "failed"
            pair_value["error"] = repr(exc)
            local.write("pair/result.json", json.dumps(pair_value, sort_keys=True) + "\n")
    return 2 if failed or len(rows) != len(schedule) else 0


if __name__ == "__main__":
    raise SystemExit(main())
