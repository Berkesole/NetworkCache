# Single-cluster concurrency pilot v1 (worker-c only)

This isolated branch snapshots the effective, dirty worker-c source. The old
repository, images and raw attempts are not changed. See ../../.. /baseline for
original HEAD, binary diff, status, and per-file hashes (absolute root below).

Root: `/data/networkcache-concurrency-pilot-v1/`; code: `source/`; lab and raw
are independent from every prior suite. One lab at a time. No formal READY,
no multi-seed confidence intervals, no slides or PDF output.

```
cd /data/networkcache-concurrency-pilot-v1/source
python3 experiments/concurrency_pilot_v1/pilot.py preflight
python3 experiments/concurrency_pilot_v1/pilot.py smoke --execute
python3 experiments/concurrency_pilot_v1/pilot.py pilot --execute
python3 experiments/concurrency_pilot_v1/pilot.py status
python3 experiments/concurrency_pilot_v1/pilot.py analyze
```

`pilot` includes any unfinished smoke gates. A failed correctness gate blocks
all continuation. Only pre-request infrastructure failure can retry once. Raw
attempts never overwrite; a timeout is evidence, not an excuse to retry. Its
arrival model skips higher rates, while equal-rate peer-method and the other
arrival model remain eligible. Resume never hides a previous failure.

32 protocol nodes, 8 leaf and 4 spine; 512 objects, 64KiB, BF1024 h3, wire v3,
2ms/1Gbit, 1500 MTU. Original immutable seed001 4096-request Zipf identity order
is kept. Endpoints are stratified as in R2, not unconstrained IID. Setup keys
are disjoint from measurement. Setup is excluded and drained before measuring.

6 smoke runs: 256 requests each, serial+500ms stable+100ms think, C1 no silence,
C4 no silence, NC and TCP. 12 open-load runs: 4096 requests each, Poisson/burst
at aggregate 8/32/128 req/s, NC/TCP. 2 cold-wave runs: width 2/4/8/16, distinct
never-requested catalog keys, nonowner requesters grouped by leaf where possible.
Every run is freshly initialized, and method order alternates by config.

Poisson intervals Exp(lambda); burst piecewise Poisson alternates 4 seconds at
lambda/4 and 1 second at 4lambda, with seed-fixed starting phase. Arrival seed
9173 is independent of key selection. These are synthetic sensitivity models,
not production trace fits. Offered timestamps never wait for completion.

Per-node ingress FIFO and one active protocol request are deliberate prototype
limits. Report latency from scheduled arrival, not merely node execution.
Arrival/admission/issue/node-start/application-end clocks and all failed or
unissued rows are preserved. 8 seconds after node start (or dispatch if startup
marker never arrives) aborts the run; open-load watchdog is last arrival+60s.
Completed-only percentiles retain explicit failure denominators. p99 from these
small samples is exploratory. Phase boundaries drain, not each request/batch.

Instrumentation adds atomic node-start markers, append-only application delivery
audit, and sent application-frame traces. NC core behavior is unchanged. Frame
bytes are not IP throughput, and do not count retransmissions. Detailed event
traces flush each event; this observer cost is part of all pilot conditions and
precludes interpreting throughput as a production capacity limit. TCP keeps its
existing serialized provider loop and uncached owner fetch behavior.

Reports in `derived-bootstrap-v2/report.json` and `derived-bootstrap-v2/report.md` include completion,
queue-inclusive latency, component times, observed concurrency, cache-hit path,
packet traffic, wall time. Raw timelines hold host CPU/memory, load, pending and
active counts. No per-request provider decomposition uses the old global marker.

Background unit: `networkcache-concurrency-pilot-v1.service`, Restart=no.
`status` reads snapshots and does not start containers or write files.

## Bootstrap repair · 2026-09-12

The first execution failed twice before issuing any requests: its independent
lab omitted `/shared/start-node.sh` and `/shared/start-switch.sh`. Those failed
attempts remain untouched under `raw/` and `lab/`, with their original campaign
snapshots. This was a deployment omission, not a measured protocol failure.

Execution revision `bootstrap-v2` installs both executable bootstraps before
launch, checks their shell syntax in preflight, and records their copied hashes
in each raw attempt. It uses `raw-bootstrap-v2/`, `lab-bootstrap-v2/`, and
`derived-bootstrap-v2/`. The new campaign records its parent campaign, while
preserving the same compiled runtime image and frozen workload. Each configuration
still permits at most one pre-request infrastructure retry within this revision;
correctness/controller failures never silently resume. The 20-condition matrix
is unchanged, and no failed measurement is replaced by this repair.

Wall time now includes file preparation, startup, setup, measurement, evidence
preservation and cleanup, measured with the host monotonic clock. Separate
startup, setup/drain and preserve/cleanup durations accompany measurement time.
