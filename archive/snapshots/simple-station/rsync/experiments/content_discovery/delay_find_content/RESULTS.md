# Delay Find Content Results

This note summarizes the current paper-facing result for the non-invasive content discovery delay experiment. It is intentionally kept inside the project experiment area and does not modify the paper source.

## Research Questions

- **RQ1: Container correctness.** Can the packaged Docker deployment issue REQUEST packets, find a provider, return DATA, and deliver it to the application?
- **RQ2: Hop-distance scaling.** As the provider moves farther away, do discovery delay, end-to-end miss latency, and packet costs increase in the expected direction?
- **RQ3: Path materialization.** After one request returns DATA along a path, does a later requester for the same key hit a closer cache and reduce latency/packet cost?
- **RQ4: Reproducibility.** Are the trends stable in the Kathara primary environment and also visible in the Docker mirror?

## Legacy Docker Baseline

The previous baseline result is `discovery-v1-n30`, collected with packaged Docker containers on an isolated Docker bridge network. It remains useful as a small line/binary-tree sanity package, but it is no longer the final topology story for the paper.

- Manifest: 120 runs, all `ok`.
- Merged requests: 150 rows, including 120 cold requests and 30 path-materialization requests.
- Raw run outputs remain under the ignored `results/discovery-v1-n30/` directory.
- Paper-facing CSVs and figures are copied to `paper_assets/discovery-v1-n30/`.

## Paper-Facing Figures

- `line_hop_sweep.pdf`: supports RQ2. The line topologies show median end-to-end miss latency increasing from 100.5 ms at provider hop 2, to 150.7 ms at hop 3, and 200.7 ms at hop 4.
- `warm_path_materialization.pdf`: supports RQ3. In the branching topology, path materialization reduces median provider hit hop from 4 to 1, median miss latency to 25.1% of cold, median REQUEST packets from 5 to 2, and median aggregate flushes from 5 to 1.

## Draft Result Text

English draft:

> We first evaluate content discovery delay in packaged Docker deployments without modifying the protocol implementation. Across the line topologies, the median end-to-end miss latency grows with provider distance: 100.5 ms at hop distance 2, 150.7 ms at hop distance 3, and 200.7 ms at hop distance 4. In the branching topology, a first cold request materializes the data along the return path. A later request for the same key is then satisfied at a closer cache, reducing the median provider-hit hop from 4 to 1 and the median miss latency from 200.7 ms to 50.4 ms. The same warm request also reduces median REQUEST packets from 5 to 2 and median aggregate flushes from 5 to 1.

Chinese draft:

> 我们首先在不修改协议实现的前提下，使用打包后的 Docker 容器评估 content discovery delay。在线性拓扑中，provider 距离增加时，端到端 miss latency 的 median 从 hop 2 的 100.5 ms 增至 hop 3 的 150.7 ms，并进一步增至 hop 4 的 200.7 ms。在分支拓扑中，第一次 cold request 会让 DATA 返回路径上的节点物化缓存；随后另一个 requester 请求相同 key 时，可以在更近的位置命中。实验中 provider-hit hop 的 median 从 4 降至 1，miss latency 从 200.7 ms 降至 50.4 ms，同时 REQUEST packets 的 median 从 5 降至 2，aggregate flushes 的 median 从 5 降至 1。

## Current Kua-Style DC Result

The current paper-facing result is `kua-dc-v1-kathara-n30`, collected in Kathara with Kua-inspired 16- and 32-device datacenter topologies. Docker mirror results are also collected under `kua-dc-v1-docker-n30` for same-graph pipeline comparison.

Kathara primary:

- Manifest: 60 runs, all `ok`.
- Merged requests: 120 rows, with 60 cold rows and 60 path-materialization rows.
- `kua-dc-16`: median provider-hit hop `4 -> 1`; median miss latency `311.2 ms -> 56.4 ms`; miss latency ratio `0.181`.
- `kua-dc-32`: median provider-hit hop `4 -> 1`; median miss latency `361.6 ms -> 57.3 ms`; miss latency ratio `0.159`.
- REQUEST packet medians fall from `73.5 -> 2` for `kua-dc-16` and `135 -> 2` for `kua-dc-32`.
- Aggregate flush medians fall from `73 -> 1` for `kua-dc-16` and `136 -> 1` for `kua-dc-32`.

