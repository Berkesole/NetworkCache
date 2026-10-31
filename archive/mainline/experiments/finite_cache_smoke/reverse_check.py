#!/usr/bin/env python3
"""Reverse verification: the OLD acceptance module (commit 9e0c2d69) let five
known-bad inputs PASS. This loads that module from git and runs it on fixtures
that the NEW module (smoke_accept.py) rejects, so the new negative tests are
shown to target real defects rather than invented ones.

Usage: reverse_check.py [--old-commit SHA]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OLD_PATH = "experiments/finite_cache_smoke/smoke_accept.py"


def load_old(commit: str):
    source = subprocess.check_output(
        ["git", "-C", str(REPO), "show", f"{commit}:{OLD_PATH}"], text=True)
    tmp = Path(tempfile.mkdtemp(prefix="old-accept-"))
    target = tmp / "old_smoke_accept.py"
    target.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("old_smoke_accept", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules["old_smoke_accept"] = module
    spec.loader.exec_module(module)
    return module, source


def fixtures():
    """Fixtures for the OLD signature (planned_indexes/completions/...)."""
    h = "0123456789abcdef"
    planned = {"1"}
    base_trace = {1: [{"event": "cache_config", "budget_mode": "lru",
                       "budget_bytes": "1048576"},
                      {"event": "replica_admit", "key": "r1", "value_bytes": "1",
                       "resident_bytes": "1", "resident_objects": "1"},
                      {"event": "replica_evict", "key": "r1", "value_bytes": "1",
                       "resident_bytes": "0", "resident_objects": "0"}]}
    monitored = [{"node": 1, "status": "ok", "used_memory": "100",
                  "evicted_keys": "0", "redis_run_id": "r", "ts_epoch_ns": "1000"},
                 {"node": 1, "status": "ok", "used_memory": "100",
                  "evicted_keys": "0", "redis_run_id": "r", "ts_epoch_ns": "200000000"}]
    common = dict(planned_indexes=planned, completions=planned,
                  audit_indexes=planned, audit_ok=True, trace_problems=[],
                  control=Path("/nonexistent"), expected_policy="lru",
                  expected_budget=1048576, hosts=1)
    return {
        # TCP Direct with NO transport evidence at all.
        "tcp_no_transport": dict(common, transport="tcp-direct", trace_files={},
                                 monitor_records=[], monitor_exit={}),
        # monitor exit code missing for the only instance.
        "monitor_exit_missing": dict(common, transport="nc", trace_files=base_trace,
                                     monitor_records=monitored, monitor_exit={}),
        # monitor timestamp garbage.
        "monitor_garbage_time": dict(
            common, transport="nc", trace_files=base_trace,
            monitor_records=[dict(monitored[0], ts_epoch_ns="garbage"),
                             dict(monitored[1], ts_epoch_ns="garbage")],
            monitor_exit={1: 0}),
        # wrong final gauge (resident bytes do not match the replay).
        "wrong_final_gauge": dict(
            common, transport="nc",
            trace_files={1: base_trace[1] + [{"event": "cache_gauge",
                                              "resident_bytes": "999",
                                              "resident_objects": "9"}]},
            monitor_records=monitored, monitor_exit={1: 0}),
        # missing eviction snapshot (resident_bytes absent on the evict event).
        "missing_evict_snapshot": dict(
            common, transport="nc",
            trace_files={1: [base_trace[1][0], base_trace[1][1],
                             {k: v for k, v in base_trace[1][2].items()
                              if k not in ("resident_bytes", "resident_objects")}]},
            monitor_records=monitored, monitor_exit={1: 0}),
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old-commit", default="9e0c2d69")
    args = ap.parse_args(argv)
    module, source = load_old(args.old_commit)

    results = {}
    for name, kwargs in fixtures().items():
        try:
            verdict = module.accept_slot(**kwargs)
            results[name] = bool(verdict["qualified"])
        except Exception as exc:  # a crash is also not a rejection we can trust
            results[name] = f"error:{exc!r}"
    print(json.dumps({"old_commit": args.old_commit,
                      "old_qualified": results}, indent=2))

    expected_pass = set(fixtures())
    wrongly_passed = [n for n, v in results.items() if v is True]
    print("old_wrongly_passed:", sorted(wrongly_passed))
    print("reproduced_all_five:", sorted(wrongly_passed) == sorted(expected_pass))
    return 0 if sorted(wrongly_passed) == sorted(expected_pass) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))