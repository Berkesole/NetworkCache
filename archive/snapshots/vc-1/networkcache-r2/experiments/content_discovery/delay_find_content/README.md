# Delay To Find Content

This experiment measures how long the existing protocol needs to discover and return content. The default implementation remains non-invasive for protocol behavior: packaged containers and legacy stdout logs are still supported, while fine-grained timing uses an optional env-gated C++ event trace that only records events and does not change REQUEST/DATA semantics.

Fine-grained sub-50 ms timing should use the newer env-gated C++ event trace,
not the legacy shell timestamp on stdout. Enable it only for diagnosis or
trace-specific batches:

```bash
RSYNC_EVENT_TRACE=1 KATHARA_NODE_LOOP_SLEEP_MS=25 \
experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --scenarios kua-dc-v3-16-zipf-distanceblock-s2500-seed001 \
  --repeats 1 \
  --run-group-id trace-distanceblock-16-kathara-smoke
```

When enabled, each host writes `/shared/traces/node<N>.jsonl`, and the runner
copies those files into `results/<run_id>/traces/`. The trace analyzer writes
`trace_summary.csv`, `trace_breakdown.csv`, and
`trace_breakdown_by_class.csv`. The trace records C++ event timestamps at the
event site with both `system_clock` epoch nanoseconds and monotonic
nanoseconds; the default protocol behavior and wire format are unchanged.

## Paper Questions And Completion Target

This experiment group should be completed before moving to bandwidth or cache-management work. It is intended to answer four paper questions:

- **RQ1: Container correctness.** Can the packaged Docker deployment issue REQUEST packets, find a provider, return DATA, and deliver it to the application?
- **RQ2: Hop-distance scaling.** As the provider moves farther away, do discovery delay, end-to-end miss latency, and packet costs increase in the expected direction?
- **RQ3: Path materialization.** After one request returns DATA along a path, does a later requester for the same key hit a closer cache and reduce latency/packet cost?
- **RQ4: Reproducibility.** Are the Kathara primary trends stable and also visible in the Docker mirror?

The previous `discovery-v1-n10` run was a draft trend check. The older line/binary-tree data package is `discovery-v1-n30` and includes:

- Docker main batch: `discovery-v1-n30`, with 30 repeats for `line-3-cold`, `line-4-cold`, `line-5-cold`, and `binary-tree-7-path-warm`.
- Batch acceptance: 120 manifest rows, all `status=ok`; `merged.csv` should include 150 request rows because the warm binary-tree scenario contributes both cold and warm requests.
- Curated CSVs: final `aggregate.csv`, `comparison.csv`, plus any smaller paper-facing excerpts derived from them.
- Draft figures: `line_hop_sweep.pdf` and `warm_path_materialization.pdf` generated from the final batch.
- Kathara parity: optional engineering follow-up for the older line/binary-tree round.

The completed single-key Kua-style datacenter round is `kua-dc-v1`:

- Kathara primary batch: `kua-dc-v1-kathara-n30`.
- Docker mirror batch: `kua-dc-v1-docker-n30`.
- Scenarios: `kua-dc-16-warm` and `kua-dc-32-warm`.
- Topology intent: rack/core fabric nodes run `rsync-node-dev`, so they can forward and cache REQUEST/DATA traffic instead of acting as pure Linux switches.
- Kathara link model: 2 ms per interface with Linux `tc netem`; no bandwidth limit and no loss.
- Batch acceptance: both environments completed 60/60 `ok` runs and 120 merged request rows.
- Paper-facing assets: `paper_assets/kua-dc-v1/`.

The new topology candidate is Kua-DC v3:

- `16` and `32` mean rsync host/storage node count, not switch count.
- Kathara leaf/spine devices are pure L3 forwarding devices. They run static
  routes, `ip_forward=1`, and link delay, but they do not run Redis or
  `rsync-node-dev`.
- The rsync protocol overlay exists only among host nodes. Physical host-to-host
  TCP traffic traverses the Kathara leaf/spine fabric.
- Docker mirror runs only the host overlay and records
  `docker_mirror_model=host_overlay_no_physical_fabric`.
- v1/v2 results are kept as historical mechanism validation; v3 is the next
  paper-facing topology candidate.

The next workload-oriented round is Kua-style datacenter v2:

- Workloads: `zipf` and `uniform`.
- Scenarios: `kua-dc-16-zipf-seed001`, `kua-dc-32-zipf-seed001`,
  `kua-dc-16-uniform-seed001`, and `kua-dc-32-uniform-seed001`.
- Per run: 64 keys, 200 requests, 60 warm-up requests, and 140 measurement
  requests.
