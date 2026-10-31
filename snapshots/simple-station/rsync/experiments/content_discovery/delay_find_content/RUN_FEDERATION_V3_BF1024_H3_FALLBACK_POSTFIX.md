# Federation-v3 staged-fallback post-fix rerun

This runner reuses the canonical 1,024-bit/3-hash workload but writes to
completely separate result and paper-asset namespaces:

- `results/federation-v3-bf1024-h3-fallback-pilot-postfix/`
- `paper_assets/federation-v3-bf1024-h3-fallback-pilot-postfix/`

It does not overwrite or select any pre-fix run. Validation additionally
requires the run's recorded Git commit to contain commit
`aa2c6f24f62d8529b30385dc90a3ebbe961ede51`. This includes both the
staged-fallback DATA-route fix and the paired local-provider obligation fix.
A descendant commit is accepted; `3744fd5` alone and all earlier commits are rejected with
`git_commit_precedes_required_postfix`.

## Smoke first

Run static/preflight checks and the isolated 88-container smoke:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_postfix_suite/00_preflight.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_postfix_suite/05_smoke.sh
```

These commands write the smoke only under the post-fix result root. They never
reuse `results/federation-v3-bf1024-h3-fallback-pilot/smoke/run001`.

## Full four-run pilot

After the post-fix smoke is accepted, run the four pilots in the foreground:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_postfix_suite/10_pilots.sh
```

Or run preflight, smoke, and pilots in sequence:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_postfix_suite/run_all.sh
```

The pilot runner stops on the first failed configuration and never overwrites a
raw directory. It generates post-fix CSV/PDF assets only after all four runs are
READY.

Read status or validate without starting containers:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_postfix_suite/status.sh
experiments/content_discovery/delay_find_content/scripts/federation_v3_bf1024_h3_fallback_postfix_suite/90_validate.sh
```

The analysis treats requester-side end-to-end latency as authoritative.
Discovery/return-path, service class, cache fraction, and serving distance are
explicitly labeled first-observed-provider-hit proxies. REQUEST counters are
logical trace send events, not packet-capture or physical link-frame counts.
