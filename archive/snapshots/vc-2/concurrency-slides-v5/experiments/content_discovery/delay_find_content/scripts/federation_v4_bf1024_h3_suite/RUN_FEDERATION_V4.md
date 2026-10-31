# Federation-v4 formal experiment

This isolated suite runs six paired dual-cluster configurations. It never reads
or overwrites federation-v3 raw results.

## Matrix

- NetworkCache: 512-key Zipf, 512-key Uniform, 1000-key Zipf, 1000-key Uniform.
- Known-owner NetworkCache unicast: 512-key Zipf.
- TCP Direct: 512-key Zipf.
- Ten deterministic seeds and two selected repetitions per seed: 120 selected runs.

Each run contains 1,024 pre-activation local requests, 2,048 post-activation
remote requests and 512 post-activation local controls. The two isolation probes
are not part of the 3,584 formal requests.

## Commands

From `/root/zzh/rsync`:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v4_bf1024_h3_suite/00_preflight.sh
experiments/content_discovery/delay_find_content/scripts/federation_v4_bf1024_h3_suite/10_networkcache.sh
experiments/content_discovery/delay_find_content/scripts/federation_v4_bf1024_h3_suite/20_baselines.sh
experiments/content_discovery/delay_find_content/scripts/federation_v4_bf1024_h3_suite/90_validate.sh
```

The formal campaign lock must already exist. The recommended preparation is
the unified `paper_final_suite/05_smoke.sh`, which freezes the validated runtime
image before any formal attempt begins.

Or run the complete foreground sequence:

```bash
experiments/content_discovery/delay_find_content/scripts/federation_v4_bf1024_h3_suite/run_all.sh
```

An interruption is resumed by executing the same command again. Raw attempt
directories are immutable. `status.sh` prints current state and returns without
waiting or starting containers.

The 120-run phase is expected to require roughly 7–10 days of cumulative
foreground time on the current host. Results are stored in:

```text
experiments/content_discovery/delay_find_content/results/federation-v4-bf1024-h3-formal/
```

The continuous timeline keeps A→A and B→B local-control series connected across
`t0`; A→B and B→A remote series begin only after activation. It never invents
pre-activation remote values.
