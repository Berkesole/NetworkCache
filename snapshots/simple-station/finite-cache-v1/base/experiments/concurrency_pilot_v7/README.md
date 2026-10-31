# Worker-c v7: node polling interval sensitivity

Six independent cold-cache runs: Poisson128 with node loop sleep 25/5/1ms;
burst128 with 1/5/25ms, interleaved. New 25ms runs are contemporaneous controls;
v6 30s-TTL results remain immutable provenance-checked references.

Fixed: query lifetime30s, fallback1.5s, 4096 original seed001 requests,
arrival seed9173, 32 protocol nodes, same v4 runtime image, original fabric,
2ms/1G links, Zipf1.1, 512 objects, 64KiB, BF1024h3, trace flush1.
No fixed application concurrency, no single-node in-flight cap.
No connection reuse, protocol code changes or image build. Admission budget16
commands per loop remains fixed. Polling changes batch/aggregation opportunities
as well as scheduling latency; traffic and mechanism counters are required.

Retain measurement started-request abort disabled, dispatch8s guard,
setup behavior, last-arrival+60s watchdog, final quiescence20s, stable500ms.
Load timeouts are observations, never automatic reruns. Correctness/process or
finalization failure stops the queue. Only pre-request infrastructure faults
may retry once. Raw is append-only, one lab per worker, Restart=no.

Runtime smokes are reused from unchanged binary; all controller regression
tests rerun. Before requests, all32 runtime logs must match TTL30s, fallback1.5s
and that condition's polling interval. No additional smoke or load matrix.

Root /data/networkcache-concurrency-pilot-v7, branch
codex/single-cluster-concurrency-pilot-v7. From source run:
python3 experiments/concurrency_pilot_v7/pilot.py preflight
python3 experiments/concurrency_pilot_v7/pilot.py pilot --execute
python3 experiments/concurrency_pilot_v7/pilot.py status
python3 experiments/concurrency_pilot_v7/pilot.py analyze

Status is read-only; startup gets one bounded confirmation, no ongoing polling.
derived/polling_comparison.md/json includes completion denominators, success-only
latency, early/late cohorts, expiry, transport queues, traffic, CPU/memory and
startup/setup/measurement/finalization/wall times. CPU mean includes drain and
wait; not an equal-duration CPU comparison. QueryTable live size is unobserved.
Single seed, one repetition; no formal CI, READY, maximum-capacity or universal
performance claim. No local repository, old raw, a/b, simple-station, Slides
or PDF modifications. Do not automatically proceed to transport changes.