Docker mirror:

- Manifest: 60 runs, all `ok`.
- Merged requests: 120 rows.
- Both topologies show provider-hit hop `4 -> 1`.
- Median miss latency ratios are approximately `0.251` for both 16 and 32 devices.

Paper-facing assets are in `paper_assets/kua-dc-v1/`:

- `kathara_aggregate.csv`
- `kathara_comparison.csv`
- `docker_aggregate.csv`
- `docker_comparison.csv`
- `kua_dc_16_path_materialization.pdf`
- `kua_dc_16_path_materialization.svg`
- `kua_dc_32_path_materialization.pdf`
- `kua_dc_32_path_materialization.svg`

The Kua-style figures are split by topology rather than combining 16 and 32
devices into one normalized-ratio chart. Each figure shows absolute cold and
warm medians for provider-hit hop, miss-latency breakdown, and packet/aggregate
pressure.

Raw run outputs remain under the ignored `results/` tree.

## Next Multi-Workload Result

`kua-dc-v1` is a controlled single-key mechanism validation. The next result
package is `kua-dc-v2`, which keeps the same 16/32-device Kua-style topologies
but replaces the two-request workload with deterministic multi-key traces:

- `zipf`: hotspot workload, 64 keys, 200 requests per run, Zipf alpha 1.1.
- `uniform`: control workload with the same key/request counts and uniform key
  popularity.
- Each run has 60 warm-up requests and 140 measurement requests.
- Warm-up introduces 60 keys; measurement contains 4 first-key cold probes and
  136 repeated-key requests.
- v2 drivers use a 100 ms node loop sleep by default to keep multi-key traffic
  observable.
- Kathara remains the paper-primary environment; Docker remains a same-graph
  mirror.
- Result directories are isolated by workload and environment, for example
  `kua-dc-v2-zipf-kathara-n30` and `kua-dc-v2-uniform-docker-n30`.

The v2 analyzer output keeps workload metadata on each request row:
`workload_id`, `workload_type`, `seed`, `phase`, `request_index`, and
`is_first_key_request`. Full merged output keeps warm-up rows; aggregate and
comparison CSVs use measurement rows when phase metadata is present. Batch
status also uses measurement rows for v2, while warm-up incompletes remain in
the full merged CSV for debugging.

Current v2 smoke status:

- Docker Zipf smoke with 100 ms loop sleep produced 400 request rows across
  16/32 topologies. The current run group `smoke-v2-zipf-docker-current` has
  2/2 `ok` runs, 400 merged rows, and 280/280 complete measurement rows, with 8
  cold probes and 272 repeated-key rows across the two topologies.
- Docker Uniform smoke with the deterministic generator produced 400 request
  rows across 16/32 topologies. Both topology summaries have 140/140 complete
  measurement rows, with 4 cold probes and 136 repeated-key rows.
- Kathara v2 smoke is currently blocked for the multi-key workload. The Zipf
  smoke run group `smoke-v2-zipf-kathara` generated 400 merged rows, but both
  16/32 summaries were `incomplete`: provider-hit events were visible, yet a
  small number of measurement requests did not reach requester app receive. A
  slower 16-node diagnostic (`smoke-v2-zipf-kathara-slow16`, 200 ms loop sleep)
  remained incomplete, so this is not just a short wait-window issue.
- Completion diagnostics now distinguish this from a Docker/analyzer mismatch:
  `smoke-v2-zipf-docker-current` has 0 incomplete measurement requests, while
  `smoke-v2-zipf-kathara` has 9. In the normal Kathara smoke, all 9 missing
  measurement requests show DATA forwarded directly to the requester host
  (for example, rack node to requester), but the requester log does not show
  `Received DATA` or `[App] Received`. The slower 16-node run still has 9
  incomplete measurement requests, mostly with DATA reaching the requester rack
  or host but not completing at the app.
- Aggregation now emits `completion.csv` alongside `aggregate.csv` and
  `comparison.csv`. For `smoke-v2-zipf-kathara`, completion is 2/4 cold and
  133/136 path-materialization requests on `kua-dc-16`, and 2/4 cold and
  134/136 path-materialization requests on `kua-dc-32`. Latency aggregates
  remain measurement-only and should be interpreted together with completion
  ratio for incomplete Kathara multi-load runs.
