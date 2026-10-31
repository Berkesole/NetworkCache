# Federation convergence pilot

This directory records the separate, non-formal pilot used to choose a longer
POST observation horizon after Federation-v4. It does not modify or append to
the completed 120-run Federation-v4 campaign.

## Frozen comparison point

- 2 clusters x 32 protocol hosts
- 512 objects per cluster, Zipf alpha 1.1, NetworkCache method
- BF1024, 3 hashes, wire v3, staged fallback at 1500 ms
- 64 KiB DATA, 2 ms links, 1 Gbit/s, global closed loop
- frozen runtime image
  `sha256:bce2e56d82225efd51335a3af959ec0c0fc199782ab68281fd16f1ff16c13a74`
- 8 PRE batches and 64 POST batches, 11,264 application requests
- the original 16-POST-batch workload is an exact endpoint/key prefix

The pilot is one seed and one run. It may select a candidate horizon, but it is
not formal evidence and must not be used for a confidence interval or an
equivalence claim.

## Convergence decision

The analyzer uses non-overlapping four-batch windows. A boundary passes only
when all of the following hold:

- remote end-to-end latency changes by at most 5% and at most 10 ms;
- its least-squares slope over the last eight batches is at most 2 ms/batch in
  absolute value;
- preferred and fallback logical REQUEST means change by at most 0.10;
- the cache-service fraction proxy changes by at most 0.03;
- the serving-hop proxy changes by at most 0.10.

Two consecutive boundaries must pass. The earliest second boundary is the
candidate POST length. If no candidate is found by batch 64, inspect the
mechanism traces before changing protocol parameters or extending the horizon.

## Server target

The target is `simple-station:/home/simpleai/zhengzihao/rsync`. The server must
first grant `simpleai` Docker access and install the small host dependency set.
Run the bootstrap below as root, then start a fresh SSH login:

```bash
bash experiments/distributed_federation_convergence/bootstrap_simple_station.sh
```

After the frozen runtime, Kathara venv, VDE plugin, and source snapshot have
been deployed, the execution interface is:

```bash
python3 experiments/content_discovery/delay_find_content/scripts/generate_federation_convergence_pilot.py
python3 experiments/content_discovery/delay_find_content/scripts/run_federation_convergence_pilot.py preflight
python3 experiments/content_discovery/delay_find_content/scripts/run_federation_convergence_pilot.py run --execute
python3 experiments/content_discovery/delay_find_content/scripts/run_federation_convergence_pilot.py status
```

`status` is read-only and exits immediately. Raw output is append-only under
`results/federation-convergence-pilot/attempts/`; the runner refuses to
overwrite `attempt001`.
