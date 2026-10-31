# Source index

## Main line

| Directory | Host | Branch | Commit | Dirty |
|---|---|---|---|---|
| `mainline/` | xiaoxin-ubuntu | `experiments/finite-cache-v1` | `1b8b9753` | 75 |

The 74 non-paper working-tree changes on this host are experiment/build
artifacts. The remaining change is the `vldb/VLDB-networkcache` submodule
pointer, which is excluded from this code archive.

## vc snapshots

`vc` and `vc-1` contain the same detached `2abc060` snapshot. Their code
patches are byte-identical and are retained under separate host labels for
provenance.

## vc-2 snapshots

| Directory | Branch | Commit |
|---|---|---|
| `snapshots/vc-2/concurrency-pilot-v1/` | `codex/single-cluster-concurrency-pilot-v1` | `8c620fb` |
| `snapshots/vc-2/concurrency-pilot-v2/` | `codex/single-cluster-concurrency-pilot-v2` | `d2a4d6f` |
| `snapshots/vc-2/concurrency-pilot-v3/` | `codex/single-cluster-concurrency-pilot-v3` | `69987c6` |
| `snapshots/vc-2/concurrency-pilot-v4/` | `codex/single-cluster-concurrency-pilot-v4` | `28bfc10` |
| `snapshots/vc-2/concurrency-pilot-v5/` | `codex/single-cluster-concurrency-pilot-v5` | `78617f7` |
| `snapshots/vc-2/concurrency-pilot-v6/` | `codex/single-cluster-concurrency-pilot-v6` | `60dccec` |
| `snapshots/vc-2/concurrency-pilot-v7/` | `codex/single-cluster-concurrency-pilot-v7` | `d0cb71f` |
| `snapshots/vc-2/concurrency-pilot-v8/` | `codex/single-cluster-concurrency-pilot-v8` | `b895c17` |
| `snapshots/vc-2/concurrency-pilot-v9/` | `codex/single-cluster-concurrency-pilot-v9` | `6a14e6d` |
| `snapshots/vc-2/concurrency-slides-v1/` | `codex/concurrency-slides-v1` | `d4beb04` |
| `snapshots/vc-2/concurrency-slides-v2/` | `codex/concurrency-slides-v2` | `25dadf2` |
| `snapshots/vc-2/concurrency-slides-v3/` | `codex/concurrency-slides-v3` | `4951e84` |
| `snapshots/vc-2/concurrency-slides-v4/` | `codex/concurrency-slides-v4` | `e719f74` |
| `snapshots/vc-2/concurrency-slides-v5/` | `codex/concurrency-slides-v5` | `68fd224` |
| `snapshots/vc-2/concurrency-supplement-v1/` | `codex/concurrency-supplement-v1` | `d7aeb72` |
| `snapshots/vc-2/concurrency-supplement-v2/` | `codex/concurrency-supplement-v1` | `d7aeb72` |
| `snapshots/vc-2/concurrency-pre48-v2/` | `codex/concurrency-supplement-v1` | `3385412` |
| `snapshots/vc-2/concurrency-pre64post96-v2/` | `codex/concurrency-supplement-v1` | `0e1526c` |

## simple-station snapshots

These are a separate history from the same-named vc-2 projects.

| Directory | Branch | Commit |
|---|---|---|
| `snapshots/simple-station/rsync/` | detached | `2abc060` |
| `snapshots/simple-station/concurrency-slides-v5/` | `simple-station/concurrency-slides-v5` | `0e56414` |
| `snapshots/simple-station/concurrency-capacity-v1/` | `simple-station/concurrency-capacity-v1` | `ac2fc77` |
| `snapshots/simple-station/concurrency-supplement-v1/` | `simple-station/concurrency-supplement-v1` | `02b3378` |
| `snapshots/simple-station/concurrency-pre48-v1/` | `simple-station/concurrency-supplement-v1` | `ba46492` |
| `snapshots/simple-station/concurrency-pre64post96-v1/` | `simple-station/concurrency-supplement-v1` | `36f315c` |
| `snapshots/simple-station/pilot-bootstrap/` | local bootstrap scripts and plugin config | not a Git working tree |
| `snapshots/simple-station/finite-cache-v1/a0469dea-experiments/` | snapshot experiment code | not a Git working tree |

## Uncommitted code

- `patches/vc/networkcache-r2-tracked.patch`: 10 tracked source files.
- `patches/vc-1/networkcache-r2-tracked.patch`: same content as the vc patch.
- `patches/simple-station/rsync-tracked.patch`: 13 tracked source files.

The vc-2 and simple-station pre64post96 changes are workload JSON/TSV data.
They are excluded by design and recorded in `archive-manifest.json`.

