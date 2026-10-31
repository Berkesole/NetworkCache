#!/usr/bin/env python3
"""Pilot metric extraction from the controller ledger and per-node traces.

Percentiles use nearest-rank on the completed sample; the throughput denominator
is the MEASUREMENT window length (never the whole run), and drain is reported
separately. Timeouts/not-completed requests are kept, not dropped. Cache hit
counts are reported as service-event classes; a request-level hit rate is only
emitted when every completed request can be attributed to exactly one provable
class.

Usage: pilot_metrics.py --self-test
"""

from __future__ import annotations

import argparse

ATTRIBUTED_VIA = ("local_cache", "provider", "owner", "remote")


def _pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def compute_metrics(ledger, plan, hosts):
    measurement = [r for r in ledger if r.get("phase") == "measurement"]
    setup = [r for r in ledger if r.get("phase") == "setup"]
    window = plan["measurement_window_ns"]
    window_ns = max(1, window["end"] - window["start"])
    base = min((int(r["planned_mono_ns"]) for r in measurement), default=0)
    w0, w1 = base + window["start"], base + window["end"]

    in_window = [r for r in measurement
                 if w0 <= int(r.get("completed_mono_ns") or 0) <= w1]
    completed = [r for r in measurement if r.get("status") == "ok"]
    failed = [r for r in measurement if r.get("status") != "ok"]
    latencies = [int(r["total_latency_ns"]) for r in completed
                 if r.get("total_latency_ns")]
    drain_ns = None
    if measurement:
        last_arrival = max(int(r["planned_mono_ns"]) for r in measurement)
        last_done = max((int(r["completed_mono_ns"]) for r in measurement
                         if r.get("completed_mono_ns")), default=None)
        if last_done is not None:
            drain_ns = max(0, last_done - last_arrival)

    via_classes = {}
    unattributed = 0
    for r in completed:
        via = r.get("via")
        if via:
            via_classes[via] = via_classes.get(via, 0) + 1
        else:
            unattributed += 1
    request_level_hit_rate = None
    if completed and not unattributed:
        request_level_hit_rate = round(
            via_classes.get("local_cache", 0) / len(completed), 4)

    released = [r for r in measurement if r.get("issued_mono_ns")]
    metrics = {
        "planned_requests": len(setup) + len(measurement),
        "planned_setup": len(setup),
        "planned_measurement": len(measurement),
        "released_measurement": len(released),
        "completed_measurement": len(completed),
        "failed_measurement": len(failed),
        "failed_status_breakdown": {s: sum(1 for r in failed if r.get("status") == s)
                                    for s in {r.get("status") for r in failed} if s},
        "success_rate": round(len(completed) / len(measurement), 4) if measurement else None,
        "offered_rate_per_s": plan["rate"],
        "actual_release_rate_per_s": round(len(released) / (window_ns / 1e9), 3)
        if window_ns else None,
        "window_completions": len(in_window),
        "window_seconds": round(window_ns / 1e9, 4),
        "window_throughput_per_s": round(len(in_window) / (window_ns / 1e9), 3)
        if window_ns else None,
        "drain_seconds_after_last_arrival": round(drain_ns / 1e9, 4)
        if drain_ns is not None else None,
        "latency_ns": {"p50": _pct(latencies, 0.50), "p95": _pct(latencies, 0.95),
                       "p99": _pct(latencies, 0.99), "n": len(latencies)},
        "latency_sample_note": ("sample too small for a stable p99"
                                if len(latencies) < 1000 else None),
        "scheduler_delay_ns_p95": _pct([int(r["scheduler_delay_ns"])
                                        for r in completed
                                        if r.get("scheduler_delay_ns")], 0.95),
        "execution_ns_p95": _pct([int(r["execution_ns"]) for r in completed
                                  if r.get("execution_ns")], 0.95),
        "service_event_classes": via_classes,
        "unattributed_completed": unattributed,
        "request_level_hit_rate": request_level_hit_rate,
        "hit_rate_note": ("request-level (every completed request attributed)"
                          if request_level_hit_rate is not None
                          else "service-event classes only; final provider not attributable"),
        "hosts": hosts,
    }
    return metrics


