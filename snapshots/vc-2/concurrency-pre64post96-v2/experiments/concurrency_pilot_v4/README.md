# worker-c v4: matched serial/open-arrival comparison

Only `/data/networkcache-concurrency-pilot-v4/`, branch
`codex/single-cluster-concurrency-pilot-v4`, new image and campaign.
Parent v2 and intermediate v3 sources, images and attempts remain unchanged.

The live TCP receive queue previously violated the documented entry-snapshot
batch boundary. During both v2 NC r32 failures node5 continued processing
packets for the entire eight-second timeout (largest trace gap 45/35 ms),
but did not return to application admission or flush aggregation. v4 freezes
the number of receive frames at each `processOne()` entry. It does not cap
application concurrency. The snapshot-only v3 reproduction still timed out:
request99 began at node10, but reverse-path DATA reached node9 after 8.77 seconds.
Per-packet synchronous TCP connect/send occupied the protocol thread for roughly
8–20 ms each, accumulating queueing at relays. v4 moves TCP connect/send into
the existing IO event loop with nonblocking sockets and per-peer FIFO queues.
It retains one connection per frame; no new pooling, coalescing, dropped frames
or application concurrency cap. Pending output participates in quiescence;
wire-send trace is recorded only after actual complete socket submission.
No forwarding, caching, fallback parameters, QueryTable, Bloom, UnionWindow or
eight-second request timeout changes. This is a new transport implementation
and image, so all serial/open comparisons must use v4.

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

Commands: `python3 experiments/concurrency_pilot_v4/pilot.py`
with `preflight`, `smoke --execute`, `repair-check --execute`,
`pilot --execute`, `status`, or `analyze`. Use Restart=no systemd units;
one lab, no persistent status polling. Never launch pilot until repair gates pass.

`derived/time_comparison.json` and `.md` report measurement and total wall-time
ratios only for complete same-version matched runs. A paced arrival rate is
not a maximum throughput claim. Failed runs retain all planned requests and
are not eligible for speedup claims. No formal READY, cross-seed CI or PDF.
