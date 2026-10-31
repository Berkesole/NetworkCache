# Repository scope

The repository root is the curated, buildable NetworkCache source snapshot
published at `github.com/Berkesole/NetworkCache`. It is intentionally kept
byte-for-byte compatible with that release for the C++ implementation,
tests, configuration, and package documentation.

`archive/` contains the wider code recovery assembled for the VLDB vision
paper. It holds the experiment-line snapshots from xiaoxin, vc, vc-1, vc-2,
and simple-station, plus uncommitted source patches and a provenance
manifest. Results, raw traces, generated tables, plots, build products, and
the manuscript are excluded.

Start with the root `README.md` for the buildable implementation. Use
`archive/README.md` and `archive/inventory/SOURCES.md` when tracing an
evaluation revision back to its source host and commit.

The experiment archive has its own boundary check:

```sh
cd archive
python3 inventory/verify_archive.py
```

