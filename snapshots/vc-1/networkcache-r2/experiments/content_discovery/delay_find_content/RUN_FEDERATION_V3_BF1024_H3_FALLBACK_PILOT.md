# Federation-v3 1K/3-hash staged-fallback pilot

This suite is isolated from latency-v3 and the previous 2K/4K Bloom pilots. It
uses wire v3, a 1,024-bit Bloom filter with three salted hashes, and the staged
QueryTable fallback mechanism. It never writes either overall report.

The preferred phase follows SyncTable matches. A node immediately expands to
its remaining neighbors when matched choices disappear after excluding the
upstream edge. If the origin still has an exact unsatisfied key after 1,500 ms,
it emits a fallback request; fallback forwarding ignores SyncTable direction
and uses the stable request UID, per-edge state, hop limit 12, and seven-second
QueryTable lifetime for bounded duplicate suppression.

## Fixed pilot matrix

The order is fixed: 512-key Zipf, 512-key Uniform, 1,000-key Zipf, and
1,000-key Uniform. Every configuration has one seed and one immutable attempt,
with 3,584 measured requests: 1,024 pre-link local requests, 2,048 post-link
remote requests, and 512 post-link local controls. Each DATA value is 64 KiB.
An incomplete or rejected attempt stops the sequence; it is not overwritten or
automatically retried.

Fixed runtime parameters are:

- `RSYNC_WIRE_VERSION=3`
- `RSYNC_BLOOM_BITS=1024`, `RSYNC_BLOOM_HASH_COUNT=3`
- `RSYNC_STAGED_FALLBACK=1`, `RSYNC_FALLBACK_DELAY_MS=1500`
- `RSYNC_QUERY_TIMEOUT_MS=7000`, controller timeout 8,000 ms
- 100 ms think time, 2 ms links, 1 Gbit/s, MTU 1500, queue 1000

## Commands

Run all static checks, the 88-container short smoke, and then the four pilots:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_suite/run_all.sh
```

Individual stages are:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_suite/00_preflight.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_suite/05_smoke.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_suite/10_pilots.sh
```

Read status without waiting or starting containers:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_suite/status.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_suite/90_validate.sh
```

Re-running resumes by skipping READY configurations. If an existing raw attempt
is not READY, the runner stops and prints its reason instead of overwriting it.

## Outputs

Raw attempts are stored under
`results/federation-v3-bf1024-h3-fallback-pilot/`. Descriptive CSVs and
`pilot_trends.pdf` are written under
`paper_assets/federation-v3-bf1024-h3-fallback-pilot/` only after all four
pilots are READY. These original four runs predate the staged-fallback DATA-route
and local-provider obligation fixes and are retained as **pre-fix diagnostics only**; they must not be reused as
the final trend dataset. The additional `pilot_fallback_summary.csv` reports
source fallback triggers, trigger-to-application-delivery time, observed
immediate dead-end expansion, logical REQUEST send events, and duplicate
suppression. It does not claim physical frame counts or causal recovery.

`pilot_run_level_request_accounting.csv` reports full raw-trace logical send
events and separates the two intentional isolation-probe lifecycles. The
per-request/bin CSVs stop at first application receipt and therefore omit later
request-lifecycle traffic. `pilot_analysis_notes.md` records these scopes,
first-observed-provider proxy limitations, provenance, and warm-up decisions.

Use `RUN_FEDERATION_V3_BF1024_H3_FALLBACK_POSTFIX.md` and its isolated runner
for any post-fix smoke or full pilot. The post-fix runner cannot select these
historical READY directories.

These single-seed pilot figures do not contain confidence intervals and are not
paper results. They are used to validate reachability and decide whether the
large-directory request timeline is long enough before designing a formal
multi-seed experiment.
