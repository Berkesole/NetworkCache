# Run NetworkCache latency-v3

latency-v3 is isolated from latency-v2. Raw attempts are written only below
`experiments/content_discovery/delay_find_content/results/latency-v3/` and are
never overwritten. The old reports and result directories are not inputs.

## Preparation

Run the static checks and immutable engineering smoke matrix first:

```bash
cd /root/zzh/rsync
experiments/content_discovery/delay_find_content/scripts/latency_v3_suite/00_preflight.sh
```

The smoke matrix uses the required 16/32-host, NC, Known-owner, TCP Direct,
size, and 4 MiB pilot coverage. It validates and skips immutable READY smoke
directories on resume. To run one case:

```bash
python3 experiments/content_discovery/delay_find_content/scripts/run_latency_v3_smoke.py \
  --case controlled16-nc-64k
```

Smoke data is never selected formally.

## Formal execution

Run every phase in the foreground:

```bash
experiments/content_discovery/delay_find_content/scripts/latency_v3_suite/run_all.sh
```

Or run independent phases:

```bash
experiments/content_discovery/delay_find_content/scripts/latency_v3_suite/10_controlled.sh
experiments/content_discovery/delay_find_content/scripts/latency_v3_suite/20_iid_zipf.sh
experiments/content_discovery/delay_find_content/scripts/latency_v3_suite/30_iid_uniform.sh
experiments/content_discovery/delay_find_content/scripts/latency_v3_suite/40_data_size.sh
```

The runner targets two valid repetitions for each of ten seeds and permits at
most four immutable attempts per seed/config. Re-running a phase resumes from
manifests and skips READY seed/config pairs.

## Status and validation

These commands only read existing results and return immediately:

```bash
experiments/content_discovery/delay_find_content/scripts/latency_v3_suite/status.sh
experiments/content_discovery/delay_find_content/scripts/latency_v3_suite/90_validate.sh
```

Formal completion is 19 READY configurations and 380 selected runs. A
`NEEDS_MORE` configuration retains all evidence and causes final validation to
exit non-zero. Do not copy smoke runs into the formal results tree.

No report is generated during execution. The bilingual files under
`experiments/reports/latency-v3/` are generated only after all formal data pass
validation; `experiments/reports/overall/` remains untouched.

## Fixed latency-v3 runtime conditions

Legacy defaults remain unchanged. latency-v3 explicitly records and gates:

- the dedicated `Dockerfile.latency-v3`, which recompiles the current source
  from pre-existing project Redis/runtime layers without contacting package
  mirrors during the formal matrix;
- Bloom wire v2 (154-byte REQUEST codec frame, or 158 bytes with TCP length);
- QueryTable lifetime 4500 ms and controller request timeout 5000 ms;
- a true post-completion protocol-quiescence barrier, then 100 ms think time;
- quiescence stability windows of 250 ms (1 KiB), 500 ms (64 KiB), and
  2000 ms (1 MiB); the non-formal 4 MiB pilot uses 5000 ms;
- identical Linux time-namespace offsets across all hosts;
- Redis noeviction/unlimited memory, empty DB, and 1000 ms client timeout;
- every egress at 2 ms, 1 Gbit/s, limit 1000, and MTU 1500.

The stability window is outside request latency. It prevents residual
NetworkCache traffic from being aggregated with the next canonical request.
Budget roughly 2--4 minutes for a 64 KiB IID run and 5--7 minutes for a 1 MiB
Controlled run on the reference machine, plus topology startup/teardown.

The two required local project layers are checked during preflight. A Docker
build failure is retained as an infrastructure diagnostic, stops the current
phase immediately, and does not consume one of a seed's four experiment
attempts. This avoids repeating a common infrastructure failure across the
matrix.

The old environment spelling remains accepted for smoke compatibility, but is
no longer required because the mirror-independent Dockerfile is the v3
default:

```bash
LATENCY_V3_OFFLINE_BUILD=1 \
experiments/content_discovery/delay_find_content/scripts/latency_v3_suite/00_preflight.sh
```
