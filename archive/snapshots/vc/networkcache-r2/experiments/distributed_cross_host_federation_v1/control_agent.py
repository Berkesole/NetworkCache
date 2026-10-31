#!/usr/bin/env python3
"""Restricted control-directory and border-route agent for one federation domain."""

from __future__ import annotations

import argparse
import fnmatch
import hmac
import json
import os
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


class AgentState:
    def __init__(self, root: Path, token: str, role: str, border_id: int,
                 dci_gateway: str, attempt_id: str = "") -> None:
        self.root = root.resolve()
        self.token = token
        self.role = role
        self.border_id = border_id
        self.dci_gateway = dci_gateway
        self.attempt_id = attempt_id

    def path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("path escapes the control root")
        return candidate

    def border_container(self) -> str:
        output = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            check=True, text=True, capture_output=True,
        ).stdout.splitlines()
        needle = f"node{self.border_id}"
        pattern = re.compile(rf"(?:^|_){re.escape(needle)}(?:_|$)")
        matches = [name for name in output if pattern.search(name)]
        if len(matches) != 1:
            raise RuntimeError(f"expected one live {needle} container, found {matches}")
        return matches[0]

    def activate(self) -> dict[str, Any]:
        container = self.border_container()
        remote = range(33, 65) if self.role == "domain-a" else range(1, 33)
        for node in remote:
            completed = subprocess.run(
                [
                    "docker", "exec", container, "ip", "route", "replace",
                    f"10.90.0.{node}/32", "via", self.dci_gateway,
                ],
                check=False, text=True, capture_output=True,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or f"route activation failed for {node}")
        now = time.monotonic_ns()
        atomic_write(self.root / "border-activation.tsv", f"{self.role}\t{now}\tactive\n")
        return {"status": "ok", "role": self.role, "monotonic_ns": now}

    def deactivate(self) -> dict[str, Any]:
        container = self.border_container()
        remote = range(33, 65) if self.role == "domain-a" else range(1, 33)
        for node in remote:
            subprocess.run(
                ["docker", "exec", container, "ip", "route", "del", f"10.90.0.{node}/32"],
                check=False, text=True, capture_output=True,
            )
        now = time.monotonic_ns()
        atomic_write(self.root / "border-activation.tsv", f"{self.role}\t{now}\tinactive\n")
        return {"status": "ok", "role": self.role, "monotonic_ns": now}

    def network_proof(self) -> dict[str, Any]:
        requester = 2 if self.role == "domain-a" else 34
        target = 34 if self.role == "domain-a" else 2
        names = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"], check=True,
            text=True, capture_output=True,
        ).stdout.splitlines()
        pattern = re.compile(rf"(?:^|_)node{requester}(?:_|$)")
        matches = [name for name in names if pattern.search(name)]
        if len(matches) != 1:
            raise RuntimeError(f"expected one live node{requester} container, found {matches}")
        requester_container = matches[0]
        border = self.border_container()
        results = {}
        for label, container in (("requester", requester_container), ("border", border)):
            completed = subprocess.run(
                ["docker", "exec", container, "ip", "route", "get", f"10.90.0.{target}"],
                check=False, text=True, capture_output=True,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or f"{label} route proof failed")
            results[label] = completed.stdout.strip()
        return {"status": "ok", "role": self.role, "requester": requester,
                "target": target, "routes": results}


def handler_factory(state: AgentState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CrossHostFederationAgent/1"

        def log_message(self, fmt: str, *args: object) -> None:
            return

        def reply(self, status: int, value: object) -> None:
            body = json.dumps(value, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            return hmac.compare_digest(supplied, f"Bearer {state.token}")

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4 * 1024 * 1024:
                raise ValueError("request body too large")
            value = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def do_GET(self) -> None:  # noqa: N802
            if not self.authorized():
                self.reply(403, {"error": "unauthorized"})
                return
            if self.path != "/health":
                self.reply(404, {"error": "not found"})
                return
            self.reply(200, {
                "status": "READY", "role": state.role,
                "attempt_id": state.attempt_id,
                "root": str(state.root), "monotonic_ns": time.monotonic_ns(),
            })

        def do_POST(self) -> None:  # noqa: N802
            if not self.authorized():
                self.reply(403, {"error": "unauthorized"})
                return
            try:
                request = self.read_json()
                if self.path == "/write":
                    atomic_write(state.path(str(request["path"])), str(request["text"]))
                    result: object = {"status": "ok"}
                elif self.path == "/read":
                    path = state.path(str(request["path"]))
                    try:
                        text = path.read_text(encoding="utf-8")
                    except FileNotFoundError:
                        text = ""
                    result = {"exists": bool(text) or path.is_file(), "text": text}
                elif self.path == "/exists":
                    result = {"exists": state.path(str(request["path"])).exists()}
                elif self.path == "/list":
                    directory = state.path(str(request["path"]))
                    pattern = str(request.get("pattern", "*"))
                    values = [] if not directory.is_dir() else [
                        child.name for child in directory.iterdir()
                        if fnmatch.fnmatch(child.name, pattern)
                    ]
                    result = {"entries": sorted(values)}
                elif self.path == "/unlink":
                    for relative in request.get("paths", []):
                        state.path(str(relative)).unlink(missing_ok=True)
                    result = {"status": "ok"}
                elif self.path == "/activate":
                    result = state.activate()
                elif self.path == "/deactivate":
                    result = state.deactivate()
                elif self.path == "/network-proof":
                    result = state.network_proof()
                else:
                    self.reply(404, {"error": "not found"})
                    return
                self.reply(200, result)
            except Exception as exc:
                self.reply(400, {"error": str(exc)})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--role", required=True, choices=("domain-a", "domain-b"))
    parser.add_argument("--border-id", required=True, type=int, choices=(250, 450))
    parser.add_argument("--dci-gateway", required=True)
    parser.add_argument("--bind", default="10.253.0.2")
    parser.add_argument("--port", type=int, default=47881)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    token = args.token_file.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise SystemExit("agent token must contain at least 32 characters")
    state = AgentState(
        args.root, token, args.role, args.border_id, args.dci_gateway, args.attempt_id
    )
    server = ThreadingHTTPServer((args.bind, args.port), handler_factory(state))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