- Kathara Uniform smoke and formal n30 are intentionally not started until the
  Kathara Zipf blocker is resolved or completion ratio / timeout rate is
  accepted as a first-class metric.

## Kua-DC v3 Topology Reset

After reviewing the Kua topology mapping, the next candidate result is Kua-DC
v3. v3 changes the topology model rather than the protocol:

- `16` and `32` now mean rsync host/storage node count only.
- Leaf and spine devices are pure Kathara forwarding devices with L3 static
  routing, `ip_forward=1`, and 2 ms `tc netem` delay.
- Leaf/spine devices do not run Redis or `rsync-node-dev`; they do not cache,
  aggregate, request, or provide data.
- The rsync overlay is host-to-host only. Physical packets traverse the
  spine/leaf fabric, but the switches do not understand REQUEST/DATA.
- Docker v3 is a host-overlay mirror and does not model the physical
  spine/leaf fabric. It is useful for pipeline comparison, not physical latency
  equivalence.

Implemented v3 scaffolding:

- `generate_workloads_v3.py` writes single-key, Zipf, and Uniform scenarios.
- `kua-dc-v3-16`: 16 hosts, 4 leaves, 4 spines, 32 physical links.
- `kua-dc-v3-32`: 32 hosts, 8 leaves, 4 spines, 64 physical links.
- `start-switch.sh` configures forwarding-only switch nodes.
- The Kathara renderer now separates protocol hosts from physical fabric
  devices for `kua-dc-v3-*` scenarios.
- The current v3 host overlay uses one primary gateway host per rack, with
  gateway hosts connected as a simple bounded-degree ring. Earlier two-gateway
  and cross-rack full-mesh overlays were too dense for the existing 500 ms
  QueryTable window under Kathara and produced cold requests with provider
  hits but missing app receive. A chorded-ring trial was also tested and failed
  the Zipf completion gate, so active v3 generation is back on the simple ring.

Current v3 status: framework implementation, static/render validation, and
single-key smoke are complete. Kathara `smoke-v3-single-key-kathara-check6`
completed both 16-host and 32-host scenarios with cold/path-materialization
rows and complete non-negative latency. Docker
`smoke-v3-single-key-docker-check1` also completed as a host-overlay mirror.
Kathara `smoke-v3-zipf-kathara-check1` on the simple gateway ring completed the
16-host Zipf workload, but the 32-host Zipf workload remained incomplete:
133/140 measurement requests had complete latency/hop fields, with 7
measurement rows flagged by `completion_diagnostics.csv`. The bounded-degree
chorded gateway ring then passed single-key smoke, but
`smoke-v3-zipf-kathara-chorded` still failed the formal gate: the 16-host Zipf
run had 3 missing measurement rows, and the 32-host Zipf run had 3 missing
cold measurement rows. The active topology was restored to the simple gateway
ring, and node-loop cadence trials were run at 25 ms and 10 ms. The 25 ms run
is the best observed setting so far, but it still misses 1/136
path-materialization measurement row in 16-host and 3/136 in 32-host. The
10 ms run regressed. No v3 formal n30 result should be considered paper data
until the remaining Zipf completion blocker is resolved or completion ratio /
timeout rate is explicitly adopted as a paper metric.

Single-key smoke evidence:

- Kathara 16-host: cold hop 3 and miss latency 322.8 ms; path-materialization
  hop 1 and miss latency 109.7 ms.
- Kathara 32-host: cold hop 3 and miss latency 323.8 ms; path-materialization
  hop 1 and miss latency 60.7 ms.
- Docker mirror 16/32 also shows cold hop 3 and path-materialization hop 1,
  but is not used as a physical fabric latency source.

Zipf smoke evidence:

- Kathara 16-host: measurement completion ratio 1.0 for both cold and
  path-materialization rows on the simple gateway ring, but chorded ring
  regressed to cold completion ratio 0.75 (3/4) and path-materialization
  completion ratio 0.985294 (134/136).
- Kathara 32-host: simple ring cold completion ratio 0.25 (1/4) and
  path-materialization completion ratio 0.970588 (132/136); chorded ring kept
  cold at 0.25 (1/4) but improved path-materialization to 1.0 (136/136).