- Schedule policy: warm-up introduces 60 keys; measurement keeps 4 first-key
  cold probes and 136 repeated-key requests.
- Runtime cadence: v2 Docker and Kathara runs default to 100 ms node loop sleep
  so multi-key REQUEST/DATA traffic remains observable.
- Result isolation: use run groups such as `kua-dc-v2-zipf-kathara-n30` and
  `kua-dc-v2-uniform-docker-n30`; batch drivers refuse to overwrite an existing
  manifest unless `--overwrite` is passed.

Legacy line/binary-tree Docker run:

```bash
experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --run-group-id discovery-v1-n30 \
  --repeats 30
```

With current scenario wait times, this full batch takes roughly one hour plus image build and container startup overhead. Run it when the machine can be left alone long enough to finish.

Legacy line/binary-tree figure generation:

```bash
experiments/content_discovery/delay_find_content/scripts/plot_results.py \
  experiments/content_discovery/delay_find_content/results/discovery-v1-n30
```

## Legacy Baseline Result

`discovery-v1-n30` is the older Docker bridge baseline for this experiment group:

- `manifest.csv`: 120 runs, 120 `ok`, 0 incomplete/failed.
- `merged.csv`: 150 request rows, including 120 cold rows and 30 path-materialization rows.
- Hop sweep median miss latency: `line-3` 100.5 ms, `line-4` 150.7 ms, `line-5` 200.7 ms.
- Path materialization median provider hit hop: cold 4, warm 1.
- Path materialization median miss latency ratio: 0.251.
- Path materialization median REQUEST packets: cold 5, warm 2.
- Path materialization median aggregate flushes: cold 5, warm 1.

Paper-facing assets are copied to:

```text
experiments/content_discovery/delay_find_content/paper_assets/discovery-v1-n30/
```

This directory contains only curated CSVs and final draft figures. Raw run logs, per-run summaries, `manifest.csv`, `merged.csv`, and container logs remain under the ignored `results/` tree. This Docker package is kept as a legacy baseline; the Kua-style Kathara round is the current paper-facing result.

## Kua-Style DC 16/32 Round

The current round narrows the paper topology set to two datacenter-style scenarios:

- `kua-dc-16-warm`: 9 host nodes, 3 rack forwarders, and 4 core forwarders.
- `kua-dc-32-warm`: 24 host nodes, 4 rack forwarders, and 4 core forwarders.

Both scenarios use one provider and two requests for `redis_key_a`: a cold request from Node 1 and a warm request from Node 2. The provider is placed in a different rack so the cold path crosses rack/core fabric, while the warm request can be satisfied by a closer cache if path materialization occurs.

Kathara is the primary environment:

```bash
experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --run-group-id kua-dc-v1-kathara-n30 \
  --repeats 30
```

Docker is the same-graph mirror without explicit per-link delay:

```bash
experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --run-group-id kua-dc-v1-docker-n30 \
  --repeats 30
```

The completed n30 results are:

- Kathara primary: `kua-dc-v1-kathara-n30`, 60/60 runs `ok`, 120 merged request rows.
- Docker mirror: `kua-dc-v1-docker-n30`, 60/60 runs `ok`, 120 merged request rows.
- Kathara `kua-dc-16`: median provider-hit hop `4 -> 1`; median miss latency `311.2 ms -> 56.4 ms`.
- Kathara `kua-dc-32`: median provider-hit hop `4 -> 1`; median miss latency `361.6 ms -> 57.3 ms`.
- Docker mirror: both topologies also show median provider-hit hop `4 -> 1`.

The topology design note is in `topologies/README.md`. Raw run outputs stay under ignored `results/`; curated CSVs and final figures are copied into `paper_assets/kua-dc-v1/`.

## Kua-Style DC v2 Multi-Key Workloads

Generate or refresh deterministic v2 workload scenarios with:

```bash
python3 experiments/content_discovery/delay_find_content/scripts/generate_workloads.py
```

The generator writes Kathara scenarios, Docker mirror configs, and metadata
sidecars under `workloads/kua-dc-v2/generated/`. The analyzer uses those
sidecars to annotate each parsed request with `workload_id`, `phase`,
`request_index`, and `seed`.

Formal Zipf batches:

```bash
experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --workload zipf \
  --repeats 30

experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --workload zipf \
  --repeats 30
```

Formal Uniform batches:

```bash
experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --workload uniform \
  --repeats 30

experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --workload uniform \
  --repeats 30
```

