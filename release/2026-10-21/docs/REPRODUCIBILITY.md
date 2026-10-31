# Reproducibility and execution

## Supported local checks

Use the Debug, Redis-disabled build in the [quick start](../README.md#quick-start). The verification runner requires only Python's standard library. No external hosts or datasets are used.

| Check | What is verified | What it does not establish |
| --- | --- | --- |
| `core` | Existing cache and Bloom-filter assertions | End-to-end routing or throughput |
| `mock_delivery` | The requested key/value reaches node 1 in linear and triangle scenarios | Multi-process timing or wire behavior |
| `tcp_delivery` | The provider, relay, and requester exchange the expected data in three independent processes | WAN latency, large-scale behavior, or fault tolerance |

Run all checks with `ctest --test-dir build --output-on-failure`, or select a runner case:

```sh
python3 scripts/tests/verify_local.py --build-dir build --case core
python3 scripts/tests/verify_local.py --build-dir build --case mock
python3 scripts/tests/verify_local.py --build-dir build --case tcp
```

The TCP check allocates three distinct ephemeral loopback ports. It waits for provider and relay initialization before issuing the request, checks exact data delivery in the logs, and verifies normal process exit. It does not report success based on exit status alone. Temporary files are removed after the run; failures print the node logs. Loopback sockets must be permitted by the execution environment.

CTest registers the integration checks only when Python is found during CMake configuration. Check that all three expected cases appear. The core test uses `assert`; the artifact build retains these assertions for GNU/Clang even when a different build type is selected. Debug is the validated configuration.

## Optional Redis backend

Redis is disabled by default. A Redis-enabled build requires a local `redis-server`, hiredis, and redis-plus-plus. The CMake helper can use installed libraries or fetch hiredis **v1.2.0** and redis-plus-plus **1.3.10**. Fetching requires Git and network access.

Use a separate build directory:

```sh
cmake -S . -B build-redis -DCMAKE_BUILD_TYPE=Debug \
  -DENABLE_REDIS=ON -DFETCH_REDIS_DEPS=ON
cmake --build build-redis --target test_redis --parallel 2
bash scripts/tests/run_redis_suite.sh \
  build-redis/test_redis_cache_contract build-redis/rsync-node-dev
```

If the libraries are already installed, set `-DFETCH_REDIS_DEPS=OFF`. Confirm that CMake prints `rsync-node-dev: Redis support enabled`; the inherited dependency helper otherwise disables Redis when required libraries are absent. A missing `test_redis` target indicates that Redis support was not configured.

The suite starts a private Redis process with TCP disabled and a temporary Unix socket, runs cache contract tests, checks runtime backend selection and failure behavior, then stops its own server. When built with Redis support, `rsync-node-dev` selects Redis by default and exits if its socket is unavailable. The main `rsync-router` scenario runner explicitly uses in-memory caches.

## Optional containers

```sh
bash docker/build.sh latest
bash docker/run.sh latest
```

The wrapper builds from this repository regardless of the invoking directory. The run wrapper executes the default local verification suite in a disposable container, overriding the service entrypoint. Docker image building needs network access for the base image and system packages. No host data directory is mounted by this check.

The image also retains the Redis/Supervisor configuration used by the network-emulation labs. Its normal entrypoint manages those services; Redis cache support must be enabled at image build time when a lab needs it. A Redis health check alone does not verify data delivery.

## Optional Kathara labs

Two inherited labs are included: a four-node line and a five-node branch. They require Docker, Kathara, and a Redis-enabled image. The launch helpers build the required image by default and render the endpoint topology from [config/acceptance](../config/acceptance). These directory names are retained for script compatibility.

```sh
bash scripts/kathara/phase5f_linear_up.sh
# Inspect kathara/phase5f-linear-smoke/shared/logs/
bash scripts/kathara/phase5f_linear_down.sh

bash scripts/kathara/phase6a_acyclic_branch_up.sh
# Inspect kathara/phase6a-acyclic-branch-smoke/shared/logs/
bash scripts/kathara/phase6a_acyclic_branch_down.sh
```

The `up` helpers clean and restart their selected lab; the `down` helpers stop it. These are optional deployment examples and are not included in the validated local check results.

## Relationship to performance results

This package contains the implementation snapshot and functional examples available in the source repository. It does not contain a verified export of the external multi-host experiment environment, the complete workload-generation and measurement pipeline, or raw data and plotting inputs for all reported results. No figure-level performance reproduction is claimed for this snapshot.

The local checks establish that selected paths execute and deliver the expected value. They do not measure speedup, bandwidth reduction, sensitivity to cache capacity, or performance at cluster scale. Reproducing such results requires the corresponding experiment revision, configuration, workloads, and measurement scripts in addition to these sources. See [implementation semantics](ARCHITECTURE.md#snapshot-semantics) for behavior that affects interpretation.

## Validation record

The packaging verification results and tool versions are recorded in [VALIDATION.md](VALIDATION.md). Optional Redis and network-emulation runs are listed separately from checks that were actually executed.