- Simple ring with 25 ms node-loop cadence completed all cold measurement rows
  in both 16-host and 32-host, but still missed path-materialization rows:
  135/136 for 16-host and 133/136 for 32-host.
- Simple ring with 10 ms node-loop cadence regressed: 16-host cold was 3/4 and
  32-host path-materialization was 124/136.
- Diagnostics show mixed evidence: some missing requests have DATA return
  visible away from the requester, some reach a requester neighbor or appear
  sent toward the requester without an app receive log, and one row has app
  receive but missing provider-hit attribution. This points to a remaining
  experiment/analyzer/completion-policy issue, not a reason to modify the
  protocol source.

Observability-only diagnostic update:

- `RSYNC_OBSERVABILITY=1` now enables structured `[Obs]` stdout events without
  changing packet fields, forwarding behavior, QueryTable timeout, cache
  behavior, or the wire format. The default remains off.
- The analyzer now bounds a request window at the next request for the same
  key, regardless of requester. This avoids attributing a later warm-block
  response to an earlier cold-block request when no request id is present in
  the legacy logs.
- `smoke-v3-zipf-kathara-ring-s25-obs` produced 400 merged rows and 280
  measurement rows. After re-analysis, 278/280 measurement rows are complete.
- The remaining two missing measurement rows are both in `kua-dc-v3-32`:
  one row has provider-hit evidence and DATA reaching the requester neighbor,
  but not the requester, with QueryTable expiration evidence; one row has no
  provider/DATA return evidence in the request window and also has expiration
  evidence.
- Because completion is still below 1.0, v3 Zipf remains blocked for formal
  n30. Uniform v3 smoke, Docker v3 formal mirror, and Kathara formal n30 should
  not start until this is resolved or completion ratio / timeout rate is
  explicitly adopted as a paper metric.

Trace-based real timing update:

- Legacy latency summaries are derived from shell timestamps prepended to
  stdout log lines. They are useful for coarse request completion summaries,
  but they are not precise enough to explain sub-50 ms breakdowns such as
  NetworkCache warm nearby request versus Oracle NC Unicast request-to-hit
  time.
- `RSYNC_EVENT_TRACE=1` now enables internal C++ JSONL event tracing. Each
  node writes its own `/shared/traces/node<N>.jsonl` with `system_clock` epoch
  nanoseconds and monotonic nanoseconds captured at the event site.
- The trace is observability-only: it does not change REQUEST/DATA packet
  fields, QueryTable/SyncTable/cache behavior, forwarding decisions, or the
  default runtime path.
- Trace-aware Kathara runs produce `trace_summary.csv`,
  `trace_breakdown.csv`, and `trace_breakdown_by_class.csv`. These files should
  replace stdout-derived timing for fine-grained request-to-send,
  TCP connect+send, send-to-handle, provider lookup, DATA return, and app
  delivery analysis. The underlying CSV keeps the legacy `tcp_send` field
  name, but the measured interval covers sender-side TCP connection setup plus
  write, not pure socket write time.
- The next timing diagnosis should rerun NetworkCache distanceblock and Oracle
  NC Unicast with `RSYNC_EVENT_TRACE=1`, then compare NC warm nearby requests
  against Oracle NC Unicast using trace breakdown rows only.

Spacing sweep update:

- `generate_workloads_v3.py` now emits isolated Zipf spacing scenarios:
  `kua-dc-v3-{16,32}-zipf-s300-seed001` and
  `kua-dc-v3-{16,32}-zipf-s500-seed001`. They keep 64 keys, 200 requests,
  60 warm-up requests, and 140 measurement requests; only the request tick
  spacing changes.
- `smoke-v3-zipf-kathara-spacing-s300` produced 400 merged rows and 280
  measurement rows. 16-host completed 140/140 measurement requests; 32-host
  completed 138/140 and missed 2 path-materialization rows.
- `smoke-v3-zipf-kathara-spacing-s500` produced 400 merged rows and 280
  measurement rows. 16-host completed 140/140 measurement requests; 32-host
  completed 139/140 and missed 1 path-materialization row.
- The remaining s500 missing row is request index 192 on requester 15 for
  `redis_key_041`; diagnostics show no provider/DATA return evidence in the
  request window.
