#!/usr/bin/env python3
"""Binary-safe TCP Direct node driven by the latency-v3 closed-loop controller."""

from __future__ import annotations

import argparse
import csv
import os
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict


MASK64 = (1 << 64) - 1


def deterministic_value(size: int, seed: int) -> bytes:
    state = seed or 0x9E3779B97F4A7C15
    output = bytearray(size)
    for index in range(size):
        state ^= (state << 13) & MASK64
        state ^= state >> 7
        state ^= (state << 17) & MASK64
        state &= MASK64
        output[index] = state & 0xFF
    return bytes(output)


def fnv1a64(data: bytes) -> str:
    value = 14695981039346656037
    for byte in data:
        value ^= byte
        value = (value * 1099511628211) & MASK64
    return f"{value:016x}"


def load_data(path: Path) -> Dict[str, bytes]:
    result: Dict[str, bytes] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            value = deterministic_value(int(row["value_size_bytes"]), int(row["content_seed"]))
            if fnv1a64(value) != row["expected_fnv1a64"]:
                raise ValueError(f"hash mismatch generating {row['key']}")
            result[row["key"]] = value
    return result


def recvall(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("truncated stream")
        chunks.extend(chunk)
    return bytes(chunks)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def server_loop(listener: socket.socket, data: Dict[str, bytes], stop: threading.Event) -> None:
    listener.settimeout(0.2)
    while not stop.is_set():
        try:
            client, _ = listener.accept()
        except socket.timeout:
            continue
        with client:
            client.settimeout(5)
            index_size, key_size = struct.unpack("!II", recvall(client, 8))
            recvall(client, index_size)
            key = recvall(client, key_size).decode()
            value = data.get(key)
            # The provider timestamp is carried in the response so TCP Direct
            # has the same predeclared discovery/return-path decomposition as
            # the NetworkCache methods. Kathara containers share the host
            # monotonic clock; the preflight/validator gates this assumption.
            provider_hit_ns = time.monotonic_ns()
            if value is None:
                client.sendall(struct.pack("!BQQ", 1, 0, provider_hit_ns))
            else:
                client.sendall(struct.pack("!BQQ", 0, len(value), provider_hit_ns) + value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node_id", type=int)
    parser.add_argument("data_file", type=Path)
    parser.add_argument("control_dir", type=Path)
    parser.add_argument("schedule_hash")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port-base", type=int, default=47000)
    args = parser.parse_args()

    data = load_data(args.data_file)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.host, args.port_base + args.node_id))
    listener.listen(128)
    stop = threading.Event()
    server = threading.Thread(target=server_loop, args=(listener, data, stop), daemon=True)
    server.start()
    atomic_write(args.control_dir / "ready" / f"node{args.node_id}.ready", f"ready\t{args.node_id}\n")

    command_dir = args.control_dir / "commands" / f"node{args.node_id}"
    command_dir.mkdir(parents=True, exist_ok=True)
    processed: set[Path] = set()
    activation_processed = False
    quiesce_seen = ""
    quiesce_seen_at = 0
    try:
        while not (args.control_dir / "stop").exists():
            activation_path = args.control_dir / "activation" / f"node{args.node_id}.tsv"
            if args.node_id in (1, 33) and not activation_processed and activation_path.exists():
                activation_processed = True
                fields = activation_path.read_text().rstrip("\n").split("\t")
                status, error = "rejected", "invalid activation command"
                peer = 33 if args.node_id == 1 else 1
                if len(fields) == 6 and int(fields[0]) == peer and fields[5] == args.schedule_hash:
                    completed = subprocess.run(["ip", "link", "set", "dev", "eth0", "up"],
                                               text=True, capture_output=True)
                    if completed.returncode == 0:
                        first, last = (33, 64) if args.node_id == 1 else (1, 32)
                        failures = []
                        for target in range(first, last + 1):
                            route = subprocess.run(["ip", "route", "replace", f"10.90.0.{target}/32",
                                                    "via", fields[4], "dev", "eth0",
                                                    "src", f"10.90.0.{args.node_id}"],
                                                   text=True, capture_output=True)
                            if route.returncode: failures.append(route.stderr.strip())
                        status, error = ("ok", "") if not failures else ("rejected", failures[0])
                    else:
                        error = completed.stderr.strip() or "failed to enable federation interface"
                atomic_write(args.control_dir / "activated" / f"node{args.node_id}.tsv",
                             f"{args.node_id}\t{peer}\t{status}\t{time.monotonic_ns()}\t{error}\n")
            for path in sorted(command_dir.glob("*.tsv")):
                if path in processed:
                    continue
                processed.add(path)
                fields = path.read_text(encoding="utf-8").rstrip("\n").split("\t")
                if len(fields) < 7 or fields[6] != args.schedule_hash:
                    continue
                index, _method, key, owner_text, expected_size, expected_hash, schedule_hash = fields[:7]
                start = time.monotonic_ns()
                status = "ok"
                value = b""
                provider_hit_ns = 0
                try:
                    with socket.create_connection((f"10.90.0.{int(owner_text)}", args.port_base + int(owner_text)), timeout=5) as sock:
                        sock.settimeout(5)
                        index_bytes, key_bytes = index.encode(), key.encode()
                        sock.sendall(struct.pack("!II", len(index_bytes), len(key_bytes)) + index_bytes + key_bytes)
                        response_status, size, provider_hit_ns = struct.unpack("!BQQ", recvall(sock, 17))
                        value = recvall(sock, size) if response_status == 0 else b""
                        if response_status != 0:
                            status = "not_found"
                except Exception:
                    status = "io_error"
                end = time.monotonic_ns()
                actual_hash = fnv1a64(value)
                if status == "ok" and (len(value) != int(expected_size) or actual_hash != expected_hash):
                    status = "integrity_mismatch"
                row = "\t".join([index, str(args.node_id), key, status, str(len(value)), actual_hash,
                                  str(start), str(end), "0", "tcp_direct", schedule_hash,
                                  str(provider_hit_ns)]) + "\n"
                atomic_write(args.control_dir / "completions" / f"{index}.tsv", row)
            markers = sorted((args.control_dir / "quiesce").glob("*.marker")) if (args.control_dir / "quiesce").exists() else []
            if markers:
                index = markers[-1].stem
                if index != quiesce_seen:
                    quiesce_seen = index
                    quiesce_seen_at = time.monotonic_ns()
                if time.monotonic_ns() - quiesce_seen_at >= 100_000_000:
                    ack = args.control_dir / "quiesced" / f"{index}-node{args.node_id}.ack"
                    if not ack.exists():
                        atomic_write(ack, f"{index}\t{args.node_id}\t{quiesce_seen_at}\n")
            time.sleep(0.01)
    finally:
        stop.set()
        server.join(timeout=2)
        listener.close()
        atomic_write(args.control_dir / "stopped" / f"node{args.node_id}.stopped",
                     f"stopped\t{args.node_id}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
