# Source provenance

- Source commit: `341b90d8f2a16312fa046f7965d9c0f1313eec2f`.
- Packaging date: 2026-10-21.
- License: the original [MIT license](../LICENSE), retained unchanged.

All C++ implementation and test files are copied byte-for-byte from the source snapshot. Executable names and the `RSYNC_*` environment variable interface are retained for compatibility. The source repository's Git history and configuration are not included in the copied material.

## Packaging changes

- Replace development READMEs and internal design notes with an implementation guide and executable reproduction instructions.
- Rename the CMake project to `NetworkCache`; retain its original executable targets. Register functional checks with CTest and keep core assertions enabled for GNU/Clang.
- Add a Python standard-library runner that verifies core assertions, two mock scenarios, and three-process TCP delivery.
- Replace the Makefile wrapper with an offline default test path and a separate optional Redis build directory.
- Correct Docker build context resolution and provide a disposable container test command. Include Python and a Debug build in the image; refresh obsolete runtime comments.
- Remove whole-line narrative comments from example YAML files, including outdated expectations; retain their configuration values unchanged.
- Add ignore rules for generated output and private development material.

## Excluded material

The export intentionally excludes manuscript sources, PDFs, bibliographies, meeting transcripts, research notes, author/contact metadata from old READMEs, credentials, local Git history, build products, logs, caches, and downloaded browser/dependency trees.

Older phase-by-phase acceptance wrappers are omitted because some assert log messages that no longer match this implementation. Their deployment configurations and Kathara launch helpers are retained; the new local runner is the documented functional verification entry point. Obsolete internal documentation about inactive SYNC behavior is also omitted.

This packaging record does not identify the snapshot as the exact source revision of every performance experiment. The [reproducibility scope](REPRODUCIBILITY.md#relationship-to-performance-results) distinguishes the available implementation from missing external experiment material.

[SHA256SUMS](../SHA256SUMS) covers the exported files except itself and local Git metadata. From the repository root, `sha256sum -c SHA256SUMS` checks the packaged snapshot; regenerate the manifest after making deliberate changes.
