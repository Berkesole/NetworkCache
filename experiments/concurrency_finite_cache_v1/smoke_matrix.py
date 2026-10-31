#!/usr/bin/env python3
"""Frozen six-slot finite-cache smoke matrix (round-5).

`r32`/`r128` are ARRIVAL RATES in requests/second for the open-arrival (Poisson)
process, not concurrency caps -- see experiments/concurrency_slides_v5/matrix.py
(`rate` in conditions(); `per_node_inflight`/`concurrency_cap` are None).

The matrix is frozen: six unique slots, fixed order, one run per slot. The
versioned method configuration below declares, per method, which gates are
required and which evidence kinds MAY be absent (TCP Direct runs no Node cache,
so it must not be asked for cache_config/Redis monitoring, and must not be given
fabricated cache evidence).

Usage:
  smoke_matrix.py slots
  smoke_matrix.py manifest --source-sha SHA --image-id ID --tool-sha SHA \
      --config-sha SHA --workload-sha SHA --out-dir DIR --out manifest.json
  smoke_matrix.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "finite-cache-smoke-matrix"
VERSION = 1

# Shared conditions, taken from the frozen concurrency smoke design
# (pilot.environment / pilot.timeout_policy / matrix.slot).
COMMON = {
    "hosts": 16,
    "object_size": 65536,
    "catalog": 512,
    "distribution": "poisson",
    "seed": 1,
    "repetition": 1,
    "arrival_seed": 9172 + 1,
    "loop_sleep_ms": 25,
    "query_lifetime_seconds": 30,
    "wire_version": 3,
    "bloom_bits": 1024,
    "bloom_hash_count": 3,
    "staged_fallback": 1,
    "fallback_delay_ms": 1500,
    "link_delay_ms": 2,
    "link_rate_mbit": 1000,
    "link_queue_packets": 1000,
    "link_mtu": 1500,
    "event_trace": 1,
    "event_trace_flush_every": 1,
    "redis_maxmemory": 0,
    "redis_maxmemory_policy": "noeviction",
    "redis_require_empty": 1,
    "redis_timeout_ms": 1000,
}

ARRIVAL_INTERVAL_NS = {32: 31_250_000, 128: 7_812_500}

# Versioned method configuration. `required_gates` must all pass; `optional_gate`
# may be absent and then must NOT be reported as a failure; `forbidden_evidence`
# must never be fabricated or demanded.
METHOD_CONFIG = {
    "nc-bypass0": {
        "policy": "bypass",
        "budget_bytes": 0,
        "runs_node_cache": True,
        "required_gates": [
            "requests_complete", "content_hash", "exactly_once_delivery",
            "cache_config", "replica_admit_eq_0", "replica_evict_eq_0",
            "resident_objects_eq_0", "resident_bytes_eq_0",
            "redis_monitor_all_instances", "no_fatal", "no_backend_error",
        ],
        "optional_gate": None,
        "forbidden_evidence": [],
    },
    "nc-lru1m": {
        "policy": "lru",
        "budget_bytes": 1_048_576,
        "runs_node_cache": True,
        "required_gates": [
            "requests_complete", "content_hash", "exactly_once_delivery",
            "cache_config", "resident_bytes_within_budget",
            "replica_evict_gt_0", "owner_not_evicted",
            "per_node_accounting_closed", "no_duplicate_billing",
            "redis_monitor_all_instances", "no_fatal", "no_backend_error",
        ],
        "optional_gate": None,
        "forbidden_evidence": [],
    },
    "tcp-direct": {
        "policy": None,
        "budget_bytes": 0,
        "runs_node_cache": False,
        "required_gates": [
            "requests_complete", "content_hash", "exactly_once_delivery",
        ],
        "optional_gate": "tcp_evidence",
        "forbidden_evidence": ["cache_config", "redis_monitor", "cache_events"],
    },
}


def _slots() -> list[dict]:
    out = []
    for rate in (32, 128):
        for method in ("nc-bypass0", "nc-lru1m", "tcp-direct"):
            slot_id = (f"finite-smoke-n16-b65536-c512-poisson-r{rate}-{method}"
                       f"--s001-r1")
            out.append({
                "slot_index": len(out) + 1,
                "id": slot_id,
                "rate": rate,
                "arrival_interval_ns": ARRIVAL_INTERVAL_NS[rate],
                "method": method,
                "method_config": METHOD_CONFIG[method],
                "bash_cache_policy": METHOD_CONFIG[method]["policy"],
                "cache_budget_bytes": METHOD_CONFIG[method]["budget_bytes"],
                **COMMON,
            })
    return out


def slots() -> list[dict]:
    result = _slots()
    assert len(result) == 6
    assert len({s["id"] for s in result}) == 6
    assert [s["slot_index"] for s in result] == [1, 2, 3, 4, 5, 6]
    return result


def pre_manifest(source_sha: str, image_id: str, tool_sha: str,
                 config_sha: str, workload_sha: str, out_dir: str) -> dict:
    """Build the run-before manifest. Source, image and mounted tool hashes are
    recorded SEPARATELY -- a directory name is not a source version."""
    matrix = slots()
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "source_sha256": source_sha,
        "image_id": image_id,
        "mounted_tool_sha256": tool_sha,
        "smoke_config_sha256": config_sha,
        "workload_sha256": workload_sha,
        "output_dir": out_dir,
        "expected_nodes": list(range(1, COMMON["hosts"] + 1)),
        "monitor_policy": {
            "scope": "every distinct Redis instance (one per NC node)",
            "separate_series_per_instance": True,
            "interval_ms": 200,
            "max_gap_ms": 600,
            "baseline_evicted_keys": 0,
        },
        "acceptance_version": VERSION,
        "fixed_order": [s["id"] for s in matrix],
        "slots": matrix,
    }


def _self_test() -> int:
    failures = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"{name}: {'PASS' if condition else 'FAIL'}{' ' + detail if detail and not condition else ''}")
        if not condition:
            failures += 1

    s = slots()
    check("six_slots", len(s) == 6)
    check("frozen_order", [x["rate"] for x in s] == [32, 32, 32, 128, 128, 128])
    check("methods_per_rate",
          [x["method"] for x in s[:3]] == ["nc-bypass0", "nc-lru1m", "tcp-direct"])
    check("r32_is_arrival_rate",
          s[0]["rate"] == 32 and s[0]["arrival_interval_ns"] == 31_250_000)
    check("r128_interval", s[3]["arrival_interval_ns"] == 7_812_500)
    check("bypass_zero_budget", s[0]["cache_budget_bytes"] == 0 and
          s[0]["bash_cache_policy"] == "bypass")
    check("lru_one_mib", s[1]["cache_budget_bytes"] == 1_048_576 and
          s[1]["bash_cache_policy"] == "lru")
    check("tcp_direct_has_no_cache_policy", s[2]["bash_cache_policy"] is None)
    check("tcp_direct_does_not_demand_cache_evidence",
          "cache_config" not in METHOD_CONFIG["tcp-direct"]["required_gates"] and
          "redis_monitor" not in METHOD_CONFIG["tcp-direct"]["required_gates"])
    check("tcp_direct_forbids_fabricated_cache_evidence",
          set(METHOD_CONFIG["tcp-direct"]["forbidden_evidence"]) ==
          {"cache_config", "redis_monitor", "cache_events"})
    check("nc_gates_not_bypassable",
          "resident_bytes_within_budget" in METHOD_CONFIG["nc-lru1m"]["required_gates"] and
          "replica_evict_gt_0" in METHOD_CONFIG["nc-lru1m"]["required_gates"])
    check("common_conditions", s[0]["hosts"] == 16 and s[0]["object_size"] == 65536 and
          s[0]["catalog"] == 512 and s[0]["event_trace"] == 1)
    check("r32_and_r128_share_plan_shape",
          all(x["hosts"] == 16 and x["catalog"] == 512 and x["distribution"] == "poisson"
              for x in s))
    m = pre_manifest("a" * 64, "sha256:" + "b" * 64, "c" * 64, "d" * 64, "e" * 64,
                     "/tmp/smoke")
    check("manifest_separates_versions",
          m["source_sha256"] != m["image_id"] and
          m["source_sha256"] != m["mounted_tool_sha256"] and
          m["image_id"] != m["mounted_tool_sha256"])
    check("manifest_expected_nodes", m["expected_nodes"] == list(range(1, 17)))
    check("manifest_fixed_order_len", len(m["fixed_order"]) == 6)
    check("manifest_monitor_is_per_instance",
          m["monitor_policy"]["separate_series_per_instance"] is True)
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", nargs="?", choices=["slots", "manifest"])
    parser.add_argument("--source-sha", default="")
    parser.add_argument("--image-id", default="")
    parser.add_argument("--tool-sha", default="")
    parser.add_argument("--config-sha", default="")
    parser.add_argument("--workload-sha", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--out", default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.action == "slots":
        print(json.dumps(slots(), indent=2))
        return 0
    if args.action == "manifest":
        if not all((args.source_sha, args.image_id, args.tool_sha,
                    args.config_sha, args.workload_sha, args.out_dir)):
            parser.error("manifest requires --source-sha --image-id --tool-sha "
                         "--config-sha --workload-sha --out-dir")
        manifest = pre_manifest(args.source_sha, args.image_id, args.tool_sha,
                                args.config_sha, args.workload_sha, args.out_dir)
        text = json.dumps(manifest, indent=2) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0
    parser.error("choose slots, manifest or --self-test")
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))