- Spacing improves completion but does not close the gate. The next decision
  should be a paper-method decision: either include completion ratio / timeout
  rate as a first-class metric, or redesign the multi-key workload envelope
  more explicitly. Do not keep increasing spacing or run formal n30 as if the
  strict latency table is complete.

Light workload update:

- `generate_workloads_v3.py` now emits isolated light Zipf scenarios:
  `kua-dc-v3-{16,32}-zipf-light-s500-seed001`.
- The light workload keeps 64 keys but reduces each run to 120 requests:
  60 warm-up requests and 60 measurement requests. It uses 500 ms request
  spacing under the 25 ms node-loop cadence.
- `smoke-v3-zipf-light-kathara-s500` produced 240 merged rows and 120
  measurement rows. Both 16-host and 32-host runs are `ok`; completion is
  120/120 measurement rows.
- `smoke-v3-zipf-light-docker-s500-long` produced 240 merged rows and 120
  measurement rows. Both Docker mirror runs are `ok`; completion is 120/120
  measurement rows. Docker remains a host-overlay mirror, not a physical
  spine/leaf latency source.
- Measurement composition is 4 cold rows and 56 path-materialization rows per
  topology.
- Kathara smoke medians still show the expected warm effect. For 16-host,
  provider-hit hop moves from 2.5 to 1 and median miss latency ratio is 0.257.
  For 32-host, provider-hit hop moves from 3.5 to 1 and median miss latency
  ratio is 0.221.
- `kua-dc-v3-zipf-light-kathara-n30` was stopped after the strict gate failed:
  16-host completed 30/30 runs, but 32-host run 7 had 1 incomplete measurement
  request. The missing request was node16 requesting `redis_key_001`; DATA was
  forwarded to node16 but no app receive was emitted.
- `diagnose-v3-zipf-light-32-kathara-obs-n10` reproduced the issue with
  observability enabled: 9/10 runs were `ok`, path-materialization completion
  was 559/560, and request_id 273 showed DATA receive at node16 followed by
  `query_table_miss` and no app receive. Therefore light Zipf is not a
  strict-complete formal workload.
- The heavier 200-request Zipf and light Zipf workloads should be treated as
  pressure or limitation experiments unless completion ratio / timeout rate
  becomes a formal metric.

Paired workload candidate:

- `generate_workloads_v3.py` now emits isolated paired scenarios:
  `kua-dc-v3-{16,32}-zipf-paired-s700-seed001`.
- The paired workload keeps 64 keys and 120 requests, but uses 40 warm-up
  requests and 80 measurement requests. Measurement is 40 explicit cold/warm
  key pairs; the two requesters for each measured key are different hosts in
  the same adjacent consumer rack.
- This design keeps the path-materialization question but avoids repeatedly
  issuing the hottest key from unrelated racks.
- `smoke-v3-zipf-paired-kathara-s700-fixed` completed both 16-host and 32-host
  smoke runs with 240 merged rows and 160/160 complete measurement rows.
- `diagnose-v3-zipf-paired-16-kathara-n10` and
  `diagnose-v3-zipf-paired-32-kathara-n10` both completed 10/10 runs. Each
  result has 1200 merged rows and 800/800 complete measurement rows.
- `smoke-v3-zipf-paired-docker-s700` completed the Docker mirror with 2/2
  runs `ok`, 240 merged rows, and 160/160 complete measurement rows.
- Kathara n10 medians show the expected warm effect. In 16-host, provider-hit
  hop moves from 3 to 1 and miss latency ratio is 0.258. In 32-host,
  provider-hit hop moves from 3 to 1 and miss latency ratio is 0.257.
- `kua-dc-v3-zipf-paired-kathara-n30` failed the strict formal gate and was
  stopped before the Docker mirror. The 16-host run group reached 25/26 `ok`;
  run 26 had one incomplete cold measurement row: requester node3 asked for
  `redis_key_032`, provider node16 produced a provider hit at hop 3, and DATA
  returned to the requester-side neighbor but not to requester node3. The
  diagnostic classifies the evidence as possible QueryTable return-window
  pressure.
- Therefore `zipf-paired-s700` is now a failed formal candidate and remains as
  pressure evidence only. The next candidate is `zipf-pairblock-s1500`, which
  separates measured cold requests and measured warm requests into two blocks
  with 1500 ms spacing and a 6000 ms inter-block gap.
