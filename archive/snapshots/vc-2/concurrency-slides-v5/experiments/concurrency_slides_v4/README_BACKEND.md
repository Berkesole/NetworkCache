# Open-arrival Slides matrix — bridge backend candidate (2026-09-15)

Only worker-c, independent branch/root. Runtime is byte-identical to v1, with a new
tag pointing to that same image ID. The failed v2 control/DATA lane change is NOT
adopted. Original protocol and all v1 source files remain unchanged.

The change is Kathara's official Linux bridge/veth backend, selected by a
campaign-local CLI wrapper and pinned plugin ID. The global Kathara config and
VDE plugin remain unchanged. Only point-to-point collision domains are permitted.
All topology devices and routes, 2 ms / 1 Gbit/s / 1000-packet netem, MTU 1500,
open arrivals, TTL 30 s, fallback 1.5 s, and TCP timeout 5 s remain fixed.
GSO/GRO/TSO are explicitly disabled on the exact lab veth pairs before application
requests so backend-specific super-packets do not alter the packet-sized netem
model. Before/after offload snapshots are retained. This change is part of the
new backend provenance, not a claim of identical underlying virtual hardware.

This is a new experimental environment. Never merge its latency measurements with
VDE results. Initial exact-failed-condition regression is diagnostic only, followed
by six smokes, all 64 fresh pilots, and then 1,280 fresh formal slots. A failed
measurement stops the queue; no success-selecting retries or validator exceptions.

Use the root's `bin` first in PATH for execution and cleanup. The wrapper blocks
non-lab commands, global configuration mutation, and automatic plugin upgrades.
`status` remains read-only. Raw attempts are append-only. No local source, other
host, historical raw, or Slides/PDF changes are made.
