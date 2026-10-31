# Federation-v3 large-catalog pilot

This isolated exploratory suite compares one matched seed under three workloads:

1. 1,000 objects per cluster, Zipf alpha 1.1;
2. 10,000 objects per cluster, Zipf alpha 1.1;
3. 10,000 objects per cluster, Uniform.

Each run contains 3,584 measured requests: 1,024 pre-link local requests, 2,048
post-link remote requests, and 512 post-link local controls. Values are 64 KiB.
The two clusters remain physically and logically isolated until the controller
activates only node1 and node33. These pilots are descriptive and do not produce
confidence intervals or modify either latency-v3 overall report.

## Isolation

- Canonical inputs: `workloads/federation-v3-pilot/`
- Short-smoke inputs: `workloads/federation-v3-pilot-smoke/`
- Immutable raw runs: `results/federation-v3-pilot/`
- Exploratory outputs: `paper_assets/federation-v3-pilot/`

Existing raw directories are never overwritten. Each pilot accepts exactly one
run. A failed run stops the sequence; the runner does not create a replacement or
a fourth experiment.

## Commands

From the repository root:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v3_pilot_suite/00_preflight.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_pilot_suite/05_smoke.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_pilot_suite/run_all.sh
```

The smoke loads the full 20,000-object catalog into the 88-container lab but runs
only 224 measured requests. The pilot runner refuses to start before this smoke is
READY. The three pilots run in the fixed order listed above and typically require
about four to five hours in total, depending on host load and Kathara startup.

Instant, non-waiting status and read-only validation are:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v3_pilot_suite/status.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_pilot_suite/90_validate.sh
```

`run_all.sh` is resumable only in the conservative sense: it skips a pilot that
already passes every gate. If an existing run directory fails validation, it is
preserved and execution stops for manual inspection.

## Acceptance and interpretation

Every selected pilot must complete all 3,584 requests with the expected size and
hash, have no overlapping request intervals, pass both pre-link isolation probes,
activate only the two boundary nodes, provide at least 99% trace decomposition,
and contain all cache and SyncTable snapshots.

`pilot_warmup_assessment.csv` applies the predeclared checks. Pre bins 5--6 are
compared with bins 7--8 separately for A and B. Post remote bins 13--14 are
compared with bins 15--16. Uniform non-stability is retained as a descriptive
outcome. The pilot never changes alpha, seed, or workload after observing results.

Primary outputs after all three runs are:

- `pilot_trends.pdf`
- `pilot_bin_summary.csv`
- `pilot_key_progress.csv`
- `pilot_cache_occupancy.csv`
- `pilot_bloom_occupancy.csv`
- `pilot_warmup_assessment.csv`

No file below `experiments/reports/` is written by this suite.
