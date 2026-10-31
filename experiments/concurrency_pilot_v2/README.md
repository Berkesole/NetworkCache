# Worker-c open-arrival pilot v2

Purpose: test completion/correctness, cache warmup versus queue growth, and wall
time under independent arrivals, including multiple requests at the same node.
This is one seed and one run per condition, not a formal capacity claim.

Root: `/data/networkcache-concurrency-pilot-v2/`.
Branch: `codex/single-cluster-concurrency-pilot-v2`.
The v1 source, image and raw remain untouched. `recovery/` holds an independent
verification of its interrupted C4 archive and exact-lab cleanup. Those old
measurements are not v2 acceptance tests and do not support cross-version speedup.

## Commands

Run from `source/experiments/concurrency_pilot_v2/`:

```
python3 pilot.py preflight
python3 pilot.py recover --execute
python3 pilot.py smoke --execute
python3 pilot.py pilot --execute
python3 pilot.py status
python3 pilot.py analyze
```

`status` reads only. `pilot` first checks both new smoke gates, then runs 12 load
conditions: Poisson/burst × 8/32/128 requests/s × NC/TCP Direct. Conditions are
ordered by rate, with alternating methods. The original seed001 4096-request
identity sequence, stratified endpoints and arrival seed9173 are frozen. Each
condition starts with fresh caches, with setup excluded from measurement.

No fixed concurrency target, global cap or per-node single-inflight cap exists.
All due arrivals are admitted and dispatched regardless of outstanding requests.
Node's single event loop admits up to16 new commands per iteration and continues
protocol processing; this is a scheduling budget, not an outstanding limit.
TCP client and server both use asynchronous IO, uncached independent owner fetches.

Poisson interarrivals use Exp(lambda). Burst alternates4 seconds at lambda/4 and
1 second at4lambda with frozen initial phase. These are sensitivity models,
not production trace fits. The finite4096 requests bound the experiment; no
automatic extension or convergence pass threshold is applied.

## Application identity and unchanged policy

`requestFileAsync(key, app_request_id)` registers each call independently before
possible synchronous cache delivery. Remote application IDs map to local origin
UIDs. Preferred satisfaction can complete multiple actual origin queries; each
application ID receives one completion. Fallback completes only its associated
origin UID. Wire request IDs are not used as guesses for application identity.
No extra application-level coalescing is added. Existing routing/cache/Bloom/
QueryTable/UnionWindow policies and the old public API are retained.

The runtime marker `control/async-v2.enabled` selects the new experiment entry;
legacy experiment callers keep their old entry semantics. NC is still driven
on one thread; this pilot does not introduce concurrent access to Node state.

Smoke uses4 groups of4 requests after32 setup requests: staggered same-node
different cold keys, same-node same cold key, multi-node same cold key, and a
repeated now-warm key. NC's final group must use local cache; TCP still fetches
the owner. Actual same-node execution intervals must overlap. Diagnostic objects
are not shared across unrelated cold groups and smoke never enters load summaries.

## Failure and collection

8 seconds after node start or unstarted dispatch aborts the run; the global
watchdog is last planned arrival+60 seconds. IO failures/timeouts are outcomes,
not integrity failures or proof of a crash. No load failure retries. Higher rates
for that method/model are skipped; its peer method/model can continue. Corruption,
duplicate application delivery, process death, controller or finalization failure
halts the queue. Only pre-request infrastructure failures may retry once.

Save `measurement_outcome.json` before stop/archive; wait for node acknowledgements,
stop only this lab's exact containers, preserve non-temporary evidence, and always
attempt exact-lab cleanup. Finalization status never overwrites measurement status.
An unfinished raw attempt or failed cleanup forbids automatic continuation.

All planned requests remain in the ledger, including transport errors, timeouts,
uncompleted, unissued and not-yet-arrived-at-abort rows. Quantiles describe successes
only, with the full denominator beside them. p99 is exploratory. Detailed traces
and client hash validation have observer cost; this is not production maximum throughput.

## Outputs and interpretation

`derived/report.md` and `.json` link per-run summaries, hashes, CSVs and SVGs.
Report five timestamps, user/execution/ingress/scheduler latency, input and output
rates, actual global/per-node overlap, pending work, cache service, fallback and
protocol frames/bytes, CPU/memory and process/OOM evidence. Frame bytes exclude
IP/TCP overhead and retransmission. There is no global-current-request provider
decomposition in this concurrent experiment.

Batch cohorts contain128 original-order requests.4-batch rolling trends and the
first/last512 requests are descriptive. Time plots also show queue growth;
burst reports include complete phase-aligned5-second cycles. Inspect cache and
execution trends separately from queue and user latency. Stable cache behavior
alone does not establish a stable queue; periodic bursts do not imply instability.

Record startup, setup, measurement, arrival window, post-arrival drain, final
quiescence, finalization and total monotonic wall time. Nominal arrival windows
512/128/32 seconds are not end-to-end ETA. Do not claim convergence or equivalence
to serial latency automatically. No slides, PDF, multi-seed CI or FORMAL READY.

Service: `networkcache-concurrency-pilot-v2`, `Restart=no`, one lab at a time.
After launch use only one bounded confirmation; no recurring polling.
