# Repository scope

The repository root is the NetworkCache experiment-line implementation from
xiaoxin-ubuntu `/root/zzh/rsync`, branch `experiments/finite-cache-v1`,
commit `1b8b9753`. It is the source line used for the paper's cache,
capacity, concurrency, and event-tracing work.

`release/2026-10-21/` preserves the earlier curated public source package
byte-for-byte. That package is useful as a smaller functional baseline, but
it does not contain the experiment line's finite-cache implementation,
event tracing, probe control, or expanded tests.

`archive/snapshots/` contains the additional code snapshots from vc, vc-1,
vc-2, and simple-station. `archive/patches/` contains uncommitted source
changes recovered from those working copies. Results, raw traces, generated
tables, plots, build products, and the manuscript are excluded.

Start with the root `README.md` for the main implementation. Use
`archive/inventory/SOURCES.md` when tracing an evaluation revision back to
its source host and commit.

The experiment archive has its own boundary check:

```sh
cd archive
python3 inventory/verify_archive.py
```