def cache_metrics(cache_summary, trace_files, metrics):
    per_node = {}
    totals = {"admits": 0, "evicts": 0, "bypasses": 0, "owner_served": 0,
              "local_cache_hit": 0}
    for node, recs in trace_files.items():
        counts = {"admits": 0, "evicts": 0, "bypasses": 0, "local_cache_hit": 0,
                  "provider_hit": 0, "fallbacks": 0, "owner_evict": 0}
        gauges = []
        for r in recs:
            e = r.get("event")
            if e == "replica_admit":
                counts["admits"] += 1
            elif e == "replica_evict":
                counts["evicts"] += 1
            elif e == "replica_bypass":
                counts["bypasses"] += 1
            elif e == "local_cache_hit":
                counts["local_cache_hit"] += 1
            elif e == "provider_hit":
                counts["provider_hit"] += 1
            elif e in ("immediate_dead_end_fallback", "origin_fallback_armed",
                       "fallback", "request_forward_start"):
                counts["fallbacks"] += 1
            elif e == "owner_evict":
                counts["owner_evict"] += 1
            elif e == "cache_gauge":
                gauges.append({"bytes": r.get("resident_bytes"),
                               "objects": r.get("resident_objects"),
                               "ts_mono_ns": r.get("ts_mono_ns")})
        counts["resident_bytes_final"] = cache_summary["final_bytes"].get(node)
        counts["gauge_samples"] = len(gauges)
        per_node[node] = counts
        for k in ("admits", "evicts", "bypasses"):
            totals[k] += counts[k]
        totals["local_cache_hit"] += counts["local_cache_hit"]
    completions = metrics["completed_measurement"] or 1
    window_s = metrics["window_seconds"] or 1
    return {
        "per_node": per_node,
        "totals": totals,
        "evictions_per_second": round(totals["evicts"] / window_s, 4),
        "evictions_per_1000_completions": round(totals["evicts"] / completions * 1000, 3),
        "competing_nodes": sorted(n for n, c in per_node.items() if c["evicts"] > 0),
        "quiet_nodes": sorted(n for n, c in per_node.items() if c["evicts"] == 0),
        "counting_rule": ("event classes are reported separately; provider_hit / "
                          "DATA / application deliveries are never summed together"),
    }


def _self_test() -> int:
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        print(f"{name}: {'PASS' if ok else 'FAIL'}{' ' + detail if detail and not ok else ''}")
        if not ok:
            failures += 1

    plan = {"rate": 128, "measurement_window_ns": {"start": 0, "end": 2_000_000_000}}
    ledger = []
    for i in range(10):
        planned = 100_000_000 + i * 100_000_000
        ledger.append({"phase": "setup", "status": "ok", "planned_mono_ns": planned,
                       "completed_mono_ns": planned + 1000, "total_latency_ns": 1000})
    for i in range(10):
        planned = 100_000_000 + i * 200_000_000
        ledger.append({"phase": "measurement", "status": "ok" if i < 9 else "io_error",
                       "planned_mono_ns": planned, "issued_mono_ns": planned + 10,
                       "completed_mono_ns": planned + 100_000_000,
                       "total_latency_ns": 100_000_000 + i,
                       "scheduler_delay_ns": 10, "execution_ns": 1000,
                       "via": "local_cache" if i % 2 == 0 else "provider"})
    m = compute_metrics(ledger, plan, 32)
    check("planned_counts", m["planned_setup"] == 10 and m["planned_measurement"] == 10)
    check("failed_kept", m["failed_measurement"] == 1 and m["success_rate"] == 0.9)
    check("window_throughput_denominator",
          m["window_seconds"] == 2.0 and m["window_throughput_per_s"] is not None)
    check("drain_reported", m["drain_seconds_after_last_arrival"] is not None)
    check("latency_percentiles", m["latency_ns"]["p50"] is not None and
          m["latency_ns"]["p99"] is not None)
    check("request_level_hit_rate", m["request_level_hit_rate"] == round(5 / 9, 4))

    ledger2 = [dict(r) for r in ledger]
    ledger2[-2]["via"] = None  # a COMPLETED request without attribution
    m2 = compute_metrics(ledger2, plan, 32)
    check("unattributed_no_request_hit_rate", m2["request_level_hit_rate"] is None and
          "service-event" in m2["hit_rate_note"])

    traces = {1: [{"event": "replica_evict"}, {"event": "replica_evict"},
                  {"event": "replica_admit"}, {"event": "local_cache_hit"}],
              2: [{"event": "replica_admit"}, {"event": "cache_gauge",
                                               "resident_bytes": "1"}]}
    cm = cache_metrics({"final_bytes": {1: 0, 2: 65536}}, traces, m)
    check("per_node_evictions", cm["per_node"][1]["evicts"] == 2 and
          cm["per_node"][2]["evicts"] == 0)
    check("competing_vs_quiet", cm["competing_nodes"] == [1] and cm["quiet_nodes"] == [2])
    check("eviction_rates", cm["evictions_per_second"] > 0 and
          cm["evictions_per_1000_completions"] > 0)
    check("counting_rule_stated", "never summed" in cm["counting_rule"])
    return 0 if failures == 0 else 1


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    ap.error("choose --self-test")
    return 3


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))