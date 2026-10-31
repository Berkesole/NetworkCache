# worker-c concurrency-slides-v2: transport repair revision

Independent root /data/networkcache-concurrency-slides-v2, branch
codex/concurrency-slides-v2. Old v1 source/image/raw remain unchanged. User
authorized transport changes on 2026-09-15 after the Uniform16 Poisson128 NC
failure. Local repository, other hosts, workload, slides and PDFs are untouched.

Original v1: 4076/4096,20 natural expiries; passive reproduction:4089/4096,
7 expiries. Hot TCP sockets accumulated about4MiB Send-Q, with retransmissions
and virtual-interface TX drops. Mixed per-peer FIFO sends control behind DATA.
This does not prove that all overload is an implementation defect.

Candidate repair: independent persistent control(REQUEST/SYNC) and DATA lanes
per neighbor. FIFO within each lane; both lanes serviced fairly. No packet drop,
replay, compression, batching/coalescing or application dedup added. Keep one
frame per lane per IO tick,IO sleep1ms,Node poll25ms,Nagle defaults,link2ms/1G,
TTL30s,fallback1.5s,wire3,BF1024h3,cache/QueryTable/UnionWindow unchanged.
Cross-class ordering is no longer guaranteed by one shared TCP stream; the
protocol must already handle independent network arrivals. Packet wire format
and endpoints are unchanged. TCP Direct remains uncached, independent per
request,5s exchange timeout. The experiment is not identical to old v1 transport.

Per-frame transport.csv records unique node/frame IDs,lane,wire request ID,
queue residence,send calls,EAGAIN count and queue depth. It does not infer a
concurrent application's provider by matching a key. Diagnostic kernel ss/tc/ip
snapshots are passive and are not part of formal latency decomposition.

New regression tests deliberately block a DATA socket and verify control still
progresses; mixed traffic checks FIFO within each lane,exact IDs and64KiB values,
connection reuse and cleanup. Existing partial-send/no-replay and protocol tests
remain required. A failed-condition reproduction on this new image is an
independent diagnostic,never selected as a pilot or formal run.

Only after the targeted reproduction qualifies do we start a new queue:
6 network smoke ->64 single-seed pilots ->1280 selected formal runs.
All stages rerun on one new frozen image; no old qualified runs are imported.
Matrix,workload identity,arrival seed9172+seed,models/rates,horizons and statistical
hierarchy exactly match v1. No lower rate,longer timeout or convergence gate.
Any failed measurement stops; only pre-request infrastructure failure may retry
once. Formal CI only at1280/1280. Pilot-derived disk/ETA gate retained.

CLI: python3 source/experiments/concurrency_slides_v2/pilot.py
preflight|status|analyze; smoke|pilot|run|campaign require --execute.
Restart=no,one lab,one bounded startup confirmation,no ongoing polling.
Current verification/freeze/recovery evidence is kept separately under this root.
