# NetworkCache Sync Protocol (rsync)

> A C++20 prototype for cache-to-cache synchronization of named datacenter objects.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
![C++20](https://img.shields.io/badge/C++-20-blue.svg)
![Status](https://img.shields.io/badge/Status-Research_Prototype-orange)

---

## Overview

`rsync` is the implementation prototype for **NetworkCache**, a miss-path
synchronization substrate for distributed caches. Applications keep familiar
local-cache semantics: a local hit returns immediately, while a miss is handled
by a nearby NetworkCache node instead of by application-specific sharding,
directory lookup, retry, and cache-fill logic.

The prototype treats object names, keys, or versioned identifiers as stable
distributed handles. On a miss, nodes propagate Bloom-filter summaries of
pending demand, aggregate overlapping requests on shared egress paths, learn
soft location hints from returned data, and materialize objects along the return
path. The design targets commodity Ethernet and "intelligent end-hosts +
simple fabric" deployments rather than requiring lossless RDMA-style networks.

This repository is still a research prototype: it is useful for validating the
protocol mechanics and running controlled scenarios, not as a production cache
service.

---

## Design Goals

### 1. Decouple identity from placement

NetworkCache separates:

- **Object identity**: named keys represented in REQUEST packets by Bloom filters.
- **Pending demand**: QueryTable entries that remember which downstream nodes may
  still be waiting for matching objects.
- **Location hints**: SyncTable entries learned from returned DATA packets.
- **Transient aggregation**: UnionWindow state used only to reduce short-term
  forwarding pressure.

This keeps the cache API centered on object names while allowing placement to be
learned and optimized by the synchronization layer.

### 2. Make cache misses shared synchronization events

A miss is not treated as an isolated remote fetch. It can reveal useful system
state:

- Multiple nodes may be waiting for overlapping objects.
- Returned data can satisfy local demand and downstream demand.
- The return path can teach nodes where a copy may exist next time.
- Caching returned data also acts as an implicit duplicate filter.

### 3. Prefer bounded redundancy over rigid reverse paths

The current protocol allows opportunistic multipath retrieval, but bounds
amplification with:

- finite `hop_count` / `max_hop_count`,
- previous-hop exclusion during forwarding,
- single-layer REQUEST aggregation,
- short-lived QueryTable entries,
- cache-based suppression of duplicate DATA fan-out.

The goal is not perfect duplicate elimination. The goal is convergence with
useful path diversity and lower repeated miss cost.

---

## Current Architecture

```
Node
├── NetworkInterface
│   ├── MockNetwork     # in-process topology simulation
│   └── TcpNetwork      # length-prefixed TCP packet transport
├── LocalCache
│   ├── InMemoryCache   # default, dependency-free backend
│   └── RedisCache      # optional Redis backend via redis-plus-plus
├── QueryTable          # original pending demand state
├── SyncTable           # learned neighbor/location hints
├── UnionWindow         # per-egress single-layer REQUEST aggregation
└── BloomFilter         # approximate object-name summaries
```

### Packet types

| Type | Purpose | Main payload |
|------|---------|--------------|
| `REQUEST` | Propagate demand for one or more keys | Bloom filter |
| `DATA` | Return a concrete key/value object | key + value |

`SYNC` is no longer part of the active packet path. Neighbor knowledge is
currently learned from DATA returns and local SyncTable updates; periodic BF
gossip remains future work.

### Runtime entrypoints

| Target | Purpose |
|--------|---------|
| `rsync-router` | Scenario-driven mock simulator, used by `make run` |
| `rsync-node-dev` | Per-node runtime with mock or TCP transport |
| `test_core` | Dependency-free unit-style checks for core data structures |
| `test_mesh_demo` | 8x8 mock mesh scenario |
| `test_redis_cache_contract` | Optional Redis cache contract tests |

---

## Implemented Today

- Core protocol state:
  - `Node`
  - `Packet`
  - `BloomFilter`
  - `QueryTable`
  - `SyncTable`
  - `UnionWindow`
- Request path:
  - local cache lookup before forwarding,
  - Bloom-filter REQUEST creation,
  - broadcast fallback on SyncTable miss,
  - SyncTable-directed unicast on learned hints,
  - previous-hop exclusion,
  - hop-count bound,
  - single-layer per-egress aggregation.
- Data path:
  - local cache insertion on first DATA arrival,
  - learned SyncTable hint from DATA source,
  - QueryTable-based downstream fan-out,
  - local application notification when the requester receives data,
  - cache-based duplicate suppression.
- Transports:
  - in-process `MockNetwork` for deterministic scenarios,
  - TCP transport with topology-derived or overridden endpoints.
- Cache backends:
  - dependency-free in-memory cache,
  - optional Redis cache over Unix socket.
- Scenario and validation support:
  - YAML-like topology/scenario parser,
  - 3-node default demo,
  - 8x8 mock mesh demo,
  - Docker bridge acceptance scripts,
  - Kathara scenario scripts.

---

## Still In Progress

- Event-driven runtime:
  - `processOne()` is still driven by outer loops and tick counts.
  - TCP polling is integrated into `receive()`, not a standalone reactor.
- Bloom filter engineering:
  - current BF uses one `std::hash` position per key,
  - no counting BF, generation management, saturation tracking, or configured
    false-positive target yet.
- SyncTable policy:
  - route lookup and learned hints exist,
  - LRU eviction, TTL cleanup integration, adaptive weights, and failure feedback
    are not fully wired into the active runtime.
- Evaluation:
  - current tests validate mechanics and smoke scenarios,
  - latency, amplification, redundancy, and scalability measurements are still
    paper/evaluation work.
- Producer-side semantics:
  - the prototype targets immutable, versioned, or epoch-named values,
  - strong coherence, write invalidation, allocation ownership, and free
    semantics are out of scope for the current consumer-side design.

---

## Build And Run

### Prerequisites

- GCC 10+ or Clang 12+ with C++20 support
- CMake 3.15+
- Make
- Optional: Docker, Kathara, Redis 7+, hiredis, redis-plus-plus

Redis support is disabled by default so the core prototype can build offline.

### Quick commands

```bash
make              # configure and build all default targets
make run          # build and run the default 3-node mock scenario
make test-core    # build and run dependency-free core tests
make run_mesh     # build and run the 8x8 mock mesh demo
make test-redis   # configure with Redis support and run Redis tests
make clean        # clean CMake target outputs
make distclean    # remove build/
```

### Manual CMake

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
./build/rsync-router
```

### Default mock scenario

The default topology is `config/3-node-topology.yaml`:

```
Node 1 <-> Node 2 <-> Node 3
```

Node 3 starts with `redis_key_a=VALUE_OF_A`, and Node 1 requests
`redis_key_a`. A successful run shows REQUEST forwarding through Node 2 and
DATA returning through Node 2 to Node 1.

```bash
make run
```

Expected key lines:

```text
[Node 1] Broadcast REQUEST for 1 key(s) (no SyncTable hit)
[Node 2] flush AGGREGATE to Node 3
[Node 3] Sending DATA for key=redis_key_a to Node 2
[Node 2] Forwarding DATA for key=redis_key_a to Node 1
[Node 1] Received DATA for key=redis_key_a
[App] Received data for key=redis_key_a, size=10, content="VALUE_OF_A"
```

### Per-node runtime

`rsync-node-dev` can be configured by CLI flags or environment variables.

```bash
./build/rsync-node-dev \
  --node-id 1 \
  --transport tcp \
  --topology config/manual/3-node-tcp-local.yaml \
  --iterations 100 \
  --sleep-ms 50
```

Common options:

- `--node-id <id>`
- `--transport <mock|tcp>`
- `--topology <path>`
- `--iterations <count>`
- `--sleep-ms <milliseconds>`
- `--seed <k=v,k=v>`
- `--request-keys <k1,k2>`
- `--tcp-self <host:port>`
- `--tcp-peer <id=host:port>`

Equivalent environment variables include `RSYNC_NODE_ID`,
`RSYNC_TRANSPORT`, `RSYNC_TOPOLOGY_CONFIG`, `RSYNC_LOOP_ITERATIONS`,
`RSYNC_LOOP_SLEEP_MS`, `RSYNC_SEED_DATA`, `RSYNC_REQUEST_KEYS`,
`RSYNC_TCP_SELF`, and `RSYNC_TCP_PEERS`.

For hands-on Docker, TCP, and Kathara instructions, see
[`docs/manual-runtime-guide.zh-CN.md`](./docs/manual-runtime-guide.zh-CN.md).

---

## Redis Support

Redis support is optional at build time:

```bash
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_REDIS=ON \
  -DFETCH_REDIS_DEPS=ON
cmake --build build -j
```

`RedisCache` connects over a Unix socket. The default path is
`/run/redis/redis.sock`; override it with `REDIS_SOCKET_PATH`.

```bash
REDIS_SOCKET_PATH=/tmp/redis.sock ./build/rsync-node-dev ...
```

The Redis test suite can be run with:

```bash
make test-redis
```

When using fetched shared libraries, you may need:

```bash
LD_LIBRARY_PATH=build/_deps/hiredis-build:build/_deps/redis_plus_plus-build make test-redis
```

---

## Project Layout

```
rsync/
├── CMakeLists.txt
├── Makefile
├── main.cpp                         # rsync-router mock scenario entrypoint
├── app/
│   └── rsync_node_main.cpp          # rsync-node-dev runtime entrypoint
├── src/
│   ├── node.hpp / node.cpp          # protocol state machine
│   ├── packet.hpp / packet.cpp      # REQUEST/DATA packet model
│   ├── bloomfilter.hpp              # approximate key summary
│   ├── query_table.hpp              # pending demand table
│   ├── sync_table.hpp               # learned location hints
│   ├── union_window.hpp             # request aggregation window
│   ├── cache/                       # LocalCache backends
│   └── network/                     # mock/TCP transport and topology parser
├── config/
│   ├── *.yaml                       # mock scenarios
│   ├── manual/                      # manual runtime examples
│   └── acceptance/                  # bridge/Kathara acceptance inputs
├── tests/
│   ├── test_core.cpp
│   ├── mesh.cpp
│   └── redis/
├── scripts/
│   ├── acceptance/
│   ├── kathara/
│   └── tests/
├── docker/
├── kathara/
└── docs/
```

---

## Documentation

- [`docs/manual-runtime-guide.zh-CN.md`](./docs/manual-runtime-guide.zh-CN.md):
  manual Docker, TCP, topology, and Kathara guide.
- [`docs/cache-architecture.md`](./docs/cache-architecture.md):
  cache interface and backend relationship.
- [`docs/src-structure.md`](./docs/src-structure.md):
  intended source-layout view.
- [`docs/interfaces/`](./docs/interfaces/):
  interface notes for cache, node, network, table, and global types.
- [`src/cache/README.md`](./src/cache/README.md):
  cache module details.
- [`src/network/MIGRATION.md`](./src/network/MIGRATION.md):
  notes around network-layer migration.

---

## Development Roadmap

Near-term engineering work:

- replace outer polling loops with an event-driven runtime,
- improve BloomFilter hashing and saturation controls,
- wire SyncTable cleanup, route quality, and failure feedback into runtime paths,
- expand packet codec coverage and malformed-frame tests,
- add protocol-level tests for aggregation, duplicate suppression, and learned
  warm retrieval,
- collect evaluation data for cold retrieval, warm retrieval, aggregation,
  redundancy versus delay, and dense-topology amplification.

Longer-term research work:

- producer-side naming and allocation semantics,
- stronger freshness/coherence models for mutable objects,
- large-scale topology experiments,
- comparison against application-level sharding, directory lookup, and RDMA-like
  deployment assumptions.

---

## Contributing

1. Fork the repository.
2. Create a feature branch.
3. Keep commits focused and include enough context in commit messages.
4. Run the relevant local tests or smoke scripts.
5. Open a pull request.

Code style:

- Language: C++20.
- Prefer existing project patterns over new abstractions.
- Keep protocol comments precise; identifiers should stay in English.
- Do not enable Redis or network dependency downloads unless the task requires it.

---

## License

MIT License. See [LICENSE](./LICENSE) for details.

Copyright (c) 2025 Network Cache Research Project

---

**Last updated**: 2026-06-25

**Project status**: Research prototype with mock, TCP, in-memory, and optional Redis paths

**Code name**: `rsync`; paper/system name: `NetworkCache`
