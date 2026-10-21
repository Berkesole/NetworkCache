# NetworkCache

Source artifact for a prototype of named data retrieval and reuse across connected nodes. Applications request data by string keys; nodes use Bloom-filter summaries, learned next-hop information (SyncTable), and pending demand (QueryTable) to discover and return data. The package includes the C++ implementation, example topologies, functional tests, and optional Redis and container deployment support.

This snapshot supports functional inspection and local execution. It does not include the external experiment infrastructure or datasets needed to regenerate every evaluation figure. See [reproducibility and scope](docs/REPRODUCIBILITY.md) for the supported checks and their limits.

## Quick start

Requirements: Linux, a C++20 compiler, CMake 3.20 or later, a build tool such as GNU Make, and Python 3.8 or later for integration checks. The default build requires no Redis, Docker, or third-party downloads.

Run from this repository's root:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug \
  -DENABLE_REDIS=OFF -DFETCH_REDIS_DEPS=OFF
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
```

Expected result: three passing CTest cases: `core`, `mock_delivery`, and `tcp_delivery`. The last case runs three independent processes on loopback TCP ports and checks the returned key/value and relay delivery. It usually takes several seconds.

For individual check output, use:

```sh
python3 scripts/tests/verify_local.py --build-dir build
```

`make test` is an alternative that configures, builds, and runs the same functional checks.

## Run a scenario

```sh
./build/rsync-router config/3-node-topology.yaml
```

This runs three nodes in one process using MockNetwork. Node 3 starts with `redis_key_a=VALUE_OF_A`; node 1 requests the key through node 2. The key name is historical and does not require Redis. Successful delivery includes:

```text
[Node 1] Received DATA for key=redis_key_a
[App] Received data for key=redis_key_a, size=10, content="VALUE_OF_A"
```

Other topologies are in [config](config). These are small scenarios for inspecting protocol behavior, not performance benchmarks. The optional `test_mesh_demo` executable is an illustrative scenario and is not counted as a correctness test merely because it exits successfully.

For a node running in a separate TCP process:

```sh
./build/rsync-node-dev --help
python3 scripts/tests/verify_local.py --build-dir build --case tcp
```

The TCP check generates its own topology, starts the provider before the requester, verifies delivery, and removes only its own processes and temporary files. To deploy nodes manually, provide a topology with each node's `id`, `host`, `port`, and `neighbors`, then use `--node-id`, `--topology`, `--seed`, and `--request-keys` as appropriate. A request is issued during startup, so its peers must already be running.

## Repository layout

| Path | Contents |
| --- | --- |
| [src](src) | Request/data handling, Bloom filters, state tables, caches, and transports |
| [app/rsync_node_main.cpp](app/rsync_node_main.cpp) | One node per process, with TCP or mock transport |
| [main.cpp](main.cpp) | In-process topology and scenario runner |
| [config](config) | Small mock scenarios and example deployment topologies |
| [tests](tests) | Core assertions, Redis cache contracts, and a mesh demo |
| [scripts/tests](scripts/tests) | Functional verification and isolated Redis test runners |
| [docker](docker), [Dockerfile](Dockerfile) | Optional container build and runtime configuration |
| [kathara](kathara), [scripts/kathara](scripts/kathara) | Optional network-emulation labs and launch helpers |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Implementation walkthrough and source map |
| [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) | Checks, optional dependencies, and reproducibility scope |
| [docs/PROVENANCE.md](docs/PROVENANCE.md) | Source revision and packaging changes |

The executable names `rsync-router` and `rsync-node-dev` are retained from the development codebase; they are not the Unix `rsync` utility.

## License

[MIT](LICENSE). Redis support can use independently licensed hiredis and redis-plus-plus dependencies; they are not vendored in this source package.
