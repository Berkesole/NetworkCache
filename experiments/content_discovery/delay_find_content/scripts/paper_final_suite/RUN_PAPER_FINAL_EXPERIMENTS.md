# NetworkCache formal BF1024/H3 experiment campaign R2

This package is the only manual entry point for the corrected R2 formal
campaign. It does not overwrite latency-v3, federation-v3, or the interrupted
pre-R2 formal attempts.

## Fixed campaign

- NetworkCache wire v3, 1024-bit Bloom filter, three fixed FNV-1a64 hashes.
- Staged QueryTable fallback: 1.5 s source deadline, 7 s lifetime, 12-hop limit.
- Latency-v4: 19 configurations, 380 selected runs, 4,096 IID requests per run.
- Federation-v4: six configurations, 120 selected runs, 3,584 requests per run.
- Ten deterministic seeds and two selected repetitions per configuration/seed.
- Earliest qualified attempts are selected; latency values never affect selection.

The 4,096-request IID workload is a fixed observation horizon. The analysis
reports early and late request batches and does not claim strict convergence.
Known-owner requests track exact origin completion state but have fallback
disabled; they never degrade into Bloom-guided search.

## Full foreground command

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/paper_final_suite/run_all.sh
```

Expected cumulative foreground time is approximately 14–20 days. Keep the
terminal session alive. The scripts do not create a background service or poll
after returning.

## Independent phases

```bash
experiments/content_discovery/delay_find_content/scripts/paper_final_suite/00_preflight.sh
experiments/content_discovery/delay_find_content/scripts/paper_final_suite/05_smoke.sh
experiments/content_discovery/delay_find_content/scripts/paper_final_suite/10_latency_v4.sh
experiments/content_discovery/delay_find_content/scripts/paper_final_suite/20_federation_v4.sh
experiments/content_discovery/delay_find_content/scripts/paper_final_suite/90_validate.sh
```

The same phase command resumes after interruption. Qualified configurations are
skipped; every raw attempt directory remains immutable.

## Immediate status

```bash
experiments/content_discovery/delay_find_content/scripts/paper_final_suite/status.sh
```

`status.sh` only reads manifests. It neither waits nor starts Kathara.

## Storage and campaign lock

Preflight requires at least 200 GiB free disk and 16 GiB available memory. The
expected raw footprint is 100–180 GiB, depending on rejected attempts.

`05_smoke.sh` runs only reduced smoke workflows and then freezes the runtime
image. It does not start a formal attempt. The campaign lock is written to:

```text
experiments/content_discovery/delay_find_content/results/formal-bf1024-h3-campaign-r2/
```

It binds the experiment source, generated workloads, fixed protocol parameters,
and the smoke-built runtime image ID. Formal attempts always reuse this image;
they never rebuild it. If any bound file or the image changes, resume stops
instead of mixing implementations.

## Final outputs

Only after 19/19 latency configurations and 6/6 federation configurations are
READY, validation generates:

```text
experiments/reports/networkcache-bf1024-h3-formal-r2/overall.pdf
experiments/reports/networkcache-bf1024-h3-formal-r2/overall.en.pdf
experiments/content_discovery/delay_find_content/paper_assets/networkcache-bf1024-h3-formal-r2/
```

Formal raw results are isolated under:

```text
experiments/content_discovery/delay_find_content/results/latency-v4-bf1024-h3-c512-formal-r2/
experiments/content_discovery/delay_find_content/results/federation-v4-bf1024-h3-formal-r2/
```

The pre-R2 successful NC run and incomplete Known-owner diagnostics remain in
their original directory and are never considered by R2 selection.

Federation timeline figures keep the local A→A and B→B series continuous across
activation. Remote A→B and B→A series begin after `t0`; no pre-activation remote
value is invented.