`merged.csv` keeps all warm-up and measurement rows. When phase metadata is
present, `aggregate.csv`, `comparison.csv`, and batch `ok`/`incomplete` status
are computed from measurement rows only, and `merged_measurement.csv` records
the filtered input. Warm-up incompletes remain visible in `merged.csv` for
debugging but do not enter paper latency aggregates. Batch aggregation also
writes `completion.csv`, which records measurement request completion counts
and ratios by workload/topology/phase/warm type; latency figures should use
completed rows only and should be paired with completion ratio when a Kathara
multi-load run is incomplete.

Current v2 gate: Docker Zipf/Uniform smoke runs complete, but Kathara Zipf
multi-key smoke is still under completion diagnosis. Do not run formal n30
until Kathara Zipf either reaches full measurement completion or the paper
metric explicitly includes completion ratio / timeout rate.

## Kua-DC v3 Pure Spine/Leaf Fabric

Generate deterministic v3 scenarios with:

```bash
python3 experiments/content_discovery/delay_find_content/scripts/generate_workloads_v3.py
```

This writes:

- Kathara host-only protocol scenarios under `kathara/ruikekkk/scenarios/kua-dc-v3-*`.
- Docker mirror configs under `configs/kua-dc-v3-*`.
- Metadata sidecars under `workloads/kua-dc-v3/generated/`.

Render checks:

```bash
KATHARA_LINK_DELAY_MS=2 scripts/kathara/render_ruikekkk_scenario.sh kua-dc-v3-16-warm
KATHARA_LINK_DELAY_MS=2 scripts/kathara/render_ruikekkk_scenario.sh kua-dc-v3-32-warm
kathara lstart -d kathara/ruikekkk --print
```

Expected render counts:

- `kua-dc-v3-16`: `host_count=16`, `leaf_count=4`, `spine_count=4`,
  `edge_count=16`, `physical_edge_count=32`.
- `kua-dc-v3-32`: `host_count=32`, `leaf_count=8`, `spine_count=4`,
  `edge_count=32`, `physical_edge_count=64`.

Current smoke status:

- `smoke-v3-single-key-kathara-check6`: 16/32 both `ok`; cold provider hop
  3, path-materialization provider hop 1.
- `smoke-v3-single-key-docker-check1`: 16/32 both `ok`; this is a
  host-overlay mirror only and does not model physical leaf/spine latency.
- `smoke-v3-zipf-kathara-check1`: 16-host is complete, but 32-host is still
  `incomplete` with 7 missing measurement rows out of 140. The ignored result
  directory includes `completion.csv` and `completion_diagnostics.csv`.
- `smoke-v3-single-key-kathara-chorded`: 16/32 both `ok`; cold provider hop
  3, path-materialization provider hop 1.
- `smoke-v3-zipf-kathara-chorded`: failed the formal gate. 16-host has 3
  missing measurement rows, and 32-host has 3 missing cold measurement rows.
  The ignored result directory includes `completion.csv` and
  `completion_diagnostics.csv`.
- Active v3 generation has been restored to the simple gateway ring.
- `smoke-v3-zipf-kathara-ring-s25`: failed the formal gate but is the best
  cadence trial so far. Cold measurement rows are complete for both 16/32;
  path-materialization completion is 135/136 for 16-host and 133/136 for
  32-host.
- `smoke-v3-zipf-kathara-ring-s10`: failed the formal gate and regressed
  relative to s25. Do not continue lowering node-loop sleep as the next step.
- `smoke-v3-zipf-kathara-ring-s25-obs`: observability-only diagnostic run
  with `RSYNC_OBSERVABILITY=1` and 25 ms node-loop cadence. It produced 400
  merged rows and 280 measurement rows. The analyzer now bounds request windows
  at the next same-key request, so later warm responses are not attributed to
  earlier cold requests. The remaining missing rows are still treated as real
  completion gaps rather than latency rows.
- `smoke-v3-zipf-kathara-spacing-s300`: failed the formal gate. It produced
  400 merged rows and 280 measurement rows, with 278/280 complete. 16-host is
  complete; 32-host misses 2/136 path-materialization measurement rows.
- `smoke-v3-zipf-kathara-spacing-s500`: failed the formal gate. It produced
  400 merged rows and 280 measurement rows, with 279/280 complete. 16-host is
  complete; 32-host misses 1/136 path-materialization measurement row. The
  missing row has no provider/DATA return evidence in the request window.
- The spacing sweep improves completion but does not reach strict completion
  ratio 1.0. Do not continue spacing-only tuning as the next step; decide
  whether completion ratio / timeout rate becomes a paper metric or whether
  the workload design should change more explicitly.
