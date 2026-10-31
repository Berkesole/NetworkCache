# Content Discovery Experiments

This directory is the first paper-oriented experiment area for NetworkCache content discovery. The current phase is deliberately non-invasive: experiment code observes the existing runtime from the outside and does not change the protocol implementation under `src/`.

## Scope

- **Primary environment:** Kathara, used for the final paper-facing datacenter topology results.
- **Mirror environment:** packaged Docker containers on an isolated Docker bridge network, used to run the same protocol graphs as a pipeline and reproducibility cross-check.
- **Local sanity only:** MockNetwork remains useful for quick script/debug checks, but it is not a paper result environment.
- **Protocol boundary:** no REQUEST/DATA wire-format changes, no internal JSONL trace, no eviction API, and no changes to QueryTable/SyncTable/cache semantics in this phase.

## First Metric Group

The first implemented metric group is `delay_find_content/`. It measures the current protocol behavior through timestamped container logs:

- `request_start`: requester log line such as `User wants file: <key>`.
- `provider_hit`: provider log line such as `Sending DATA for key=<key> ...`.
- `app_receive`: requester log line such as `[App] Received data for key=<key> ...`.

Derived latencies:

- `discovery_delay = provider_hit - request_start`
- `return_delay = app_receive - provider_hit`
- `miss_latency = app_receive - request_start`

Packet and table counters are parsed from existing log patterns, including `sent REQUEST packet`, `sent DATA packet`, `flush AGGREGATE`, `via SyncTable`, and `no SyncTable hit` when present.

## Warm Definition

The first warm experiment is **path materialization warm**:

1. requester A asks for `redis_key_a`, which is initially held by a provider.
2. DATA returns along the path and current cache semantics may materialize that key on intermediate nodes.
3. requester B later asks for the same key.
4. The second request should be distinguishable from the cold request and may be satisfied closer to the requester.

`SyncTable hint warm + eviction` is intentionally deferred because eviction requires an explicit node control action and is outside pure observation of the current protocol.

## Layout

- `delay_find_content/configs/`: Docker-ready topology/scenario YAML files, packaged into the experiment image and copied into each run record.
- `delay_find_content/scripts/run_kathara_batch.sh`: Kathara batch driver for the Kua-style 16/32 datacenter round.
- `delay_find_content/scripts/run_docker_bridge.sh`: Docker bridge mirror driver.
- `delay_find_content/scripts/run_kathara_parity.sh`: single-run Kathara driver, retained for parity and DC smoke runs.
- `delay_find_content/scripts/analyze_logs.py`: stdlib log analyzer that writes `summary.csv`.
- `delay_find_content/results/`: generated run outputs, ignored by git except placeholders.
- `bandwidth_streaming_delay/`: reserved for the next metric group.
