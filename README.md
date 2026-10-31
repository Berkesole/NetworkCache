# NetworkCache source archive

Single-branch archive of the NetworkCache prototype and the experiment code
that produced the VLDB vision-paper evaluation. This repository contains
source code, build configuration, experiment drivers, and small manifests.
It intentionally excludes experiment results, raw traces, generated tables,
plots, build output, and the paper sources.

The branch is `main`. No remote is configured. The goal is a self-contained,
locally reviewable snapshot before choosing where the archive should live.

## Layout

- `mainline/` is the code from xiaoxin-ubuntu `/root/zzh/rsync`, branch
  `experiments/finite-cache-v1`, commit `1b8b9753`. Only Git-tracked
  non-paper source and configuration were exported.
- `snapshots/<host>/<project>/` holds code snapshots from the experiment
  hosts. Their original Git metadata is represented in
  `archive-manifest.json`; `.git` directories are not nested here.
- `patches/<host>/` holds tracked, uncommitted source changes recovered from
  the working copies. Runtime-generated modifications are not included.
- `inventory/` records provenance, source branches, commit IDs, and
  exclusions so the history remains reviewable without requiring the remote
  machines.

## Scope

This archive is for the paper submission. Paper files such as the manuscript,
figures, and bibliography are deliberately outside this repository. The
existing paper repository is not modified by this archive.

The included experiment code is enough to review and rerun the methodology.
The data results themselves remain on the experiment hosts and in the
separate paper/data workspace.

## Counts

The manifest describes imported source trees only. The checked-out repository
also contains this README, the archive notes, the manifest, and the small
import/verification tools under `inventory/`.

Run `python3 inventory/verify_archive.py` from this directory to verify source
hashes, forbidden payload paths, and the single-branch archive layout.