- `smoke-v3-zipf-light-kathara-s500`: initial reduced-pressure v3 multi-key
  candidate. It uses 64 keys, 120 requests, 60 warm-up requests, 60 measurement
  requests, and 500 ms request spacing. The smoke completed, but the later
  32-host observability n10 diagnostic still missed 1/600 measurement rows:
  node16 received DATA for `redis_key_001` but did not emit app receive.
- `smoke-v3-zipf-light-docker-s500-long`: Docker host-overlay mirror for the
  light workload. It completed 2/2 runs with 240 merged rows and 120/120
  complete measurement rows when using the longer Docker wait window now set
  as the light workload default.
- `kua-dc-v3-{16,32}-zipf-paired-s700-seed001`: refactored workload candidate.
  It keeps 64 keys and 120 requests, but uses 40 warm-up requests and 80
  measurement requests. Measurement consists of 40 explicit cold/warm key pairs;
  each warm requester is a different host in the same adjacent consumer rack as
  the cold requester, so path materialization is measured without repeatedly
  stressing one hot key from unrelated racks.
- `smoke-v3-zipf-paired-kathara-s700-fixed`,
  `diagnose-v3-zipf-paired-16-kathara-n10`, and
  `diagnose-v3-zipf-paired-32-kathara-n10` all reached measurement completion
  ratio 1.0. The n10 runs each produced 1200 merged rows and 800 complete
  measurement rows.
- `smoke-v3-zipf-paired-docker-s700` completed the Docker host-overlay mirror:
  2/2 `ok`, 240 merged rows, and 160/160 complete measurement rows.
- `kua-dc-v3-zipf-paired-kathara-n30` failed the strict formal gate and was
  stopped before Docker mirror. The 16-host batch reached 25/26 `ok`; run 26
  missed one cold measurement row for `redis_key_032` from requester node3 to
  provider node16. Provider hit was observed, but DATA returned only to the
  requester-side neighbor, so this workload is now a pressure/failed-candidate
  record rather than a formal result.
- `kua-dc-v3-{16,32}-zipf-pairblock-s1500-seed001`: next workload candidate.
  It keeps all 64 keys preloaded, but uses 16 warm-up requests and 48
  measurement requests over 24 hot keys. Measurement is split into a cold block
  followed by a warm block for the same keys, with 1500 ms request spacing and
  a 6000 ms idle gap between blocks. This avoids interleaving cold discovery
  probes with warm path-materialization probes.
- `smoke-v3-zipf-pairblock-kathara-s1500` failed the strict smoke gate: the
  16-host run completed, but the 32-host run missed one cold measurement row
  (`redis_key_017`, requester node7, provider node4). After tightening the
  analyzer window to stop at the next same-key request, the row has no
  provider/DATA return evidence in its own request window.
- `kua-dc-v3-{16,32}-zipf-pairblock-s2500-seed001`: isolated fallback with the
  same 64/16/48 request shape, 2500 ms request spacing, and the same 6000 ms
  cold-to-warm block gap. Use this before any formal n30.
- `smoke-v3-zipf-pairblock-kathara-s2500` passed: 2/2 `ok`, 128 merged rows,
  96 measurement rows, and 96/96 complete measurement rows.
- `diagnose-v3-zipf-pairblock-kathara-s2500-n10` passed: 20/20 `ok`, 1280
  merged rows, 960 measurement rows, and 960/960 complete measurement rows.
  In the n10 comparison, median provider-hit hop moves from 3 to 1 for both
  topologies. Median miss-latency ratio is 0.287 for 16-host and 0.257 for
  32-host.
- `kua-dc-v3-zipf-pairblock-kathara-n30` failed the strict formal gate and was
  stopped before the Docker mirror. The 16-host topology completed 30/30 runs,
  but the 32-host topology failed on run 3 with one incomplete cold measurement
  row: requester node8 asked for `redis_key_009`, provider node4 owned the key,
  but no provider/DATA return evidence appeared in that cold request window.
  A later warm request for the same key completed from requester node6, so the
  failed cold row is retained as formal-gate evidence rather than overwritten.
- A 32-host-only rerun, `kua-dc-v3-32-zipf-pairblock-kathara-n30-rerun1`, was
  stopped after 13 runs. It reproduced the same incomplete row at run 13:
  request index 25, requester node8, `redis_key_009`, with no provider-hit or
  DATA-return evidence in the cold request window. This confirms that s2500 is
  not a reliable strict-complete formal workload for 32-host Kathara.

Kathara smoke commands:

