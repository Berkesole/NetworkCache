# Worker-c v6: query-state lifetime sensitivity

Only protocol query lifetime changes: 7, 15, 30 seconds. Six new independent
cold-cache runs: Poisson128 at 7/15/30, burst128 at 30/15/7 (interleaved).
Original seed001 4096 requests; arrival seed9173; same v4 runtime image.
No new runtime build or change to protocol code, forwarding, cache, Bloom,
transport, node sleep25ms, link2ms/1G, fallback1500ms or tracing.

Measurement started-request abort stays disabled. Dispatch8s, setup behavior,
global last-arrival+60s watchdog and final-quiescence20s remain unchanged.
TTL applies to all protocol query states, not just requester state; longer
retention may increase memory and traffic. This is not a claim that longer is better.

Prior v5 raw is immutable provenance-checked reference; new 7s runs are the
contemporaneous controls. One seed/one repetition, no formal CI, no READY.
Runtime smokes are reused (same binary); deterministic controller tests rerun.
Timeout/transport failure is retained without rerun; correctness/process/archive
failure stops the queue. Only pre-request infrastructure errors may retry once.

Root /data/networkcache-concurrency-pilot-v6; branch
codex/single-cluster-concurrency-pilot-v6. CLI: python3
experiments/concurrency_pilot_v6/pilot.py preflight|status|analyze|pilot --execute.
One lab at a time, Restart=no, one bounded startup check, no ongoing polling.
No local repository edits, other workers, Slides, PDF or follow-on parameter sweep.

derived/lifetime_comparison.md/json includes denominator, successful latency,
expiry counts, >8s successful completions, deadline completion fractions,
per-peer enqueue-to-send residence, resource health and wall time. Batch/time
plots use existing analysis. QueryTable occupancy is not directly observable
with this image; report this limitation, never relabel app outstanding as PIT size.
