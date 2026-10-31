# Experiments

This directory keeps paper-oriented experiment plans, drivers, analysis scripts, and small reproducibility artifacts separate from the core runtime implementation.

## Current Paper Focus

The active paper track is **content discovery**, but the immediate completion target is narrower:

1. Treat `content_discovery/delay_find_content/` Docker results as the paper-facing main result.
2. Keep stable CSV data and draft vector figures from packaged Docker runs as the source of truth.
3. Keep Kathara as an optional reproducibility backend only; it is not required for the quantitative result.
4. Move next to bandwidth/streaming delay once the delay-find-content checkpoint is committed.

This phase remains **non-invasive**: experiment code must not modify `src/`, change REQUEST/DATA wire formats, add internal tracing, or alter QueryTable/SyncTable/cache semantics.

## Content Discovery Track

### Active: Delay Find Content

`content_discovery/delay_find_content/` answers the first paper questions:

- Does the current protocol discover and return content in packaged containers?
- How does discovery cost scale with provider distance?
- Does path materialization reduce the cost of later requests for the same key?
- Are the trends reproducible across repeated packaged Docker runs?

The expected paper artifacts are:

- Docker batch data with enough repetitions for stable medians and tail statistics.
- Curated CSVs derived from `aggregate.csv` and `comparison.csv`.
- Draft figures for hop-distance scaling and path-materialization warm behavior.
- Optional Kathara parity notes when a host with full Docker plugin support is available.

### Next: Bandwidth / Streaming Delay

`content_discovery/bandwidth_streaming_delay/` is the next content-discovery metric group. It can start after the current `delay_find_content` Docker checkpoint is committed.

The first bandwidth version should also stay non-invasive: use whole-object transfer under Docker bandwidth constraints and measure `provider_hit -> app_receive`. True chunked streaming is deferred until we explicitly decide to change protocol behavior.

## Deferred Cache-Management Track

Cache-management experiments are important but not part of the immediate paper push:

- aging method
- LRU
- cache miss behavior
- one-element removal
- application workload / application setting
- AI learning

These topics are likely to require explicit cache-policy control or new workload generation, so they should be planned after the content-discovery evidence is in place.

## Output Policy

Generated run outputs live under per-experiment `results/`, `logs/`, or `traces/` directories and are ignored by git by default. Commit only small curated samples, final CSVs, or paper figures when they are intentionally part of the record.
