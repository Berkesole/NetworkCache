# worker-c v5 — started-request cutoff ablation

Single changed treatment: during measurement, an already-started application
request exceeding8 seconds is marked and left pending, instead of aborting the
whole run. It remains eligible for later completion. No invented latency for
unfinished requests. Setup retains v4 behavior; unstarted dispatch timeout8s,
global watchdog last planned arrival+60s, final quiescence20s, protocol query
lifetime7s and fallback1.5s remain unchanged. No new failure callback or retry.

Root `/data/networkcache-concurrency-pilot-v5/`, branch
`codex/single-cluster-concurrency-pilot-v5`, **same v4 image**
`sha256:bf9b83a12f3226ce3ea2f481fed5f407b200df668cdbb9897a441ba69bfb9dd4`.
Only new host tooling/campaign/raw; no runtime rebuild or protocol changes.

Two independent cache-cold runs: NC Poisson128 and burst128, each4096 original
seed001 requests, same arrival seed9173 and exact v4 schedule/offsets. V4
cutoff-on attempts remain immutable controls. Reuse v4 runtime/smoke evidence;
test the new cutoff-off controller with deterministic late-completion fixtures.
No extra smoke/load/method/seed matrix. No automatic retry after issued requests.

Commands from source: `python3 experiments/concurrency_pilot_v5/pilot.py`
`preflight`, `pilot --execute`, `status`, `analyze`. status is read-only;
one lab, Restart=no, one bounded startup check, no ongoing polling.

Outputs retain all4096 rows. An unmatched expired query may remain pending until
the overall watchdog; this is an observation, not permission to change its7s
protocol lifetime. Inspect completion counts, censored requests, queue/latency
curves and actual runtime together. Means/quantiles describe successes only.
`derived/cutoff_comparison.md/json` verifies identical runtime, request identities
and planned arrivals against the v4 controls. Common-success differences are
conditional single-seed summaries, not unbiased causal estimates or formal CI.
No Slides, PDF, local repository edits, or other worker changes.
