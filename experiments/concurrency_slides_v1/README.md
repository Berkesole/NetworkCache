# worker-c concurrency-slides-v1

Independent replay of experiment-review families. ONLY worker-c (volcengine-3,
simpleifz), new branch codex/concurrency-slides-v1, root
/data/networkcache-concurrency-slides-v1. No edits to local repository, old raw,
other workers, Slides or PDFs. Source derives from verified v9; runtime from
v8 sha256:daa791093c488e86343fd90edcb1f3dae56e072c9012799f6bd3d29bf80d8c8e.
Only application probe-control additions enter the new image; Node, QueryTable,
Bloom, UnionWindow, cache and transport code must remain byte-identical.

64 conditions: Controlled8, Zipf16, Uniform16, extra sizes8, Federation16.
Both NC and TCP Direct everywhere. Poisson32/128 globally for all families;
burst32/128 additionally for Zipf/Uniform. 64KiB size/control slots shared.
6 separate smokes ->64 seed001 pilots ->1280 formal slots (960 single/320fed).
Pilot never enters selection; full phase starts only after ALL gates pass.
Formal seed001 reruns;10seeds x2reps. Arrival seed9172+workload seed, repetition
shares arrival schedule, no fixed concurrency cap. Pair methods adjacently with
deterministic balanced order. Original request identities/order/horizon retained:
Controlled256(8x32), IID4096(32x128), Federation3584(1024PRE+2560POST).
Formal total measurement requests3,850,240, excluding setup/smoke/pilot/probes.

NC:reuse ON,poll25ms,TTL30s,fallback1.5s,BF1024h3,wire3,2ms/1G,MTU1500.
TCP:independent uncached asynchronous owner connections,5s exchange timeout.
No8s started-request abort;8s dispatch gate,60s last-arrival watchdog,
20s phase-drain timeout,500ms stable; every failure stays in the denominator.
Original generator inputs copied separately and hashed. Federated scenario alias
only fixes frozen renderer's recognized prefix; original bytes are identical.

Federation uses original88-device logical topology, no cross-host/border/WG.
PRE complete+drain ->two probes ->natural NC expiry or explicit TCP failure
->probe retirement+drain ->both gateway ACKs ->500ms ->POST independently paced.
Only probe IDs900000001/2 on requester2/34 with frozen dedicated keys may retire.
Registration requires empty local app pending. Controller proves no other active
formal request and exactly one30s natural expiry per requester in the isolated
gap. C++ verifies authorized registration, TTL elapsed, no POST, sole pending;
it removes ONLY application bookkeeping. No QueryTable cancellation/API change.
Expired origin association/seen-ID tombstones remain until lab teardown; late
probe DATA is fatal, never delivered as a new formal request. Unattributable
expiry/late DATA prevents activation. Probe deadline40s. All probe evidence is
separate from formal accounting. New async TCP helper supports frozen activation.

CLI from source: python3 experiments/concurrency_slides_v1/pilot.py
preflight|status|analyze; smoke|pilot|run|campaign require --execute.
Status performs no writes. campaign chains stages, systemd Restart=no.
One lab; preflight >=32GiB available RAM and200GiB free disk, no active container,
old experiment units inactive, exact campaign/source/input/image hashes.
After pilot, projected formal raw+lab usage*1.3 plus100GiB reserve must fit disk.
No automatic retry after ANY request. Only pre-request infra failure retries once.
Unfinished raw requires manual investigation. No delete/reuse of old attempts.

Reports retain exact-ID delivery audit, timing, arrivals/backlog, original bin
groups,4-bin trends,complete burst cycles,resource/health and aggregate traffic.
Federation direction/local-control/remote cohorts are separate. No first-key
provider-hit attribution: peer-cache and serving-distance proxies unavailable.
No convergence threshold or automatic horizon extension; timing is descriptive.
Formal inference only at1280/1280: request->run bin mean->2rep seed mean->10seed
means with95% tCI; paired method/topology/locality/size/catalog/rate effects.
Partial state COLLECTING_NO_PARTIAL_STATS; diagnostic curves have no formal CI.

Start: systemd-run --unit=networkcache-concurrency-slides-v1 --property=Restart=no
--property=WorkingDirectory=/data/networkcache-concurrency-slides-v1/source
/usr/bin/python3 -u experiments/concurrency_slides_v1/pilot.py campaign --execute
One bounded startup confirmation, no ongoing polling. Initial budget2–4days,
revised from complete pilot timings and storage in derived/formal_budget.json.
