# Archive decisions

## Source of truth

The only repository with the full development history is xiaoxin-ubuntu
`/root/zzh/rsync`, currently on `experiments/finite-cache-v1` at
`1b8b9753`. Its GitHub origin is recorded in the manifest as provenance,
but this local archive does not set a remote.

The experiment hosts contain locally cloned or copied snapshots. Identically
named projects on different hosts are distinct histories. They are preserved
under host-specific directories rather than merged or silently deduplicated.

## Data boundary

The archive is code-only. The following are excluded:

- `experiments/content_discovery/**/results/`
- raw workloads, traces, CSV/TSV/JSONL measurements, generated plots, and PDFs
- `raw/`, `results/`, `output/`, `deliveries/`, and per-slot evidence folders
- `build*/`, CMake caches, object files, binaries, and vendored dependencies
- the `vldb/` paper submodule and every paper source file

Small JSON, YAML, and text files are retained when they are configuration,
manifests, interface descriptions, or experiment inputs. Large or
measurement-shaped JSON is omitted. The manifest lists the exact filters and
the recovery locations for excluded material.

## Uncommitted changes

Only tracked working-tree changes that touch source-like files are exported
as patches. Uncommitted runtime output is not committed. The manifest records
the original dirty-file count separately so the archive does not imply that
every runtime artifact was part of the code.

## History

The new local archive has one branch, `main`. Experimental provenance is
encoded as directories and commit IDs in `archive-manifest.json`. This keeps
the submission repository simple while preserving enough information to trace
every imported snapshot back to its original host and commit.

