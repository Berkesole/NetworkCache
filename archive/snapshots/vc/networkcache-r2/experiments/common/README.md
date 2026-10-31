# Common Experiment Assets

Shared assets used by multiple experiment families.

## Suggested Contents

- `topologies/`: reusable line, tree, mesh, and bridge topologies.
- `scripts/`: shared helpers for running binaries, collecting logs, and validating outputs.
- `traces/`: shared trace schema examples and tiny sample traces.
- `analysis/`: shared notebooks or scripts for converting traces into CSVs and plots.

## Trace Direction

For content discovery, we probably want a structured event trace rather than parsing human-readable logs. A first-pass JSONL event could look like:

```json
{"ts_ns": 123, "node": 1, "event": "request_start", "request_id": 7, "key": "k1"}
{"ts_ns": 456, "node": 3, "event": "provider_hit", "request_id": 7, "key": "k1", "hop": 2}
{"ts_ns": 789, "node": 1, "event": "app_receive", "request_id": 7, "key": "k1"}
```

The exact schema is still open for discussion.
