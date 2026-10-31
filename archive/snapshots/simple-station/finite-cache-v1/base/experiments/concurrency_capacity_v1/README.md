# Concurrency capacity campaign (capacity / burst)

Measures how much concurrent open-arrival load the NetworkCache prototype
absorbs before completion degrades, queueing diverges or a run fails to drain —
and how it recovers from bursts. It is a **load-capacity** study, not a latency
comparison: the slides-v5 campaign fixes two rates, this one sweeps up to the
point of overload.

## Where it runs

| Item | Value |
|---|---|
| Host | `simple-station` (run as **root**: `nsenter` needs CAP_SYS_ADMIN) |
| Campaign root | `/home/simpleai/zhengzihao/networkcache-concurrency-capacity-v1` |
| Source branch | `simple-station/concurrency-capacity-v1` (from `simple-station/concurrency-slides-v5`) |
| Runtime image | `rsync-router:concurrency-slides-v5` (`sha256:e88a677b98…`, identical to slides-v5) |
| Local mirror branch | `experiments/concurrency-capacity-v1` (framework only) |

Reused verbatim from slides-v5: `controller.py`, `analysis.py`, `tcp_node.py`,
`federation.py`, `network_backend.py`, `kathara_bridge.py`, `start-node.sh`.
`base_manifest.json` pins their hashes and `tests/test_capacity_reports.py`
asserts they were not modified.

## Experiment matrix

Zipf-IID α=1.1, 512 objects, 64 KiB, BF1024-h3, wire v3, 25 ms polling,
30 s query lifetime, 1.5 s fallback, watchdog = last arrival + 60 s,
`request_timeout_abort=False`.

| Family | Hosts | Methods | Arrival | λ ladder (req/s) | Seeds |
|---|---|---|---|---|---|
| capacity | 16, 32 | nc, tcp-direct | Poisson | 32, 64, 128, 256, 512, 1024, 2048, 4096 | 5 |
| burst | 16, 32 | nc, tcp-direct | piecewise burst | 64, 128, 256 | 5 |

NC uses the full ladder; **TCP Direct is capped at 2048**. At 4096 req/s on 16
hosts the seed-001 pilot exhausted the TCP helper's file descriptors
(`OSError: [Errno 24] Too many open files`) after 48 % completion. The helper
opens one connection per request with no client-side cap *by design*, so this is
a harness ceiling rather than a protocol result: TCP Direct's capacity cannot be
measured above 2048 with the current harness. NC completed 4096 cleanly.

42 conditions. Stages: `smoke` 6 · `pilot` 42 (seed 001) · `formal` 210.
The rate is the **global lab arrival rate**; NC and TCP Direct share the same
planned arrivals.

### Window policy

One frozen pass is 4096 measurement requests, so a pass at rate `r` lasts
`4096/r` seconds. High rates would be too short to show backlog, so the pass is
repeated until the window reaches 30 s, capped at 8 passes so a single run stays
bounded:

| λ | 32 | 64 | 128 | 256 | 512 | 1024 | 2048 | 4096 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| repeats | 1 | 1 | 1 | 2 | 4 | 8 | 8 | 8 |
| requests | 4096 | 4096 | 4096 | 8192 | 16384 | 32768 | 32768 | 32768 |
| window (s) | 128 | 64 | 32 | 32 | 32 | 32 | 16 | 8 |

The last two rates hit the pass cap (`window_floor_hit`); they are overload
probes where the signal is completion, drain and backlog slope rather than a
30 s steady state. The pilot showed NC still draining at 1024 while TCP Direct
already accumulated backlog, which is why the ladder was extended.

`capacity_inputs.expand_measurement` relabels only `request_index`/`bin`/`round`;
key, owner, requester, size, expected hash and `schedule_sha256` are copied
byte-for-byte, so the controller's identity check still passes.

### Trace policy

Event traces dominate on-disk cost and the capacity question is answered by the
request ledger plus the 100 ms resource timeline (`timeline.jsonl`: host CPU,
MemAvailable, per-node in-flight, outstanding). Traces stay **off** except one
mechanism subset (`capacity-n32-poisson-r128-{nc,tcp-direct}`). An absent trace
directory is recorded as `traces.absent.json`, not treated as missing evidence.

## Gating semantics

`capacity_reports.validate_capacity` differs from the slides validator in one
deliberate way: an **overloaded run is a data point, not a failure**.

- accepted: `status ∈ {ok, load_timeout}`; outcome `clean` or `overload`
- still enforced: frozen campaign/image/backend/offload/netem provenance,
  arrival manifests match `controller.offsets`, request clock order and latency
  arithmetic, exactly-once delivery audit, no OOM, no fatal, and for federation
  the isolation/activation ordering plus per-method probe evidence
- incomplete requests stay in the `completion_fraction` denominator

## Capacity definitions (fixed before looking at data)

- `λ_done` — largest λ where every seed completes 100 %.
- `λ_stable` — largest λ with drain ≤ 2.0 s and steady backlog slope ≤ +0.05 req/s.
- `λ_latency` — smallest λ whose p95 exceeds 3× the lowest-rate p95.
- `host_limited` — true when host CPU peak reaches 90 % at or below `λ_done`;
  such a point is **not** interpreted as a protocol limit.

`capacity_analysis` also reports the elbow (`knee`) of the drain and p95 curves.

## Running

```bash
ROOT=/home/simpleai/zhengzihao/networkcache-concurrency-capacity-v1
SUITE=$ROOT/source/experiments/concurrency_capacity_v1/pilot.py
sudo -E env PATH=$ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  NC_ROOT=$ROOT python3 -u $SUITE preflight
sudo -E env PATH=$ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  NC_ROOT=$ROOT python3 -u $SUITE smoke --execute
sudo -E env PATH=$ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  NC_ROOT=$ROOT python3 -u $SUITE pilot --execute     # 36
sudo -E env PATH=$ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  NC_ROOT=$ROOT python3 -u $SUITE run --execute       # 180 formal slots
```

`status` and `analyze` are read-only. A measurement/correctness failure stops the
queue and needs manual investigation; only infrastructure failures before any
request was issued are retried once. After a non-measurement failure, clear
`raw/<stage>`, `lab` and `derived` before re-running (the queue refuses a second
attempt for a slot).

## Outputs

`derived/`: `run_status.csv`, `selection.json`, `checkpoint.json`, `report.md`,
and after analysis `derived/capacity/{capacity_curves.csv, knees.csv, runs.csv,
capacity.json, CAPACITY_RESULTS.md, capacity-*-n*.svg}` plus per-run
`derived/<stage>/<slot>/<attempt>/{summary.json, capacity_record.json,
validation.json, time_timeline.csv, load.svg, backlog.svg, latency.svg}`.

## Tests

```bash
cd experiments/concurrency_capacity_v1
PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py'
```

Covers matrix enumeration, the repeats/window policy, schedule expansion
identity, the validator decision table and arrival-ladder split, the federation
probe-exclusion regression, and the knee/CI estimation.