```bash
experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --topology-version v3 \
  --workload single-key \
  --repeats 1 \
  --run-group-id smoke-v3-single-key-kathara

experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --topology-version v3 \
  --workload zipf \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-kathara

RSYNC_OBSERVABILITY=1 KATHARA_NODE_LOOP_SLEEP_MS=25 \
  experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --topology-version v3 \
  --workload zipf \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-kathara-ring-s25-obs

experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --scenarios kua-dc-v3-16-zipf-s300-seed001,kua-dc-v3-32-zipf-s300-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-kathara-spacing-s300

experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --scenarios kua-dc-v3-16-zipf-s500-seed001,kua-dc-v3-32-zipf-s500-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-kathara-spacing-s500

experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --scenarios kua-dc-v3-16-zipf-light-s500-seed001,kua-dc-v3-32-zipf-light-s500-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-light-kathara-s500

experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --scenarios kua-dc-v3-16-zipf-paired-s700-seed001,kua-dc-v3-32-zipf-paired-s700-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-paired-kathara-s700

KATHARA_NODE_LOOP_SLEEP_MS=25 \
experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --scenarios kua-dc-v3-16-zipf-pairblock-s1500-seed001,kua-dc-v3-32-zipf-pairblock-s1500-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-pairblock-kathara-s1500

KATHARA_NODE_LOOP_SLEEP_MS=25 \
experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --scenarios kua-dc-v3-16-zipf-pairblock-s2500-seed001,kua-dc-v3-32-zipf-pairblock-s2500-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-pairblock-kathara-s2500

experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --topology-version v3 \
  --workload uniform \
  --repeats 1 \
  --run-group-id smoke-v3-uniform-kathara
```

Docker mirror smoke commands:

```bash
experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --topology-version v3 \
  --workload zipf \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-docker

experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --scenarios kua-dc-v3-16-zipf-light-s500-seed001,kua-dc-v3-32-zipf-light-s500-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-light-docker-s500-long

experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --scenarios kua-dc-v3-16-zipf-paired-s700-seed001,kua-dc-v3-32-zipf-paired-s700-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-paired-docker-s700

experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --scenarios kua-dc-v3-16-zipf-pairblock-s1500-seed001,kua-dc-v3-32-zipf-pairblock-s1500-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-pairblock-docker-s1500

experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --scenarios kua-dc-v3-16-zipf-pairblock-s2500-seed001,kua-dc-v3-32-zipf-pairblock-s2500-seed001 \
  --repeats 1 \
  --run-group-id smoke-v3-zipf-pairblock-docker-s2500

experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --topology-version v3 \
  --workload uniform \
  --repeats 1 \
  --run-group-id smoke-v3-uniform-docker
```

Formal n30 gate is closed for `zipf-paired-s700`, `zipf-pairblock-s1500`, and
`zipf-pairblock-s2500`. The partial
`kua-dc-v3-zipf-pairblock-kathara-n30` run is failure evidence only: do not use
it as a paper result, do not overwrite it, and do not start the matching Docker
formal mirror. The next formal candidate must use a new isolated workload name
and run group.

For conditional performance analysis, a success-only synthetic batch is also
available at `kua-dc-v3-zipf-pairblock-kathara-success30`. It selects 30
successful Kathara runs per topology and reports metrics only for completed
requests. Paper-facing CSVs for this conditional view are tracked under
`paper_assets/kua-dc-v3-zipf-pairblock-success30/`.

The current overall report uses Oracle NetworkCache Unicast as the main
known-owner baseline for the distance-diverse workload. The requester is told
the provider for each key by an oracle, but it still sends NetworkCache
REQUEST packets and receives NetworkCache DATA packets. This removes flooding,
SyncTable search, and aggregate flushes while keeping the protocol packet
path. TCP Direct Fetch is retained only as a pure direct-transfer lower bound
and is not used for NetworkCache packet-pressure comparison.

Oracle NetworkCache Unicast baseline commands:

```bash
KATHARA_NODE_LOOP_SLEEP_MS=25 \
experiments/content_discovery/delay_find_content/scripts/run_oracle_nc_unicast_batch.sh \
  --scenarios kua-dc-v3-16-zipf-distanceblock-s2500-seed001 \
  --repeats 20 \
  --run-group-id oracle-nc-unicast-distanceblock-16-kathara-attempts1

KATHARA_NODE_LOOP_SLEEP_MS=25 \
experiments/content_discovery/delay_find_content/scripts/run_oracle_nc_unicast_batch.sh \
  --scenarios kua-dc-v3-32-zipf-distanceblock-s2500-seed001 \
  --repeats 20 \
  --run-group-id oracle-nc-unicast-distanceblock-32-kathara-attempts2

python3 experiments/content_discovery/delay_find_content/scripts/collect_success_runs.py \
  --output experiments/content_discovery/delay_find_content/results/oracle-nc-unicast-distanceblock-16-kathara-success20 \
  --target-per-scenario 20 \
  --scenarios kua-dc-v3-16-zipf-distanceblock-s2500-seed001 \
  experiments/content_discovery/delay_find_content/results/oracle-nc-unicast-distanceblock-16-kathara-attempts1

python3 experiments/content_discovery/delay_find_content/scripts/collect_success_runs.py \
  --output experiments/content_discovery/delay_find_content/results/oracle-nc-unicast-distanceblock-32-kathara-success20 \
  --target-per-scenario 20 \
  --scenarios kua-dc-v3-32-zipf-distanceblock-s2500-seed001 \
  experiments/content_discovery/delay_find_content/results/oracle-nc-unicast-distanceblock-32-kathara-attempts2
```

