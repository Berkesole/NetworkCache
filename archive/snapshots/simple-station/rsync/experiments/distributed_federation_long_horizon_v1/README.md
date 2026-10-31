# Single-host Logical Federation · Long-horizon formal v3

This suite is the formal successor to the one-seed convergence pilot on
`simple-station`. It is isolated from the completed Federation-v4 raw data and
from the pilot raw attempts.

## Frozen matrix

- one physical server, two logical 32-node Federation domains;
- 512 objects per domain, Zipf alpha 1.1;
- NetworkCache and TCP Direct use the same seed schedule;
- 10 seeds x 2 repetitions x 2 methods = 40 selected runs;
- 8 PRE and 64 POST batches, 11,264 requests per run;
- BF1024 h3, wire v3, 64 KiB DATA, 2 ms / 1 Gbit links;
- global closed loop and at most one Kathara lab;
- at most four append-only attempts per slot.

The v2 execution policy waits for all 64 TCP Direct helpers to publish their
ready markers before the controller may issue request 1. The superseded v1 raw
attempts remain immutable and are labelled `PRIOR_CAMPAIGN`; they do not consume
the v2 attempt budget and are never mixed into the v2 selection.
Failures before the runtime-image snapshot (for example a service PATH that
cannot resolve Kathara) are likewise retained as `NON_EVIDENCE` diagnostics.

The v3 policy additionally normalizes the container-created control tree to a
host-controller-writable mode after all helpers are ready, verifies the command
and completion paths, and archives any controller exception in the raw attempt.
The complete v2 NC seed001 attempt is explicitly compatible because this change
is confined to the TCP Direct helper branch; no v2 TCP attempt is compatible.

The user accepted 64 POST batches on 2026-09-09 because the pilot's late
remote-latency windows reached the local-control range. The original mechanism
stationarity decision remains recorded as a diagnostic and is not rewritten.
The pilot attempt is never eligible for formal selection.

## Interface

```bash
python3 experiments/content_discovery/delay_find_content/scripts/generate_federation_long_horizon_v1.py
python3 experiments/distributed_federation_long_horizon_v1/runner.py freeze --write
python3 experiments/distributed_federation_long_horizon_v1/runner.py preflight
python3 experiments/distributed_federation_long_horizon_v1/runner.py run --execute
python3 experiments/distributed_federation_long_horizon_v1/runner.py status
```

`status` is strictly read-only. A complete publication requires 40/40 selected
runs; until then the evidence level is `COLLECTING_NO_PARTIAL_STATS`.
