# worker-c v9: validate experiment methodology, not maximum performance

Only worker-c; root `/data/networkcache-concurrency-pilot-v9/`, branch
`codex/single-cluster-concurrency-pilot-v9`. No local repository edits, old raw
changes, other hosts, Slides, PDF, multiseed extension or automatic tuning.
Use the EXACT v8 image (no build):
`sha256:daa791093c488e86343fd90edcb1f3dae56e072c9012799f6bd3d29bf80d8c8e`.

Eight cold runs, seed001 x1, identical 4096 Zipf request identities on 32-node
single-cluster fabric; 512 objects, 64KiB,2ms/1G, BF1024 h3,wire3.
Serial no quiescence NC/TCP, Poisson128 TCP/NC, burst128 NC/TCP,
then legacy-style serial-quiescent TCP/NC. Methods alternate first position.
No extra performance/smoke matrix: deterministic controller/TCP tests and prior
v8 full-network smoke are verified before these eight runs. First short serial
runs also serve as full-network controller checks for each method. Current
runtime is common across all conditions; old raw is never a speedup baseline.

NC fixed: connection reuse enabled,25ms node poll,30s query TTL,1.5s fallback.
TCP Direct helper byte-identical to v8, uncached independent owner fetches and
5s client/server exchange timeout. NC connection reuse does not silently change
TCP Direct transport. No cross-method performance conclusion from this pilot.

Open arrivals are frozen exponential/periodic-Poisson offsets, arrival seed9173,
128 requests/s, no global/per-node in-flight limit. Burst4s lambda/4 +1s4lambda.
Serial release depends on previous completion. Legacy-style release waits for
all32 nodes' quiescence acknowledgements stable500ms after each request. Exact
inter-request wait logged individually; final request uses common end drain.
The 4095 inter-request waits + final drain preserve per-request quiet pacing.
This is a reimplementation of legacy pacing on the new runtime, not old raw.

Started-request8s abort remains OFF; dispatch8s and final open arrival+60s
watchdog remain. Serial has same60s watchdog after CURRENT release to prevent
an expired query blocking forever; it never expires merely because the serial
run is long. All failures retain4096 rows, no fabricated failed latency.
Setup unchanged, final quiescence20s timeout,500ms stable; infrastructure retry
only once BEFORE any request; no automatic load reruns or horizon extension.
Correctness/process/transport-evidence/cleanup faults stop queue.

Report correctness, actual overlap, scheduling lag, backlog/cache/time curves,
startup,setup,measurement,inter-request quiet subset,finalization,and run wall.
Compute speedup ONLY complete same-method same-campaign pairs. Serial latency
uses endogenous release (does not include an external hypothetical arrival
queue); never equate its latency with open arrivals under the same demand.
Nominal32s arrival window isn't full ETA or capacity. No convergence pass target.
Two quiescent runs alone require >=68min quiet, practical queue estimate ~1.5–2h
pending actual startup/processing/drain times. Long controls run last.

CLI from source: `python3 experiments/concurrency_pilot_v9/pilot.py preflight`
or `status` (read-only); `pilot --execute` runs eight-condition queue;
`analyze` derives report. systemd unit `networkcache-concurrency-pilot-v9`
uses Restart=no. One bounded startup confirmation, no ongoing polling.
Reports: `derived/method_comparison.md/json`, per-run batch/time/cache/backlog
SVG/CSV plus requests, provenance and health evidence. Raw append-only.