- `smoke-v3-zipf-pairblock-kathara-s1500` also failed strict completion. The
  16-host run completed, but the 32-host run missed one cold measurement row:
  requester node7 asked for `redis_key_017` from provider node4. After
  tightening request windows to stop at the next same-key request, no
  provider/DATA return evidence remains in that cold request window.
- The isolated fallback candidate is `zipf-pairblock-s2500`, which keeps the
  same 64-request pair-block shape but increases request spacing to 2500 ms.
  It passed `smoke-v3-zipf-pairblock-kathara-s2500` with 2/2 `ok`, 128 merged
  rows, and 96/96 complete measurement rows.
- `diagnose-v3-zipf-pairblock-kathara-s2500-n10` passed with 20/20 `ok`, 1280
  merged rows, 960 measurement rows, and 960/960 complete measurement rows.
  In this n10 gate, provider-hit hop moved from median 3 to 1 in both
  topologies. Median miss-latency ratio was 0.287 for 16-host and 0.257 for
  32-host.
- `kua-dc-v3-zipf-pairblock-kathara-n30` did not pass the strict formal gate.
  The 16-host topology completed 30/30 runs, but the 32-host topology failed
  on run 3 and the batch was stopped before any Docker formal mirror. The
  failed row is cold request index 25: requester node8 asked for
  `redis_key_009`, provider node4 owned the key, but the analyzer and
  diagnostics found no provider-hit or DATA-return evidence within that cold
  request window. A later warm request for the same key from requester node6
  completed, so this is treated as a real formal-gate failure rather than a
  usable latency sample.
- The requested 32-host-only rerun,
  `kua-dc-v3-32-zipf-pairblock-kathara-n30-rerun1`, reproduced the same
  failure mode. It reached 12/12 `ok` and then failed run 13 on the same cold
  row: request index 25, requester node8, `redis_key_009`, with no provider-hit
  or DATA-return evidence in the cold request window. The runner was stopped
  and the lab was cleaned before any Docker formal mirror.
- Therefore `zipf-pairblock-s2500` joins `zipf-paired-s700`,
  `zipf-pairblock-s1500`, light Zipf, and heavy Zipf as pressure/limitation
  evidence. It should not be used as a paper formal result unless the paper
  explicitly adds completion ratio / timeout behavior as a metric.

Conditional-success result:

- At the user's request, we also built a conditional-success dataset that keeps
  only successful runs and reports performance for completed requests. This is
  stored as the synthetic ignored batch
  `results/kua-dc-v3-zipf-pairblock-kathara-success30`.
- The selected dataset contains 30 successful Kathara runs for each topology:
  30/30 from the 16-host partial formal batch, and 30 successful 32-host runs
  selected across the original partial formal batch, the 32-host rerun, and
  `kua-dc-v3-32-zipf-pairblock-kathara-success30-attempts2`.
- Shape: 60 selected runs, 3840 merged request rows, and 2880 measurement rows.
  Each topology contributes 720 cold and 720 path-materialization measurement
  rows. After selection, measurement completion is 2880/2880.
- 16-host conditional medians: miss latency moves from 211.6 ms cold to
  60.6 ms warm (ratio 0.286), provider-hop moves from 3 to 1, REQUEST packets
  move from 21 to 2, and aggregate flushes move from 21 to 1.
- 32-host conditional medians: miss latency moves from 235.6 ms cold to
  60.4 ms warm (ratio 0.256), provider-hop moves from 3 to 1, REQUEST packets
  move from 29 to 2, and aggregate flushes move from 28 to 1.
- Tracked paper-facing CSVs are in
  `paper_assets/kua-dc-v3-zipf-pairblock-success30/`. These CSVs support
  conditional latency and network-pressure claims for completed requests, not
  strict workload completion claims.

Distance-diverse report result:

- The current overall report uses the `zipf-distanceblock-s2500` workload to
  cover multiple provider-hop distances rather than one fixed cold/warm pair.
  Each run has 64 keys, 16 warm-up requests, and 48 measurement requests
  arranged as 24 cold requests followed by 24 warm/path-materialization
  requests for the same keys.
