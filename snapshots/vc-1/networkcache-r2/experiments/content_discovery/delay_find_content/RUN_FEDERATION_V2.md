# NetworkCache federation-v2 manual run guide

This suite is isolated from `latency-v3` and `federation-v1`. It does not overwrite their workloads, raw attempts, assets, or reports.

## What one run measures

Two 32-node Kua-DC v3 systems start independently. Their node1–node33 physical link is down and neither overlay contains a remote endpoint. One run then performs:

- 128 pre-connection local requests: 8 rounds × (8 A-local + 8 B-local);
- two reserved-key isolation probes, which must not return DATA;
- local boundary activation at `t0` on node1 and node33 only;
- 128 post-connection remote requests: 8 rounds × (8 A→B + 8 B→A);
- 64 post-connection local controls: 8 rounds × (4 A-local + 4 B-local).

All 320 measured requests carry 64 KiB DATA and execute with at most one request in flight globally. Redis is unbounded (`maxmemory 0`) and uses `noeviction`. Cache occupancy is observed between rounds; it is not a capacity or LRU experiment.

The four configurations are NetworkCache repeated, Known-owner NetworkCache unicast repeated, TCP Direct repeated, and NetworkCache Uniform. Each targets 10 seeds × 2 accepted repetitions. An attempt is selected only when all 320 requests complete with correct length/hash and all quality gates pass. At most four attempts are made per seed, and selection always uses the earliest two qualifying attempts.

## Preflight and smoke

Static preflight does not run the formal matrix:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/federation_v2_suite/00_preflight.sh
```

To rerun the four reduced 88-container smoke configurations explicitly:

```bash
FEDERATION_V2_RUN_SMOKE=1 \
experiments/content_discovery/delay_find_content/scripts/federation_v2_suite/00_preflight.sh
```

Smoke outputs are placed under `results/federation-v2-smoke/` and never enter formal selection.

## Formal run

Run everything in the foreground:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/federation_v2_suite/run_all.sh
```

The current estimate is approximately 10–14 hours for 80 selected runs, but Kathara startup time and retries can increase it. No background queue or Codex polling is required.

Individual phases:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v2_suite/10_federation_repeated.sh
experiments/content_discovery/delay_find_content/scripts/federation_v2_suite/20_federation_uniform.sh
```

All scripts are resumable. Re-running a phase never overwrites a raw attempt and skips every seed/configuration that already has two accepted runs.

## Status and validation

Immediate, read-only status:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v2_suite/status.sh
```

Final validation and analysis:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v2_suite/90_validate.sh
```

The final state must be 4/4 `READY`, 80 selected runs, and exactly two selected repetitions for every configuration/seed. `attempt_completion.csv` retains scheduled, issued, and completed counts for all attempts.

Formal raw data is under `results/federation-v2/attempts/`. Deterministic selection is in `federation_selection.csv`; analysis is in `results/federation-v2/analysis/`; accepted CSVs and figures are mirrored to `paper_assets/federation-v2/`.

## Report gate

The existing latency-v3 PDFs are not replaced by the experiment runner. After both latency-v3 (19/19) and federation-v2 (4/4) are READY, generate and atomically replace the bilingual reports with:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v2_suite/95_report.sh
```

The report describes local boundary bootstrap and on-demand remote discovery. It does not claim zero-configuration member discovery, cache-capacity scaling, throughput, congestion behavior, or failure recovery.
