#!/usr/bin/env python3
"""Passively timestamp inner IPv4 frames on the WireGuard DCI interface."""

from __future__ import annotations

import argparse
import json
import select
import socket
import struct
import time
from pathlib import Path


OBSERVER_POLICY = "cross-host-border-observer-v3-tcp-sequence"
MARKER_COLUMNS = (
    "request_index", "method", "phase", "request_class", "request_direction",
    "key", "requester", "owner", "schedule_sha256",
)


def read_marker(path: Path) -> dict[str, str]:
    try:
        fields = path.read_text(encoding="utf-8").rstrip("\n").split("\t")
    except FileNotFoundError:
        return {}
    if len(fields) != len(MARKER_COLUMNS):
        return {"marker_error": "invalid_field_count"}
    return dict(zip(MARKER_COLUMNS, fields))


def parse_ipv4(packet: bytes) -> dict[str, object] | None:
    offset = 14 if len(packet) >= 14 and packet[12:14] == b"\x08\x00" else 0
    if len(packet) < offset + 20 or packet[offset] >> 4 != 4:
        return None
    ihl = (packet[offset] & 0x0F) * 4
    if ihl < 20 or len(packet) < offset + ihl:
        return None
    total_length = struct.unpack_from("!H", packet, offset + 2)[0]
    identification = struct.unpack_from("!H", packet, offset + 4)[0]
    fragment = struct.unpack_from("!H", packet, offset + 6)[0]
    protocol = packet[offset + 9]
    source = socket.inet_ntoa(packet[offset + 12:offset + 16])
    destination = socket.inet_ntoa(packet[offset + 16:offset + 20])
    result: dict[str, object] = {
        "src": source,
        "dst": destination,
        "ip_protocol": protocol,
        "ip_total_bytes": total_length,
        "ip_identification": identification,
        "fragment_offset": fragment & 0x1FFF,
        "more_fragments": bool(fragment & 0x2000),
    }
    transport = offset + ihl
    if (fragment & 0x1FFF) == 0 and protocol in (6, 17) and len(packet) >= transport + 4:
        result["src_port"], result["dst_port"] = struct.unpack_from("!HH", packet, transport)
    if (fragment & 0x1FFF) == 0 and protocol == 6 and len(packet) >= transport + 14:
        result["tcp_flags"] = packet[transport + 13]
        result["tcp_sequence"], result["tcp_acknowledgment"] = struct.unpack_from(
            "!II", packet, transport + 4
        )
        tcp_header = (packet[transport + 12] >> 4) * 4
        result["transport_payload_bytes"] = max(
            0, total_length - ihl - tcp_header
        ) if tcp_header >= 20 else 0
    elif protocol in (6, 17):
        result["transport_payload_bytes"] = max(0, total_length - ihl)
    return result


def federation_direction(source: str, destination: str) -> str | None:
    """Return the logical domain direction for a node-loopback packet."""
    try:
        source_node = int(source.rsplit(".", 1)[1])
        destination_node = int(destination.rsplit(".", 1)[1])
    except (IndexError, ValueError):
        return None
    if not (source.startswith("10.90.0.") and destination.startswith("10.90.0.")):
        return None
    if 1 <= source_node <= 32 and 33 <= destination_node <= 64:
        return "A_to_B"
    if 33 <= source_node <= 64 and 1 <= destination_node <= 32:
        return "B_to_A"
    return None


def capture_direction(role: str, logical_direction: str) -> str:
    local_to_remote = "A_to_B" if role == "domain-a" else "B_to_A"
    return "egress" if logical_direction == local_to_remote else "ingress"


def logical_packet_type(marker: dict[str, str], logical_direction: str) -> str:
    request_direction = marker.get("request_direction", "")
    if request_direction not in ("A_to_B", "B_to_A"):
        return ""
    return "REQUEST" if logical_direction == request_direction else "DATA"


def open_packet_socket(interface: str) -> socket.socket:
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    sock.bind((interface, 0))
    sock.setblocking(False)
    return sock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingress-interface", default="wg-xfed-v1")
    parser.add_argument("--egress-interface", required=True)
    parser.add_argument("--role", required=True, choices=("domain-a", "domain-b"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    parser.add_argument("--marker-file", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Linux exposes decrypted WireGuard packets on the receiving host, but it
    # does not expose the corresponding clear-text transmit packet through the
    # same AF_PACKET socket.  Capture ingress on WireGuard and egress where the
    # local border container enters the host bridge.  Both timestamps therefore
    # remain on the relevant host's monotonic clock.
    sockets = {
        open_packet_socket(args.ingress_interface): (args.ingress_interface, "ingress"),
        open_packet_socket(args.egress_interface): (args.egress_interface, "egress"),
    }
    with args.output.open("x", encoding="utf-8", buffering=1) as handle:
        while not args.stop_file.exists():
            readable, _, _ = select.select(list(sockets), [], [], 0.2)
            if not readable:
                continue
            for sock in readable:
                packet, address = sock.recvfrom(65535)
                decoded = parse_ipv4(packet)
                if decoded is None:
                    continue
                logical_direction = federation_direction(
                    str(decoded["src"]), str(decoded["dst"])
                )
                if logical_direction is None:
                    continue
                interface, expected_capture_direction = sockets[sock]
                direction = capture_direction(args.role, logical_direction)
                # Avoid duplicate ingress observations from the host bridge and
                # retain only the clock boundary declared for each interface.
                if direction != expected_capture_direction:
                    continue
                marker = read_marker(args.marker_file)
                stream = ":".join(str(decoded.get(field, "")) for field in (
                    "ip_protocol", "src", "src_port", "dst", "dst_port"
                ))
                decoded.update({
                    "ts_mono_ns": time.monotonic_ns(),
                    "ts_epoch_ns": time.time_ns(),
                    "role": args.role,
                    "direction": direction,
                    "logical_direction": logical_direction,
                    "logical_packet_type": logical_packet_type(marker, logical_direction),
                    "capture_interface": interface,
                    "link_packet_type": address[2],
                    "wire_stream": stream,
                    "observer_policy": OBSERVER_POLICY,
                })
                decoded.update(marker)
                handle.write(json.dumps(decoded, sort_keys=True) + "\n")
    for sock in sockets:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