TCP Direct Fetch lower-bound commands:

```bash
experiments/content_discovery/delay_find_content/scripts/run_oracle_kathara_batch.sh \
  --scenarios kua-dc-v3-16-zipf-distanceblock-s2500-seed001 \
  --repeats 20 \
  --run-group-id oracle-distanceblock-16-kathara-attempts1

experiments/content_discovery/delay_find_content/scripts/run_oracle_kathara_batch.sh \
  --scenarios kua-dc-v3-32-zipf-distanceblock-s2500-seed001 \
  --repeats 20 \
  --run-group-id oracle-distanceblock-32-kathara-attempts1

python3 experiments/content_discovery/delay_find_content/scripts/collect_oracle_success_runs.py \
  --output experiments/content_discovery/delay_find_content/results/oracle-distanceblock-16-kathara-success20 \
  --target-per-scenario 20 \
  --scenarios kua-dc-v3-16-zipf-distanceblock-s2500-seed001 \
  experiments/content_discovery/delay_find_content/results/oracle-distanceblock-16-kathara-attempts1

python3 experiments/content_discovery/delay_find_content/scripts/collect_oracle_success_runs.py \
  --output experiments/content_discovery/delay_find_content/results/oracle-distanceblock-32-kathara-success20 \
  --target-per-scenario 20 \
  --scenarios kua-dc-v3-32-zipf-distanceblock-s2500-seed001 \
  experiments/content_discovery/delay_find_content/results/oracle-distanceblock-32-kathara-attempts1
```

## Implemented Scenarios

- `line-3-cold`: sanity check where Node 1 requests data held by Node 3.
- `line-4-cold`: four-node line, aligned with the existing bridge acceptance topology shape.
- `line-5-cold`: five-node line for hop-distance sweep preparation.
- `binary-tree-7-path-warm`: Node 4 requests `redis_key_a` first, then Node 5 requests the same key after path caches may have materialized.
- `kua-dc-16-warm`: Kua-inspired 16-device datacenter topology.
- `kua-dc-32-warm`: Kua-inspired 32-device datacenter topology.
- `kua-dc-v3-16-warm`: 16 rsync hosts over 4 pure leaf switches and 4 pure spine switches.
- `kua-dc-v3-32-warm`: 32 rsync hosts over 8 pure leaf switches and 4 pure spine switches.
- `kua-dc-v3-{16,32}-{zipf,uniform}-seed001`: deterministic v3 multi-key workloads.
- `kua-dc-v3-{16,32}-zipf-s{300,500}-seed001`: deterministic v3 Zipf spacing
  sweep workloads; same request/key count as default Zipf, but request ticks
  are spaced for 25 ms loop cadence.
- `kua-dc-v3-{16,32}-zipf-light-s500-seed001`: deterministic v3 light Zipf
  workload; 64 keys, 120 requests, 60 warm-up, 60 measurement, and 500 ms
  request spacing for 25 ms loop cadence.
- `kua-dc-v3-{16,32}-zipf-paired-s700-seed001`: deterministic v3 paired
  cold/warm workload; 64 keys, 120 requests, 40 warm-up, 80 measurement, and
  700 ms request spacing for 25 ms loop cadence.
- `kua-dc-v3-{16,32}-zipf-pairblock-s{1500,2500}-seed001`: deterministic v3
  pair-block workload; 64 keys preloaded, 64 requests, 16 warm-up, 48
  measurement, 24 measured cold/warm key pairs, configurable request spacing, and a
  6000 ms cold-to-warm block gap for 25 ms loop cadence.
- `kua-dc-v3-{16,32}-zipf-distanceblock-s2500-seed001`: deterministic v3
  distance-diverse workload used by the current overall report; 64 keys, 64
  requests, 16 warm-up, 48 measurement, and cold provider distances spread
  across multiple rsync overlay hop classes.

