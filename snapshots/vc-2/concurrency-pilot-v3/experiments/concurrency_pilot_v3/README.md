# worker-c v3: matched serial/open-arrival comparison

Only `/data/networkcache-concurrency-pilot-v3/`, branch
`codex/single-cluster-concurrency-pilot-v3`, new image and campaign.
Parent v2 sources, images and attempts remain unchanged.

The live TCP receive queue previously violated the documented entry-snapshot
batch boundary. During both v2 NC r32 failures node5 continued processing
packets for the entire eight-second timeout (largest trace gap 45/35 ms),
but did not return to application admission or flush aggregation. v3 freezes
the number of receive frames at each `processOne()` entry. It does not cap
application concurrency or change forwarding, caching, fallback parameters,
QueryTable, Bloom, UnionWindow or the eight-second request timeout.

Acceptance: five C++ test executables, Python controller/analysis tests, both
multi-inflight smokes, and full 4096-request NC Poisson/burst r32 reproductions.
A failed reproduction stops the gate and must not be described as repaired.
These r32 runs count once in the new load matrix; no automatic reruns.

After gates pass, `pilot --execute` resumes two 4096-request serial baselines
(NC/TCP, no per-request quiescence) and the twelve open-arrival conditions
(Poisson/burst x 8/32/128 rps x NC/TCP). Serial release is triggered by the
previous application completion. Open arrivals remain independent of completion.
All full runs share original seed001 request identities and new image/campaign.
Setup and cache initialization are repeated independently for every run.

Commands: `python3 experiments/concurrency_pilot_v3/pilot.py`
with `preflight`, `smoke --execute`, `repair-check --execute`,
`pilot --execute`, `status`, or `analyze`. Use Restart=no systemd units;
one lab, no persistent status polling. Never launch pilot until repair gates pass.

`derived/time_comparison.json` and `.md` report measurement and total wall-time
ratios only for complete same-version matched runs. A paced arrival rate is
not a maximum throughput claim. Failed runs retain all planned requests and
are not eligible for speedup claims. No formal READY, cross-seed CI or PDF.
