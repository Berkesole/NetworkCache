# NetworkCache latency-v4 manual run guide

This suite is isolated as `latency-v4-bf1024-h3-c512`. It does not read from or
write to latency-v3 or federation result directories.

## Fixed experiment definition

- 512 formal objects per single cluster and 32 separate setup objects.
- Wire v3, 1024-bit Bloom filter, three fixed FNV-1a64 hashes.
- Staged QueryTable fallback: 1500 ms source deadline and 7000 ms lifetime.
- 64 KiB is the primary DATA size; the size sweep is 1 KiB, 64 KiB and 1 MiB.
- Global closed loop with one request in flight and 100 ms think time.
- Redis is unlimited and uses `noeviction`.

Pilot history remains below:

```text
experiments/content_discovery/delay_find_content/results/latency-v4-bf1024-h3-c512/
```

Corrected formal R2 attempts are written below and never select pilot or
pre-R2 attempts:

```text
experiments/content_discovery/delay_find_content/results/latency-v4-bf1024-h3-c512-formal-r2/
```

An existing raw run directory is never overwritten.

## 1. Preflight and reduced smoke

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/00_preflight.sh
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/05_smoke.sh
```

The smoke uses the 16-host controlled schedule and the complete 512-object
catalog. It is not part of the 380 selected runs.

## 2. Three full pilots

Run the following in the foreground:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/06_pilot.sh
```

It runs, in order, 16-host Zipf NC, 32-host Zipf NC and 32-host Uniform NC for
seed 001. Formal runners remain blocked until request completeness, exact DATA
integrity, single delivery, expiry, overlap and raw hashes have been accepted
into `pilot_acceptance.json`.

The formal campaign fixes the IID observation horizon at 4,096 requests. The
earlier 2,048-request pilots remain exploratory history. Generate and validate
the paired 4,096-request pilots with:

```bash
python3 experiments/content_discovery/delay_find_content/scripts/generate_latency_v4_bf1024_h3_c512.py \
  --iid-requests 4096 --check
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/06_pilot.sh
```

The 4,096-request pilot uses a different result subdirectory. Late-window
diagnostics are retained, but they are not a convergence gate and the report
does not claim strict IID convergence.

## 3. Formal matrix

After pilot acceptance and the multi-method R2 smoke, use the canonical paper
campaign entry point:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/paper_final_suite/run_all.sh
```

Or run individual phases:

```bash
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/10_controlled.sh
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/20_iid_zipf.sh
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/30_iid_uniform.sh
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/40_data_size.sh
```

`run_all.sh` continues after a configuration reaches its attempt limit and exits
nonzero if any configuration remains incomplete. Re-running the same command
resumes the matrix and skips qualified repetitions. Infrastructure image-build
failures are recorded separately and do not consume the four experimental
attempts per seed.

The fixed 4,096-request formal matrix is expected to require roughly 7–10 days
of foreground cumulative runtime on the current host.

## Status and validation

Both commands return immediately and never poll:

```bash
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/status.sh
experiments/content_discovery/delay_find_content/scripts/latency_v4_bf1024_h3_c512_suite/90_validate.sh
```

The final target is 19/19 `READY`, 380 selected runs, and exactly two selected
repetitions for every one of ten seeds in every configuration.

## Report generation

The report generator refuses to run before all 19 configurations and all paired
ten-seed effects are complete:

```bash
python3 experiments/reports/generate_latency_v4_bf1024_h3_c512_report.py
```

It writes only:

```text
experiments/reports/networkcache-bf1024-h3-formal-r2/overall.pdf
experiments/reports/networkcache-bf1024-h3-formal-r2/overall.en.pdf
```

The report is latency-only. It does not modify the existing latency-v3 or
federation reports.