All scenario files live in `configs/` and include TCP host/port assignments for Docker bridge runs. The Docker driver builds them into the experiment image and records an exact copy in each run directory; it does not rely on host bind mounts for topology delivery.

## Running Docker Bridge Experiments

```bash
experiments/content_discovery/delay_find_content/scripts/run_docker_bridge.sh line-3-cold
experiments/content_discovery/delay_find_content/scripts/run_docker_bridge.sh binary-tree-7-path-warm
```

Each run writes to:

```text
experiments/content_discovery/delay_find_content/results/<run_id>/
```

The run directory contains:

- `metadata.json`: git commit, image tag, topology, network, container names, node IPs.
- `topology.yaml`: exact scenario file copied into the run record.
- `logs/node-<id>.log`: `docker logs --timestamps` output per node.
- `summary.csv`: analyzer output.

The Docker driver builds `networkcache-exp:<git-short-sha>` by default, then runs one container per node with the packaged topology path passed through `RSYNC_APP_ARGS`:

```text
RSYNC_APP_TARGET=rsync-node-dev
RSYNC_TRANSPORT=tcp
```

Useful environment knobs:

```bash
BUILD_IMAGE=0                      # reuse the existing image tag
RUN_ID=my-run                      # stable run directory name
RUN_SECONDS=60                     # override wait time
NODE_LOOP_ITERATIONS=600           # keep nodes alive longer
KEEP_CONTAINERS=1                  # preserve containers/network for debugging
```

## Analyzer

The analyzer parses existing timestamped logs only:

```bash
python3 experiments/content_discovery/delay_find_content/scripts/analyze_logs.py   --logs-dir experiments/content_discovery/delay_find_content/results/<run_id>/logs   --output experiments/content_discovery/delay_find_content/results/<run_id>/summary.csv   --scenario line-3-cold   --env docker   --topology line-3   --topology-file experiments/content_discovery/delay_find_content/results/<run_id>/topology.yaml   --warm-type cold
```

`summary.csv` columns are:

```text
workload_id,workload_type,seed,phase,request_index,is_first_key_request,scenario,env,topology,warm_type,requester,provider,key,discovery_delay_ns,return_delay_ns,miss_latency_ns,request_packets,data_packets,aggregate_flushes,sync_table_hits,sync_table_misses,provider_hit_hop
```

Measurement rules:

- `request_start`: requester `User wants file: <key>` line.
- `provider_hit`: first matching `Sending DATA for key=<key> ...` line within that request window.
- `app_receive`: requester `[App] Received data for key=<key> ...` line.
- `discovery_delay_ns = provider_hit - request_start`.
- `return_delay_ns = app_receive - provider_hit`.
- `miss_latency_ns = app_receive - request_start`.

If an event cannot be inferred from existing logs, the analyzer leaves the corresponding field blank instead of adding instrumentation to the protocol.

For Kathara multi-load completion debugging, use:

```bash
python3 experiments/content_discovery/delay_find_content/scripts/diagnose_completion.py \
  experiments/content_discovery/delay_find_content/results/<batch_id>
```

The diagnostic output is `completion_diagnostics.csv`. It lists incomplete
measurement requests and reports the provider-hit time, last DATA
send/forwarding evidence, whether DATA reached the requester's rack neighbor,
whether DATA was sent directly to the requester, and whether requester
`Received DATA` / `[App] Received` lines appeared.


## Batch Runs

Use the Docker batch driver when collecting paper-oriented data:

```bash
experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh
```

By default it runs each Kua-style DC Docker mirror scenario 30 times:

```text
kua-dc-16-warm,kua-dc-32-warm
```

Useful options:

```bash
experiments/content_discovery/delay_find_content/scripts/run_docker_batch.sh \
  --scenarios kua-dc-16-warm,kua-dc-32-warm \
  --repeats 30 \
  --run-group-id kua-dc-v1-docker-n30
```

The batch directory is:

```text
experiments/content_discovery/delay_find_content/results/<batch_id>/
```

It contains:

- `manifest.csv`: one row per attempted run, with `run_id`, `scenario`, `repeat_index`, `status`, summary/metadata paths, and timestamps.
- `runs/<scenario>-r<rep>/`: raw logs, topology, metadata, and per-run `summary.csv`.
- `merged.csv`: all per-run summary rows with batch/run metadata.
- `aggregate.csv`: grouped statistics for latency, packet counters, and provider hop distance.
- `comparison.csv`: cold vs path-materialization median ratios for warm scenarios.
- `completion.csv`: completed/incomplete request counts and completion ratios for the same aggregation input.

Batch statuses:

