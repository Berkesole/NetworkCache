# Run NetworkCache federation-v1

This suite is isolated under `workloads/federation-v1`, `results/federation-v1`,
and `paper_assets/federation-v1`. It never writes latency-v3 raw results.

The experiment starts two independently configured 32-node clusters. Their
only cross-cluster link is down during 64 local warm-up requests. At `t0`, only
nodes 1 and 33 enable that link and install the other boundary endpoint as a
runtime NetworkCache neighbor. This is local bootstrap information, not
zero-configuration membership discovery. Measurement then issues 128 globally
closed-loop, bidirectional remote requests carrying deterministic 64-KiB DATA.
The 32 local warm-up objects and 64 remote objects occupy 96 distinct slots in
the production 1024-bit Bloom filter, so catalog collisions cannot manufacture
false SyncTable routes in this workload.

Run the static checks and explicit 88-container smoke:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/federation_v1_suite/00_preflight.sh
FEDERATION_V1_RUN_SMOKE=1 experiments/content_discovery/delay_find_content/scripts/federation_v1_suite/00_preflight.sh
```

Run the formal matrix in the foreground:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v1_suite/run_all.sh
```

Stages can be resumed independently with `10_federation_repeated.sh` and
`20_federation_uniform.sh`. Existing raw attempt directories are never
overwritten. `status.sh` returns immediately and starts no service. Each
configuration targets 10 seeds × 2 valid repetitions and permits at most four
attempts per seed. The complete matrix is 80 selected runs.

Only one runner may use the fixed Kathara lab at a time. A second invocation
fails on `.federation-v1.lock` instead of corrupting the active run. After an
abrupt machine/process termination, first run
`scripts/kathara/down_ruikekkk_scenario.sh`, verify no federation runner remains,
and then remove only the stale lock directory before resuming.

After `90_validate.sh` reports 4/4 READY, federation analysis is written under
`results/federation-v1/analysis`. Only then may the guarded report generator
replace the two PDFs in `experiments/reports/latency-v3/`.

Generate the guarded bilingual report only after validation:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v1_suite/95_report.sh
```
