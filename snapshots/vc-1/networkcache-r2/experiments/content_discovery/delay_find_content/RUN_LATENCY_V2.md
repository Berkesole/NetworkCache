# Running The Latency-v2 Suite

This suite prepares the data for the canonical NetworkCache latency report. It
runs only in Kathara. It does not start systemd services, poll background jobs,
or generate the final report.

## One-Time Preflight

```bash
experiments/content_discovery/delay_find_content/scripts/latency_suite/00_preflight.sh
```

Preflight checks dependencies and generated workloads, then runs a finite smoke
matrix. It includes buffered-vs-per-event trace comparison, monotonic-clock
validation, and the 32-host Zipf NC/Known-owner cases that previously exposed
incomplete traces. Valid smoke batches are reused on later invocations.

## Formal Runs

Run phases separately:

```bash
experiments/content_discovery/delay_find_content/scripts/latency_suite/10_controlled.sh
experiments/content_discovery/delay_find_content/scripts/latency_suite/20_iid_zipf.sh
experiments/content_discovery/delay_find_content/scripts/latency_suite/30_iid_uniform.sh
experiments/content_discovery/delay_find_content/scripts/latency_suite/40_load_sensitivity.sh
```

Or run the complete matrix sequentially:

```bash
experiments/content_discovery/delay_find_content/scripts/latency_suite/run_all.sh
```

The full matrix contains 12 configurations and targets 240 curated runs. On a
single machine it is expected to take roughly two days, depending on Kathara
startup time and the number of additional attempts needed. Running scripts
again is safe: latency-ready configurations are skipped, raw attempt batches
are never overwritten, and configurations below the request-level quality gate
resume with a new attempt batch.

Each configuration targets four repetitions for each of five request seeds.
Latency selection accepts requests with a complete end-to-end timestamp chain
and selects the earliest four repetitions per seed that meet the prespecified
quality gate: at least 95% usable measurement requests per run and at least 70%
usable requests in every analysis bin. Controlled workloads retain 10-request
bins; IID workloads use 20-request bins. Strict whole-run completeness remains
available for internal audit but does not control the latency estimator.

Approximate wall-clock times for the initial balanced 20 attempts per
configuration are:

| Phase | Curated runs | Typical time |
| --- | ---: | ---: |
| Controlled | 80 | 8-10 hours |
| IID Zipf, 2000 ms | 80 | 14-16 hours |
| IID Uniform, 2000 ms | 40 | 7-8 hours |
| 32-host IID Zipf, 250 ms | 40 | 2-3 hours |

Reserve at least 8 GB for raw traces, diagnostics, and possible extra attempts.
These estimates are intentionally conservative and vary with Kathara startup
cost.

## Status And Validation

Status is instantaneous and never waits:

```bash
experiments/content_discovery/delay_find_content/scripts/latency_suite/status.sh
```

After all phases finish:

```bash
experiments/content_discovery/delay_find_content/scripts/latency_suite/90_validate.sh
```

Results live under:

```text
experiments/content_discovery/delay_find_content/results/latency-v2/
```

If a foreground phase is interrupted, first close only its active Kathara lab
if one remains:

```bash
scripts/kathara/down_ruikekkk_scenario.sh
```

Then rerun the same phase script. Finished raw attempt directories are retained,
the interrupted directory is never reused, and already curated configurations
are skipped.

The key handoff files are:

```text
run_status.csv
latency_selection.csv
analysis/run_bin_means.csv
analysis/seed_bin_means.csv
analysis/overall_bin_mean_ci95.csv
analysis/latency_v2_summary.csv
```

Attempt manifests, `latency_candidates.csv`, and `completion_diagnostics.csv`
remain internal evidence. The final report describes conditional latency for
requests with a complete timestamp chain and does not use completion rate as an
experimental metric.

## Final Report

Do not run the report generator before `90_validate.sh` reports every
configuration as latency `READY`. After data review, the canonical bilingual
report is generated with:

```bash
python3 experiments/reports/generate_overall_latency_report.py
```

It emits only:

```text
experiments/reports/overall/overall.pdf
experiments/reports/overall/overall.en.pdf
```

Existing report PDFs and speaking notes are archived only after both new PDFs
have been generated and passed the basic size check.