- `ok`: summary has request rows and all core latency fields are complete/non-negative.
- `empty`: summary has no request rows.
- `incomplete`: request rows exist but a core latency field is missing or invalid.
- `driver_failed`: the per-run Docker driver exited non-zero.

The batch driver continues after failed/incomplete runs so the manifest remains useful for debugging. It exits non-zero if any run is not `ok`; add `--allow-incomplete` for exploratory runs.

The aggregate analyzer can also be run directly:

```bash
python3 experiments/content_discovery/delay_find_content/scripts/aggregate_summaries.py \
  experiments/content_discovery/delay_find_content/results/<batch_id>
```

Batch aggregation output is CSV by default. Do not commit raw batch logs/results by default; commit curated CSVs or paper figures only when they are intentionally part of the paper record.

## Plotting

Install the plotting dependency if it is not already available:

```bash
python3 -m pip install --user -r experiments/content_discovery/delay_find_content/requirements.txt
```

After a batch run finishes, generate paper-style vector figures from the CSV outputs:

```bash
experiments/content_discovery/delay_find_content/scripts/plot_results.py \
  experiments/content_discovery/delay_find_content/results/discovery-v1-n30
```

For the current Kua-style DC result:

```bash
experiments/content_discovery/delay_find_content/scripts/plot_results.py \
  experiments/content_discovery/delay_find_content/results/kua-dc-v1-kathara-n30 \
  --output-dir experiments/content_discovery/delay_find_content/paper_assets/kua-dc-v1
```

The plotting script uses Matplotlib and writes PDF/SVG by default:

- `plots/line_hop_sweep.pdf` and `plots/line_hop_sweep.svg`: line topology hop distance vs median discovery/miss latency, with min/max error bars.
- `plots/warm_path_materialization.pdf` and `plots/warm_path_materialization.svg`: cold vs path-materialization normalized median metrics.
- `plots/kua_dc_16_path_materialization.pdf` and `plots/kua_dc_16_path_materialization.svg`: Kua-style 16-device DC cold vs warm medians, split into hop distance, latency breakdown, and network work.
- `plots/kua_dc_32_path_materialization.pdf` and `plots/kua_dc_32_path_materialization.svg`: Kua-style 32-device DC cold vs warm medians, split into hop distance, latency breakdown, and network work.

Optional PNG output is available when needed:

```bash
experiments/content_discovery/delay_find_content/scripts/plot_results.py \
  experiments/content_discovery/delay_find_content/results/<batch_id> \
  --formats pdf,svg,png
```

Generated plots live under the ignored `results/` tree by default. Commit only curated figures that are intentionally part of the paper record.

## Optional Kathara Parity

```bash
experiments/content_discovery/delay_find_content/scripts/run_kathara_parity.sh line-4
experiments/content_discovery/delay_find_content/scripts/run_kathara_parity.sh binary-tree-7
```

This driver reuses the existing Kathara scenario scripts and copies their logs into the same result layout. The Kathara app startup scripts prefix `rsync-node-dev` stdout/stderr with nanosecond ISO UTC timestamps, so the same log analyzer can compute latency without protocol instrumentation.

Kathara parity is optional reproducibility evidence, not a blocker and not an input to the quantitative paper result. The Docker bridge `discovery-v1-n30` batch is sufficient for the delay-find-content figures and tables. The v2 parity checkpoint confirms the line topology end to end and records a limited branching warm trend; exact latency values are not expected to match Docker bridge results. The `line-4` parity path reuses the existing phase5f `demo_key` smoke, so it is not a numeric match for Docker `redis_key_a` runs.

Current environment note: after switching environments on 2026-06-26, Kathara runs with Docker 29.3.0 and the enabled `kathara/katharanp_vde` plugin. The tracked v2 status record is in `paper_assets/kathara-parity-v2/`. `line-4` completed with full same-schema latency output; `binary-tree-7` completed as limited qualitative parity, showing a provider-hit hop reduction from 4 to 3 while requester app-receive latency remains incomplete in current Kathara logs.

For the Kua-style DC round, use the Kathara batch runner instead of calling the parity runner directly:

```bash
experiments/content_discovery/delay_find_content/scripts/run_kathara_batch.sh \
  --run-group-id kua-dc-v1-kathara-n30 \
  --repeats 30
```

## Non-Goals For This Phase

- No `RSYNC_TRACE_PATH` / internal JSONL trace.
- No `Node` eviction API and no scenario `evictions:` field.
- No REQUEST/DATA format changes.
- No Redis-vs-InMemory comparison as a first-batch variable.
- No chunked DATA or bandwidth streaming implementation yet.
