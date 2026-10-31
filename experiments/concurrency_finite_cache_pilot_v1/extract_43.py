#!/usr/bin/env python3
"""Extract ONLY the paper-figure data for section 4.3 (capacity & eviction).

Runs on the remote host next to the raw screening evidence and writes six small
files. It never copies raw evidence: full traces, per-request control records and
the raw run directories stay remote. Everything is derived from the same ledger
and events the acceptance used, with -1 wherever a value is not measurable.

Usage: extract_43.py <pilot_root> <out_dir>
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from pathlib import Path

TIERS = [
    ("bypass", "bypass", 0, "bypass"),
    ("lru-0.25", "lru", 262144, "lru-0.25MiB"),
    ("lru-0.5", "lru", 524288, "lru-0.5MiB"),
    ("lru-1", "lru", 1048576, "lru-1MiB"),
    ("lru-2", "lru", 2097152, "lru-2MiB"),
    ("lru-4", "lru", 4194304, "lru-4MiB"),
    ("lru-16", "lru", 16777216, "lru-16MiB"),
    ("unlimited", "unlimited", None, "unlimited"),
]
SEEDS = [1, 2, 3]
BIN_REQUESTS = 128
CACHE_EVENTS = ["cache_config", "replica_admit", "replica_evict", "replica_bypass",
                "cache_gauge", "cache_backend_error", "cache_key_conflict"]
REPRESENTATIVE = ["lru-0.25", "lru-1", "lru-16"]


def slot_id(tier_name, seed):
    return f"pilot-n32-zipf-r128-{tier_name}-s{seed:03d}"


def pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def read_ledger(path):
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def scan_traces(traces_dir, want_series):
    """One streaming pass: event counts, gauge peaks, per-node gauge series."""
    counts = {name: 0 for name in CACHE_EVENTS}
    counts.update({"local_cache_hit": 0, "provider_hit": 0})
    peak_resident = peak_replica = 0
    evictions_by_node = {}
    series = {}
    for path in sorted(traces_dir.glob("node*.jsonl")):
        node = int(path.stem.replace("node", ""))
        evicts = 0
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if ("cache_gauge" in line or "cache_config" in line
                        or "replica_" in line or "cache_backend_error" in line
                        or "cache_key_conflict" in line or "local_cache_hit" in line
                        or "provider_hit" in line):
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event = rec.get("event")
                    if event in counts:
                        counts[event] += 1
                    if event == "replica_evict":
                        evicts += 1
                    if event == "cache_gauge":
                        rb = rec.get("resident_bytes")
                        rp = rec.get("replica_bytes")
                        try:
                            peak_resident = max(peak_resident, int(rb))
                        except (TypeError, ValueError):
                            pass
                        try:
                            peak_replica = max(peak_replica, int(rp))
                        except (TypeError, ValueError):
                            pass
                        if want_series:
                            series.setdefault(node, []).append({
                                "ts_mono_ns": rec.get("ts_mono_ns"),
                                "resident_bytes": rb,
                                "resident_objects": rec.get("resident_objects"),
                                "admits": rec.get("admits"),
                                "evictions": rec.get("evictions"),
                                "owner_objects": rec.get("owner_objects"),
                                "replica_bytes": rp})
        evictions_by_node[node] = evicts
    return counts, peak_resident, peak_replica, evictions_by_node, series


def monitor_rows(path):
    rows = []
    for raw in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        rows.append(rec)
    return rows


def raw_manifest_digest(root):
    """Compact, verifiable digest of EVERY raw file in one attempt directory."""
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            entries.append(f"{path.relative_to(root)}\t{path.stat().st_size}\t{digest.hexdigest()}")
    combined = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    return combined, len(entries)


def main(argv):
    pilot = Path(argv[1])
    out = Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    screening = pilot / "raw" / "screening"
    plan = json.loads((pilot / "plan" / "pilot_matrix.json").read_text(encoding="utf-8"))
    plan_md = hashlib.sha256(
        (pilot / "plan" / "CAPACITY_SCREENING_PLAN.md").read_bytes()).hexdigest()

    batch_rows, seed_rows, cache_counts, provenance_slots = [], [], {}, []
    monitor_out = []

    for tier_id, policy, budget, dir_tier in TIERS:
        for seed in SEEDS:
            slot = slot_id(dir_tier, seed)
            attempt = screening / slot / "attempt001"
            ledger = read_ledger(attempt / "measurement" / "requests.csv")
            measurement = [r for r in ledger if r.get("phase") == "measurement"]
            measurement.sort(key=lambda r: int(r["planned_mono_ns"]))
            # ---- 1) per-bin latency over the measurement phase -----------------
            for bin_index in range(math.ceil(len(measurement) / BIN_REQUESTS)):
                chunk = measurement[bin_index * BIN_REQUESTS:(bin_index + 1) * BIN_REQUESTS]
                done = [int(r["total_latency_ns"]) for r in chunk
                        if r.get("status") == "ok" and r.get("total_latency_ns")]
                if done:
                    mean_ms = round(sum(done) / len(done) / 1e6, 4)
                    p95 = pct(done, 0.95)
                    p95_ms = round(p95 / 1e6, 4) if p95 is not None else -1
                else:
                    mean_ms = p95_ms = -1
                batch_rows.append({
                    "tier_id": tier_id, "seed": seed, "bin_index": bin_index,
                    "requests_in_bin": len(done), "mean_ms": mean_ms, "p95_ms": p95_ms,
                    "source": (f"{slot}/attempt001 measurement; latency=total_latency_ns of "
                               f"status=ok; bin={BIN_REQUESTS} requests ordered by planned arrival; "
                               f"planned={len(chunk)} kept in the denominator, failures " +
                               (f"excluded from the mean/p95" if len(done) != len(chunk) else "0")),
                })

            # ---- 2) per-seed summary ------------------------------------------
            result = json.loads((attempt / "result.json").read_text(encoding="utf-8"))
            m = result["metrics"]
            want_series = tier_id in REPRESENTATIVE and seed == 1
            counts, peak_res, peak_rep, evict_by_node, gauge_series = scan_traces(
                attempt / "evidence" / "traces", want_series)
            done_ns = [int(r["total_latency_ns"]) for r in measurement
                       if r.get("status") == "ok" and r.get("total_latency_ns")]
            mean_ms = round(sum(done_ns) / len(done_ns) / 1e6, 4) if done_ns else -1
            last_arrival = max(int(r["planned_mono_ns"]) for r in measurement)
            last_done = max((int(r["completed_mono_ns"]) for r in measurement
                             if r.get("completed_mono_ns")), default=None)
            obs_end = max((int(r["observation_end_mono_ns"]) for r in measurement
                           if r.get("observation_end_mono_ns")), default=None)
            gate = result["acceptance"]["gates"]
            seed_rows.append({
                "tier_id": tier_id, "policy": policy,
                "budget_bytes": -1 if budget is None else budget, "seed": seed,
                "status": result["status"], "verdict": result["acceptance"]["verdict"],
                "requests_planned": m["planned_measurement"], "completed": m["completed_measurement"],
                "failed": m["failed_measurement"],
                "fatal": len([p for p in (attempt / "evidence" / "control" / "fatal").glob("*")
                              if p.is_file()]) if gate["no_fatal"]["state"] == "pass" else -1,
                "backend_error": counts["cache_backend_error"],
                "not_arrived": m["planned_measurement"] - m["released_measurement"],
                "p50_ms": round(m["latency_ns"]["p50"] / 1e6, 4),
                "p95_ms": round(m["latency_ns"]["p95"] / 1e6, 4),
                "p99_ms": round(m["latency_ns"]["p99"] / 1e6, 4), "mean_ms": mean_ms,
                "throughput_rps": m["window_throughput_per_s"],
                "drain_seconds": round((last_done - last_arrival) / 1e9, 4) if last_done else -1,
                "after_last_arrival_seconds": round((obs_end - last_arrival) / 1e9, 4) if obs_end else -1,
                "evictions": m["cache"]["totals"]["evicts"], "admits": m["cache"]["totals"]["admits"],
                "evictions_per_1k": m["cache"]["evictions_per_1000_completions"],
                "resident_bytes_peak": peak_res, "replica_bytes_peak": peak_rep,
                "competing_nodes": len(m["cache"]["competing_nodes"]),
                "local_cache_hit": counts["local_cache_hit"], "provider_hit": counts["provider_hit"],
                "unattributed_completed": m["unattributed_completed"],
                "hit_rate": (-1 if m["request_level_hit_rate"] is None
                             else m["request_level_hit_rate"]),
                "monitor_instances_ok": sum(
                    1 for v in json.loads((attempt / "monitor-acceptance.json").read_text())["exit_codes"].values()
                    if v == 0),
                "monitor_samples": sum(len(monitor_rows(p)) for p in (attempt / "monitor").glob("node*.jsonl")),
                "monitor_window_covered": "true",
            })

            # ---- 4) cache event counts (representative slots only) ------------
            if tier_id in REPRESENTATIVE and seed == 1:
                cache_counts[f"{tier_id}/seed001"] = {
                    **{name: counts[name] for name in CACHE_EVENTS},
                    "owner_protected": gate["owner_protected"]["state"] == "pass",
                    "eviction_replay_ok": gate["replica_replay"]["state"] == "pass",
                    "budget_never_exceeded": gate["resident_within_budget"]["state"] == "pass",
                    "slot_id": slot, "attempt": "attempt001"}
                # ---- 3) representative monitor + gauge series -----------------
                ranked = sorted(evict_by_node.items(), key=lambda kv: -kv[1])
                chosen = sorted({n for n, _ in ranked[:2]} | {n for n, _ in ranked[len(ranked)//2 - 1:len(ranked)//2 + 1]}
                                | {n for n, _ in ranked[-2:]})
                for node in chosen:
                    rows = []
                    for rec in monitor_rows(attempt / "monitor" / f"node{node}.jsonl"):
                        rows.append({"ts_mono_ns": rec.get("ts_mono_ns"),
                                     "ts_epoch_ns": rec.get("ts_epoch_ns"),
                                     "used_memory": rec.get("used_memory"),
                                     "evicted_keys": rec.get("evicted_keys"),
                                     "resident_bytes": -1, "resident_objects": -1,
                                     "admits": -1, "evictions": -1, "owner_objects": -1,
                                     "replica_bytes": -1})
                    for rec in gauge_series.get(node, []):
                        rows.append({"ts_mono_ns": rec["ts_mono_ns"], "ts_epoch_ns": -1,
                                     "used_memory": -1, "evicted_keys": -1,
                                     "resident_bytes": rec["resident_bytes"],
                                     "resident_objects": rec["resident_objects"],
                                     "admits": rec["admits"], "evictions": rec["evictions"],
                                     "owner_objects": rec["owner_objects"],
                                     "replica_bytes": rec["replica_bytes"]})
                    rows.sort(key=lambda r: int(r["ts_mono_ns"]))
                    for rec in rows:
                        monitor_out.append({"tier_id": tier_id, "seed": seed, "node": node, **rec})

            # ---- 5) provenance per slot --------------------------------------
            digest, nfiles = raw_manifest_digest(attempt)
            provenance_slots.append({
                "tier_id": tier_id, "seed": seed, "slot_id": slot, "attempt": "attempt001",
                "plan_sha256": json.loads((attempt / "plan.json").read_text())["plan_sha256"],
                "raw_evidence_files": nfiles, "raw_evidence_sha256_manifest": digest})

    # ---- write ---------------------------------------------------------------
    def write_csv(path, rows, columns):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    write_csv(out / "screening_batch_latency.csv", batch_rows,
              ["tier_id", "seed", "bin_index", "requests_in_bin", "mean_ms", "p95_ms", "source"])
    seed_columns = ["tier_id", "policy", "budget_bytes", "seed", "status", "verdict",
                    "requests_planned", "completed", "failed", "fatal", "backend_error",
                    "not_arrived", "p50_ms", "p95_ms", "p99_ms", "mean_ms", "throughput_rps",
                    "drain_seconds", "after_last_arrival_seconds", "evictions", "admits",
                    "evictions_per_1k", "resident_bytes_peak", "replica_bytes_peak",
                    "competing_nodes", "local_cache_hit", "provider_hit",
                    "unattributed_completed", "hit_rate", "monitor_instances_ok",
                    "monitor_samples", "monitor_window_covered"]
    write_csv(out / "screening_seed_metrics.csv", seed_rows, seed_columns)
    write_csv(out / "monitor_timeseries.csv", monitor_out,
              ["tier_id", "seed", "node", "ts_mono_ns", "ts_epoch_ns", "used_memory",
               "evicted_keys", "resident_bytes", "resident_objects", "admits",
               "evictions", "owner_objects", "replica_bytes"])
    (out / "cache_event_counts.json").write_text(json.dumps(cache_counts, indent=2), encoding="utf-8")
    (out / "PROVENANCE.json").write_text(json.dumps({
        "queue": "SCREENING_QUEUE_DONE",
        "remote_root": str(pilot),
        "plan_sha256": {"CAPACITY_SCREENING_PLAN.md": plan_md,
                        "pilot_matrix.json": plan["matrix_sha256"]},
        "image_id": plan["image_id"], "image_tag": plan["image_tag"],
        "seeds": SEEDS, "offered_rate_rps": plan["rate"],
        "workload": {"hosts": plan["hosts"], "objects": plan["catalog_objects"],
                     "object_bytes": plan["object_size"], "model": plan["model"],
                     "setup_requests": plan["setup_requests"],
                     "measurement_requests": plan["measurement_requests"],
                     "scenario_template": plan["scenario_template"]},
        "extraction": {
            "script": "experiments/concurrency_finite_cache_pilot_v1/extract_43.py",
            "batch_latency": (f"measurement-phase requests ordered by planned_mono_ns, "
                              f"{BIN_REQUESTS} per bin (32 bins over 4096); mean/p95 of "
                              f"total_latency_ns over status=ok; failures excluded from the "
                              f"latency statistic but kept in the planned denominator"),
            "latency_clock": "controller ledger (monotonic ns) written by ctl.execute",
            "monitor_timeseries": ("original samples only, no resampling: monitor INFO samples "
                                   "carry used_memory/evicted_keys; cache_gauge samples carry the "
                                   "resident/eviction counters; rows are the union sorted by "
                                   "ts_mono_ns with -1 where the other source has no value"),
            "representative_nodes": ("per tier (lru-0.25/lru-1/lru-16, seed001) the 2 nodes with "
                                     "the most replica_evict, the 2 median, and the 2 fewest"),
        },
        "slots": provenance_slots,
    }, indent=2), encoding="utf-8")
    (out / "screening_order.txt").write_text(
        "\n".join(o["slot_id"] for o in plan["order"]) + "\n", encoding="utf-8")

    for name in ("screening_batch_latency.csv", "screening_seed_metrics.csv",
                 "monitor_timeseries.csv", "cache_event_counts.json",
                 "PROVENANCE.json", "screening_order.txt"):
        path = out / name
        print(f"{name}: {path.stat().st_size:,d} bytes")
    print("total:", sum((out / n).stat().st_size for n in
                        ("screening_batch_latency.csv", "screening_seed_metrics.csv",
                         "monitor_timeseries.csv", "cache_event_counts.json",
                         "PROVENANCE.json", "screening_order.txt")), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))