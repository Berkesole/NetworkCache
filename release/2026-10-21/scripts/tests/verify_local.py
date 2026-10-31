#!/usr/bin/env python3
"""Check the core, MockNetwork, and three-process TCP paths using local resources."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def clean_environment():
    # Avoid inheriting a developer's runtime overrides into a reproducible test.
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("RSYNC_", "REDIS_", "TEST_REDIS_"))}


def run_checked(command):
    result = subprocess.run([str(arg) for arg in command], cwd=ROOT,
                            env=clean_environment(), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=20)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command}\n{result.stdout}")
    return result.stdout


def require(text, marker, label):
    if marker not in text:
        raise RuntimeError(f"{label}: missing {marker!r}\n{text}")


def check_core(build):
    print(run_checked([build / "test_core"]), end="")
    print("PASS: core cache and Bloom-filter tests")


def check_mock(build):
    for name in ("3-node-topology.yaml", "triangle-topology.yaml"):
        output = run_checked([build / "rsync-router", ROOT / "config" / name])
        require(output, "[Node 1] Received DATA for key=redis_key_a", name)
        require(output, 'content="VALUE_OF_A"', name)
        require(output, "[main] done.", name)
        print(f"PASS: MockNetwork delivery ({name})")


def wait_for_log(proc, path, marker, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = path.read_text(errors="replace")
        if marker in text:
            return
        if proc.poll() is not None:
            raise RuntimeError(f"Node exited before {marker!r}:\n{text}")
        time.sleep(0.02)
    raise RuntimeError(f"Timed out waiting for {marker!r}:\n{path.read_text(errors='replace')}")


def check_tcp(build):
    cache = build / "CMakeCache.txt"
    if not cache.is_file():
        raise RuntimeError("TCP check requires a CMake build directory with Redis disabled.")
    if "ENABLE_REDIS:BOOL=ON" in cache.read_text():
        raise RuntimeError("Use a separate build with -DENABLE_REDIS=OFF for this TCP check.")
    with tempfile.TemporaryDirectory(prefix="networkcache-check-") as directory:
        work = Path(directory)
        reservations = []
        children = []
        handles = []
        logs = {}
        try:
            # Reserve distinct ephemeral loopback ports until each child is launched.
            for _ in range(3):
                reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                reservation.bind(("127.0.0.1", 0))
                reservations.append(reservation)
            topology = work / "topology.yaml"
            lines = ["nodes:"]
            for index, neighbors in enumerate(([2], [1, 3], [2])):
                port = reservations[index].getsockname()[1]
                lines += [f"- id: {index + 1}", "  host: 127.0.0.1",
                          f"  port: {port}", f"  neighbors: {neighbors}"]
            topology.write_text("\n".join(lines) + "\n")
            for node_id in (3, 2, 1):
                log = work / f"node{node_id}.log"
                logs[node_id] = log
                handle = log.open("w")
                handles.append(handle)
                args = [str(build / "rsync-node-dev"), "--node-id", str(node_id),
                        "--transport", "tcp", "--topology", str(topology),
                        "--iterations", "800" if node_id != 1 else "200",
                        "--sleep-ms", "5"]
                marker = "cache backend: in-memory"
                if node_id == 3:
                    args += ["--seed", "artifact_key=SUPPLEMENTAL_VALUE"]
                    marker = "seeded local key=artifact_key"
                if node_id == 1:
                    args += ["--request-keys", "artifact_key"]
                reservations[node_id - 1].close()
                proc = subprocess.Popen(args, cwd=ROOT, env=clean_environment(),
                                        stdout=handle, stderr=subprocess.STDOUT)
                children.append(proc)
                wait_for_log(proc, log, marker)
            for proc in children:
                if proc.wait(timeout=10) != 0:
                    raise RuntimeError("A TCP node returned a nonzero exit code.")
            provider = logs[3].read_text()
            relay = logs[2].read_text()
            requester = logs[1].read_text()
            require(provider, "Sending DATA for key=artifact_key to Node 2", "provider")
            require(relay, "Forwarding DATA for key=artifact_key to Node 1", "relay")
            require(requester, "[Node 1] Received DATA for key=artifact_key", "requester")
            require(requester, 'content="SUPPLEMENTAL_VALUE"', "requester value")
            print("PASS: TCP delivery across three processes (1 -> 2 -> 3 -> 2 -> 1)")
        except Exception:
            for node_id, log in sorted(logs.items()):
                print(f"--- node {node_id} ---\n{log.read_text(errors='replace')}", file=sys.stderr)
            raise
        finally:
            for proc in children:
                if proc.poll() is None:
                    proc.terminate()
            for proc in children:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            for handle in handles:
                handle.close()
            for reservation in reservations:
                reservation.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--case", choices=("all", "core", "mock", "tcp"), default="all")
    args = parser.parse_args()
    build = args.build_dir.resolve()
    checks = {"core": check_core, "mock": check_mock, "tcp": check_tcp}
    try:
        for name, check in checks.items():
            if args.case in ("all", name):
                check(build)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: all selected functional checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