- The NetworkCache analysis batches are
  `kua-dc-v3-distanceblock-16-kathara-success20` and
  `kua-dc-v3-distanceblock-32-kathara-success20`. Each topology contributes
  20 repeated runs, 1280 merged rows, and 960 measurement rows.
- 16-host averages: miss latency moves from 223.5 ms cold to 37.7 ms warm;
  discovery delay moves from 123.8 ms to 24.1 ms; provider-hop moves from 3.0
  to 0.7; REQUEST packets move from 21.8 to 1.2; aggregate flushes move from
  21.6 to 0.7.
- 32-host averages: miss latency moves from 309.8 ms cold to 54.8 ms warm;
  discovery delay moves from 176.7 ms to 37.2 ms; provider-hop moves from 3.9
  to 1.0; REQUEST packets move from 38.1 to 2.6; aggregate flushes move from
  37.9 to 1.4.
- The main oracle baseline is now Oracle NetworkCache Unicast. It uses the
  same requester/provider/key schedule, assumes the requester already knows the
  provider location, but still obtains data using NetworkCache REQUEST/DATA
  packets. It removes flooding, SyncTable search, and aggregate flushes without
  switching to a raw TCP application protocol.
- The Oracle NC Unicast analysis batches are
  `oracle-nc-unicast-distanceblock-16-kathara-success20` and
  `oracle-nc-unicast-distanceblock-32-kathara-success20`. Each topology
  contributes 20 repeated oracle-unicast runs, 1280 merged rows, and 960
  measurement rows.
- TCP Direct Fetch is retained as a pure known-location lower bound using
  `oracle-distanceblock-16-kathara-success20` and
  `oracle-distanceblock-32-kathara-success20`; it does not generate
  NetworkCache REQUEST packets or aggregate flushes, so it is not used as the
  main packet-pressure baseline.
- The updated overall reports and speaking notes are in
  `experiments/reports/overall/`; the generated figures are under
  `experiments/reports/figures/distanceblock/`.

## Draft Kua-Style Result Text

English draft:

> We evaluate content discovery delay on Kua-inspired datacenter topologies in Kathara, with Docker bridge runs used as same-graph mirror checks. In both 16- and 32-device topologies, a cold request traverses the rack/core fabric to reach a provider at median hop distance 4. After the returned data materializes along the path, a later requester for the same key is served by a nearer cache at median hop distance 1. In Kathara, this reduces median end-to-end miss latency from 311.2 ms to 56.4 ms in the 16-device topology and from 361.6 ms to 57.3 ms in the 32-device topology. The same warm effect also sharply reduces REQUEST packet and aggregate flush counts.

Chinese draft:

> 我们在 Kathara 中使用 Kua-inspired datacenter 拓扑评估 content discovery delay，并用同构 Docker bridge 结果作为 mirror check。在 16 和 32 个 protocol device 的拓扑中，cold request 都需要跨 rack/core fabric 到达 provider，median provider-hit hop 为 4。第一次请求返回 DATA 后，路径上的节点物化缓存；随后另一个 requester 请求相同 key 时，median provider-hit hop 降至 1。在 Kathara 主结果中，16 节点拓扑的 median end-to-end miss latency 从 311.2 ms 降至 56.4 ms，32 节点拓扑从 361.6 ms 降至 57.3 ms；REQUEST packet 和 aggregate flush 的数量也同步大幅下降。

## Historical Follow-Up

The earlier line/binary-tree Kathara parity has been completed as optional reproducibility scaffolding for the legacy Docker baseline.

Current status as of 2026-06-26: the switched environment runs Kathara with Docker 29.3.0 and the enabled `kathara/katharanp_vde` plugin. `kathara-line-4-parity-v2` completed with a same-schema summary row and complete latency fields. `kathara-binary-tree-7-parity-v2` completed as limited qualitative parity: it produced cold and path-materialization provider-hit rows, with provider-hit hop moving from 4 to 3, but requester app-receive latency remains incomplete in current Kathara logs.

The tracked parity status summary is in `paper_assets/kathara-parity-v2/`. Docker `discovery-v1-n30` and the earlier line-4/binary-tree-7 parity runs remain useful as engineering baselines, while the completed Kua-style DC round is the current Kathara primary result for delay-find-content.
