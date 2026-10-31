# Open-arrival Slides matrix — bridge backend candidate (2026-09-15)

## 2026-09-16: direct-file logging candidate

This revision inherits v4's bridge backend and workload. The only inherited source
change is `start-node.sh`: launch the same binary directly into a regular log file,
without a shell pipeline invoking `date` for every stdout/stderr line. Human log
bytes are preserved but no wrapper timestamps are added. Authoritative epoch and
monotonic timestamps remain in the unmodified structured trace. Real child PID
supervision replaces supervision of a logging shell. The 8 s dispatch deadline,
30 s query lifetime, receive-batch snapshot, UnionWindow scope, polling cadence,
all protocol algorithms and the image ID are unchanged. Existing v4 raw is not
reclassified/imported. A new 64-host failed-condition diagnostic precedes fresh
smokes, pilots and formal data under a new campaign.

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
