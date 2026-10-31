#!/usr/bin/env python3
"""Reverse verification for the round-7 fixes.

Loads the PREVIOUS acceptance module from git and shows it returned `qualified`
for six known-bad inputs; the current module rejects the equivalents.

Cases: NC window=None; missing owner set; wrong FINAL gauge objects; wrong MIDDLE
gauge; missing cache-operation timestamp; an unregistered completion file on disk.
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


def load_old(commit):
    source = subprocess.check_output(
        ["git", "-C", str(REPO), "show", f"{commit}:{OLD_PATH}"], text=True)
    tmp = Path(tempfile.mkdtemp(prefix="old-accept7-"))
    target = tmp / "old_smoke_accept.py"
    target.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("old_smoke_accept7", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules["old_smoke_accept7"] = module
    spec.loader.exec_module(module)
    return module


H = "0123456789abcdef"


def build_fixtures():
    tmp = Path(tempfile.mkdtemp(prefix="reverse7-"))
    comp = tmp / "completions"; comp.mkdir()
    audit = tmp / "delivery-audit"; audit.mkdir()
    row = "\t".join(["1", "1", "k1", "ok", "65536", H]) + "\n"
    (comp / "1.tsv").write_text(row, encoding="utf-8")
    (audit / "node1.tsv").write_text(row, encoding="utf-8")
    # an EXTRA, unregistered completion file on disk
    (comp / "999.tsv").write_text("\t".join(["999", "1", "k1", "ok", "65536", H]) + "\n",
                                  encoding="utf-8")
    planned = [{"index": "1", "node": 1, "requester": "1", "owner": "2", "key": "k1",
                "value_bytes": 65536, "expected_fnv1a64": H}]

    def traces(over=None):
        recs = [{"event": "cache_config", "budget_mode": "lru", "budget_bytes": "1048576"},
                {"event": "replica_admit", "key": "r1", "value_bytes": "65536",
                 "resident_bytes": "65536", "resident_objects": "1", "ts_mono_ns": 10},
                {"event": "cache_gauge", "resident_bytes": "65536",
                 "resident_objects": "1", "ts_mono_ns": 15},
                {"event": "replica_evict", "key": "r1", "value_bytes": "65536",
                 "resident_bytes": "0", "resident_objects": "0", "ts_mono_ns": 20},
                {"event": "cache_gauge", "resident_bytes": "0",
                 "resident_objects": "0", "ts_mono_ns": 30}]
        if over:
            recs = over(recs)
        return {1: recs}

    series = {1: [{"node": 1, "status": "ok", "ts_mono_ns": 5, "ts_epoch_ns": 1000,
                   "used_memory": "1", "evicted_keys": "0", "redis_run_id": "r"},
                  {"node": 1, "status": "ok", "ts_mono_ns": 40, "ts_epoch_ns": 2000,
                   "used_memory": "1", "evicted_keys": "0", "redis_run_id": "r"}]}
    common = dict(transport="nc", hosts=1, planned=planned,
                  completion_entries=[("1", comp / "1.tsv")],
                  audit_paths=[audit / "node1.tsv"], trace_problems=[],
                  tcp_trace_files={}, tcp_trace_problems=[], monitor_series=series,
                  monitor_exit={1: 0}, expected_policy="lru", expected_budget=1048576,
                  control=tmp / "nocontrol")

    def case_window_none():
        return dict(common, trace_files=traces(), window=None,
                    owner_keys_by_node={1: {"own1"}})

    def case_owner_missing():
        return dict(common, trace_files=traces(),
                    window={"setup_start_epoch_ns": 1000,
                            "last_completion_epoch_ns": 2000},
                    owner_keys_by_node={})

    def case_final_gauge_objects():
        def over(recs):
            recs = [dict(x) for x in recs]
            recs[-1]["resident_objects"] = "999"
            return recs
        return dict(common, trace_files=traces(over),
                    window={"setup_start_epoch_ns": 1000,
                            "last_completion_epoch_ns": 2000},
                    owner_keys_by_node={1: {"own1"}})

    def case_middle_gauge():
        def over(recs):
            # the round-6 module only ever read the LAST gauge and only its
            # resident_bytes, so a middle gauge with the wrong OBJECT count (and
            # correct bytes) was invisible to it.
            recs = [dict(x) for x in recs]
            recs[2]["resident_objects"] = "9"  # the MIDDLE cache_gauge
            return recs
        return dict(common, trace_files=traces(over),
                    window={"setup_start_epoch_ns": 1000,
                            "last_completion_epoch_ns": 2000},
                    owner_keys_by_node={1: {"own1"}})

    def case_op_missing_time():
        def over(recs):
            recs = [dict(x) for x in recs]
            del recs[3]["ts_mono_ns"]
            return recs
        return dict(common, trace_files=traces(over),
                    window={"setup_start_epoch_ns": 1000,
                            "last_completion_epoch_ns": 2000},
                    owner_keys_by_node={1: {"own1"}})

    def case_extra_completion():
        # the round-6 signature only ever received the planned path list
        return dict(common, trace_files=traces(),
                    window={"setup_start_epoch_ns": 1000,
                            "last_completion_epoch_ns": 2000},
                    owner_keys_by_node={1: {"own1"}})

    return {
        "nc_window_none": case_window_none,
        "owner_set_missing": case_owner_missing,
        "final_gauge_objects_wrong": case_final_gauge_objects,
        "middle_gauge_wrong": case_middle_gauge,
        "cache_op_timestamp_missing": case_op_missing_time,
        "unregistered_completion_on_disk": case_extra_completion,
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old-commit", default="edf08521")
    args = ap.parse_args(argv)
    module = load_old(args.old_commit)
    results = {}
    for name, builder in build_fixtures().items():
        try:
            verdict = module.accept_slot(**builder())
            results[name] = bool(verdict.get("qualified"))
        except Exception as exc:
            results[name] = f"error:{exc!r}"
    print(json.dumps({"old_commit": args.old_commit, "old_qualified": results}, indent=2))
    wrongly = sorted(n for n, v in results.items() if v is True)
    print("old_wrongly_passed:", wrongly)
    print("reproduced_all_six:", len(wrongly) == 6)
    return 0 if len(wrongly) == 6 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))