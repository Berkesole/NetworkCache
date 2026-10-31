#!/usr/bin/env python3
"""Controlled RESP proxy used to inject REAL Redis faults deterministically.

It listens on a Unix socket, forwards a synchronous request/reply stream to the
real Redis socket, and can inject a fault for a specific request:

  control/drop_next_reply          forward the next request, DISCARD its reply,
                                   then close the client connection.
  control/drop_on_command:<CMD>    same, for the first request whose command is
                                   <CMD> (e.g. SET); the trigger is consumed.
  control/close_next               close the client connection WITHOUT forwarding.

Every forwarded request is logged as JSON with the command, whether it was
forwarded to Redis (i.e. Redis executed it) and whether its reply was dropped.
Because the proxy only drops the reply AFTER forwarding the bytes, an
independent client can verify the server-side effect -- that is the
"command executed but response lost" window, not a generic broken pipe.

Usage: redis_fault_proxy.py <listen_sock> <upstream_sock> <control_dir> <log.jsonl>
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path


def read_exact(sock: socket.socket, count: int) -> bytes:
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            return buf
        buf += chunk
    return buf


def read_line(sock: socket.socket) -> bytes:
    buf = b""
    while not buf.endswith(b"\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            return buf
        buf += chunk
    return buf


def read_request(sock: socket.socket) -> tuple[bytes, str]:
    """Read one complete RESP request. Returns (bytes, command_name)."""
    header = read_line(sock)
    if not header:
        return b"", ""
    data = header
    command = ""
    if header[:1] != b"*":
        return data, ""
    try:
        count = int(header[1:].strip())
    except ValueError:
        return data, ""
    for index in range(count):
        part_header = read_line(sock)
        data += part_header
        if not part_header:
            return data, command
        if part_header[:1] == b"$":
            try:
                length = int(part_header[1:].strip())
            except ValueError:
                return data, command
            chunk = read_exact(sock, length + 2)
            data += chunk
            if index == 0:
                command = chunk[:length].decode("utf-8", "replace").upper()
        elif part_header[:1] in (b"+", b":"):
            if index == 0:
                command = part_header[1:].strip().decode("utf-8", "replace").upper()
    return data, command


def read_reply(sock: socket.socket) -> bytes:
    line = read_line(sock)
    if not line:
        return b""
    kind = line[:1]
    if kind in (b"+", b"-", b":"):
        return line
    if kind == b"$":
        try:
            length = int(line[1:].strip())
        except ValueError:
            return line
        if length < 0:
            return line
        return line + read_exact(sock, length + 2)
    if kind == b"*":
        try:
            count = int(line[1:].strip())
        except ValueError:
            return line
        out = line
        for _ in range(count):
            out += read_reply(sock)
        return out
    return line


class Proxy:
    def __init__(self, listen: Path, upstream: Path, control: Path, log: Path):
        self.listen = listen
        self.upstream = upstream
        self.control = control
        self.log_path = log
        self.log_lock = threading.Lock()

    def _log(self, entry: dict) -> None:
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")

    def _consume_trigger(self, command: str) -> str:
        if (self.control / "close_next").exists():
            (self.control / "close_next").unlink()
            return "close"
        if (self.control / "drop_next_reply").exists():
            (self.control / "drop_next_reply").unlink()
            return "drop"
        for trigger in sorted(self.control.glob("drop_on_command:*")):
            wanted = trigger.name.split(":", 1)[1].upper()
            if wanted == command:
                trigger.unlink()
                return "drop"
        return ""

    def handle(self, client: socket.socket) -> None:
        try:
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            upstream.connect(str(self.upstream))
        except OSError as exc:
            self._log({"event": "upstream_connect_failed", "error": str(exc)})
            client.close()
            return
        try:
            while True:
                request, command = read_request(client)
                if not request:
                    break
                trigger = self._consume_trigger(command)
                if trigger == "close":
                    self._log({"event": "request", "command": command,
                               "forwarded": False, "reply_dropped": False,
                               "client_closed": True})
                    break
                try:
                    upstream.sendall(request)
                except OSError as exc:
                    self._log({"event": "forward_failed", "command": command,
                               "error": str(exc)})
                    break
                reply = read_reply(upstream)
                if not reply:
                    self._log({"event": "reply_missing", "command": command,
                               "forwarded": True, "reply_dropped": False})
                    break
                if trigger == "drop":
                    self._log({"event": "request", "command": command,
                               "forwarded": True, "reply_dropped": True,
                               "client_closed": True})
                    break
                self._log({"event": "request", "command": command,
                           "forwarded": True, "reply_dropped": False})
                try:
                    client.sendall(reply)
                except OSError:
                    break
        finally:
            try:
                upstream.close()
            except OSError:
                pass
            client.close()

    def serve_forever(self) -> None:
        if self.listen.exists():
            self.listen.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.listen))
        server.listen(8)
        self._log({"event": "proxy_started", "listen": str(self.listen),
                   "upstream": str(self.upstream)})
        while True:
            client, _ = server.accept()
            threading.Thread(target=self.handle, args=(client,), daemon=True).start()


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__)
        return 2
    proxy = Proxy(Path(argv[1]), Path(argv[2]), Path(argv[3]), Path(argv[4]))
    proxy.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))