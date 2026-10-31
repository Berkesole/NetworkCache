# Federation-v3 2048-bit/3-hash large-catalog pilot

This isolated exploratory suite runs exactly four single-seed NetworkCache
experiments in this fixed order:

1. 512 objects per cluster, Zipf alpha 1.1;
2. 512 objects per cluster, Uniform;
3. 1,000 objects per cluster, Zipf alpha 1.1;
4. 1,000 objects per cluster, Uniform.

Each run contains 3,584 measured requests: 1,024 pre-link local requests, 2,048
post-link remote requests, and 512 post-link local controls. All values are 64 KiB.
The four schedules share the same requester, owner-role, direction, timing and
random-quantile sequence. The 512-object catalog is a deterministic subset of the
1,000-object catalog.

The pilot explicitly sets a 2,048-bit Bloom filter and three fixed salted FNV-1a
hashes in every NetworkCache component. Wire v2 REQUEST frames are 282 bytes, or
286 bytes with the TCP length prefix. The maximum hop count is 12. Legacy
experiments retain their default 1,024-bit/one-hash configuration.

## Isolation and outputs

- Inputs: `workloads/federation-v3-bf2048-h3-pilot/`
- Immutable raw runs: `results/federation-v3-bf2048-h3-pilot/`
- Exploratory outputs: `paper_assets/federation-v3-bf2048-h3-pilot/`

The suite does not modify federation-v3-pilot results or any overall report.
There is one allowed run directory per configuration. If a run or a quality gate
fails, the runner preserves it and stops immediately; it never retries or starts
the remaining configurations.

One recorded exception is encoded explicitly: the initial `catalog512-zipf/run001`
was stopped before any request because the node startup renderer omitted the new
Bloom environment variables. It is retained as an infrastructure diagnostic, and
the runner permits exactly one replacement at `run002`. This exception does not
permit `run003` or retries after any request has been issued.

## Commands

Run from the repository root:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf2048_h3_suite/00_preflight.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf2048_h3_suite/run_all.sh
```

Read-only, immediate status and validation commands are:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf2048_h3_suite/status.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf2048_h3_suite/90_validate.sh
```

`run_all.sh` skips a configuration only when its existing `run001` passes every
quality gate. An existing failed directory must be inspected and is never
overwritten. Four full runs typically need three to five hours, depending on
Kathara startup and host load.

## Acceptance and interpretation

Every run must complete 3,584/3,584 requests with correct DATA size and hash,
contain no overlapping request intervals, pass pre-link isolation and boundary
activation checks, provide at least 99% trace decomposition, and contain every
cache, SyncTable, clock and network snapshot. All 64 protocol nodes must report
2,048 bits and three hashes.

After all four runs pass, the suite writes `pilot_trends.pdf` and exploratory CSV
files under the paper-assets directory. They include correct and unrelated
provider hits, unrelated DATA, REQUEST forwarding, SyncTable occupancy, unique
keys, cache service and pre/post latency trajectories. These single-seed results
have no confidence intervals and are not paper results. The forwarding fallback
semantics are deliberately unchanged, so this pilot evaluates whether the Bloom
configuration is sufficient; it does not claim false positives are eliminated.
