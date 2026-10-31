#!/usr/bin/env python3
"""Track B v2 extraction: slot metrics + per-class batch rows + PROVENANCE.

Differences from the v1 extractor:
  * stage supplement_v2 (180 slots of supplementary_slots_v2.csv)
  * R3: the federation batch table is delivered PER CLASS, exactly as the harness
    writes it (pre:A_local:local, post:A_to_B:remote, post:A_local:local_control, ...),
    i.e. 32 pre rows x 2 + 48 post rows x 4 = 256 rows per slot. Nothing is aggregated
    into a "mixed" row; the weighted whole-system mean is recoverable with the formula
    recorded in PROVENANCE and the per-class request counts carried in each row.
  * new per-slot fields required by the handoff: image_id, campaign_id, schedule hash,
    slots_in_parallel, offered_mib_per_s (on top of host_cpu_mean/peak, host_limited,
    query_lifetime_seconds, activation_ok and the warm-up gate evidence).
  * non-federation families keep 128-request delivery bins (controlled's native
    32-request rounds merge four at a time; the iid families are already 128).

Usage: extract_supplement_v2.py <campaign_root> <out_dir>
"""
import csv
import hashlib
import json
import os
import pathlib
import sys

STAGE = "supplement_v2"
BIN_TARGET = 128
HOST_LIMITED_PERCENT = 90
MIB = 1024 * 1024

SLOT_FIELDS = ('family,hosts,size,catalog,distribution,model,rate,method,seed,repetition,slot_id,'
               'stage,status,verdict,gate_reason,requests_planned,completed,completion_fraction,failed,'
               'formal_expiry,isolation_expiry,'
               'total_mean_ms,total_p50_ms,total_p95_ms,total_p99_ms,'
               'execution_mean_ms,node_queue_mean_ms,scheduler_delay_mean_ms,'
               'drain_seconds,after_last_arrival_seconds,local_cache_fraction,'
               'peak_outstanding,host_cpu_mean_percent,host_cpu_peak_percent,process_failure,'
               'local_cache_hit_events,provider_hit_events,'
               'boundary_seconds,total_wall_seconds,finalization_seconds,raw,result_sha256,'
               'host_limited,query_lifetime_seconds,activation_ok,'
               'warmup_gate_passed,warmup_gate_slope_A_ns_per_bin,warmup_gate_ci_A_ns_per_bin,'
               'warmup_gate_slope_B_ns_per_bin,warmup_gate_ci_B_ns_per_bin,'
               'image_id,campaign_id,schedule_sha256,slots_in_parallel,offered_mib_per_s').split(',')

BATCH_FIELDS = ('family,hosts,size,catalog,distribution,model,rate,method,seed,repetition,'
                'phase,direction,request_class,batch_index,requests_in_bin,planned,completed,'
                'completion_fraction,local_cache_fraction,mean_ms,execution_ms,node_queue_ms,'
                'scheduler_delay_ms,weighted_contribution,source').split(',')


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1.0


def split_phase(phase):
    if ':' in phase:
        ph, direction, cls = phase.split(':', 2)
        return ph, direction, (cls if cls in ('local_control', 'remote') else 'local')
    return phase, '', 'local'


def emit(group, phase, direction, cls, index):
    planned = sum(num(g['planned']) for g in group)
    completed = sum(num(g['completed']) for g in group)

    def weighted(field):
        total = weight = 0.0
        for g in group:
            v, w = num(g.get(field)), num(g['planned'])
            if v >= 0 and w > 0:
                total += v * w
                weight += w
        return round(total / weight, 12) if weight else -1

    return {'phase': phase, 'direction': direction, 'request_class': cls, 'batch_index': index,
            'requests_in_bin': int(completed), 'planned': int(planned), 'completed': int(completed),
            'completion_fraction': round(completed / planned, 12) if planned > 0 else -1,
            'local_cache_fraction': weighted('local_cache_fraction'),
            'mean_ms': weighted('total_latency_ms'), 'execution_ms': weighted('execution_ms'),
            'node_queue_ms': weighted('node_queue_ms'),
            'scheduler_delay_ms': weighted('scheduler_delay_ms')}


def delivery_bins(rows, slot):
    """Federation -> one row per class per bin (R3); others -> merged 128-request bins."""
    out = []
    federation = any(r['phase'].split(':')[0] in ('pre', 'post') for r in rows)
    if federation:
        grouped = {}
        for row in rows:
            fam, direction, cls = split_phase(row['phase'])
            grouped.setdefault((fam, int(row['batch']), direction, cls), []).append(row)
        for index, key in enumerate(
                sorted(grouped, key=lambda k: (k[0] != 'pre', k[1], k[2], k[3])), 1):
            fam, bin_index, direction, cls = key
            row = emit(grouped[key], fam, direction, cls, index)
            row['source'] = f"{slot} batch_timeline.csv phase={fam}:{direction}:{cls} bin={bin_index}"
            row['weighted_contribution'] = round(1.0, 12)
            out.append(row)
        return out

    acc, index = [], 0
    for row in rows:
        if not acc:
            index += 1
        acc.append(row)
        if sum(num(g['planned']) for g in acc) >= BIN_TARGET:
            r = emit(acc, split_phase(acc[0]['phase'])[0], '', 'local', index)
            r['source'] = f"{slot} batch_timeline.csv (merged to {BIN_TARGET}-request bins)"
            r['weighted_contribution'] = round(sum(num(g['planned']) for g in acc) / 4096, 12)
            out.append(r)
            acc = []
    if acc:
        r = emit(acc, split_phase(acc[0]['phase'])[0], '', 'local', index)
        r['source'] = f"{slot} batch_timeline.csv (merged to {BIN_TARGET}-request bins)"
        r['weighted_contribution'] = round(sum(num(g['planned']) for g in acc) / 4096, 12)
        out.append(r)
    return out


def main():
    root = pathlib.Path(sys.argv[1])
    out = pathlib.Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    campaign = json.loads((root / "campaign.json").read_text())
    slots, batches, provenance, run_status = [], [], [], {}
    for config in campaign["assignments"][STAGE]:
        slot = config["id"]
        attempts = sorted((root / "raw" / STAGE / slot).glob("attempt*"))
        if not attempts:
            run_status[slot] = dict(qualified=False, status="not_started")
            continue
        latest = attempts[-1]
        attempt = root / "derived" / STAGE / slot / latest.name
        if not (attempt / "summary.json").is_file():
            run_status[slot] = dict(qualified=False, status="no_summary")
            continue
        summary = json.loads((attempt / "summary.json").read_text())
        cfg, gate, timing = summary["config"], summary.get("gate", {}), summary.get("timing", {})
        run_status[slot] = dict(qualified=bool(gate.get("qualified")), status=summary.get("status"),
                                reason=gate.get("reason"),
                                isolation_expiry=gate.get("isolation_expiry"),
                                attempt=latest.name)
        if not gate.get("qualified"):
            continue
        tot, ex = summary.get("total_latency_ms", {}), summary.get("execution_ms", {})
        nq, sd = summary.get("node_queue_ms", {}), summary.get("scheduler_delay_ms", {})
        hmf = attempt / "health_and_mechanisms.json"
        hm = json.loads(hmf.read_text()) if hmf.is_file() else {}
        ev, res = hm.get("events", {}), hm.get("resources", {})
        peak = res.get("host_cpu_peak_percent")
        gate_ev = {}
        wgf = latest / "warmup_gate.json"
        if wgf.is_file():
            gate_ev = json.loads(wgf.read_text())
        plan = json.loads((latest / "plan.json").read_text()) if (latest / "plan.json").is_file() else {}
        # slots_in_parallel is recorded in result.json by pilot.one(); summary.json does not
        # carry it, so read the raw result as the authority
        raw_result = json.loads((latest / "result.json").read_text())
        slots_in_parallel = raw_result.get("slots_in_parallel")

        def g(dic, key, factor=1):
            v = dic.get(key)
            return round(v * factor, 6) if isinstance(v, (int, float)) else -1

        def dom(letter, field):
            d = (gate_ev.get("domains") or {}).get(letter) or {}
            v = d.get(field)
            return round(v, 6) if isinstance(v, (int, float)) else -1

        offered = round(int(cfg["rate"]) * int(cfg["size"]) / MIB, 6)
        slots.append(dict(
            family=cfg["family"], hosts=cfg["hosts"], size=cfg["size"], catalog=cfg["catalog"],
            distribution=cfg["distribution"], model=cfg["model"], rate=cfg["rate"],
            method=cfg["method"], seed=cfg["seed"], repetition=cfg["repetition"], slot_id=slot,
            stage=STAGE, status=summary.get("status", -1),
            verdict=str(gate.get("qualified", -1)).lower(), gate_reason=gate.get("reason", -1),
            requests_planned=summary.get("planned", -1), completed=summary.get("completed", -1),
            completion_fraction=summary.get("completion_fraction", -1),
            failed=int(summary.get("planned", 0)) - int(summary.get("completed", 0)),
            formal_expiry=gate.get("formal_expiry", -1),
            isolation_expiry=gate.get("isolation_expiry", -1),
            total_mean_ms=g(tot, "mean"), total_p50_ms=g(tot, "p50"), total_p95_ms=g(tot, "p95"),
            total_p99_ms=g(tot, "p99"), execution_mean_ms=g(ex, "mean"),
            node_queue_mean_ms=g(nq, "mean"), scheduler_delay_mean_ms=g(sd, "mean"),
            drain_seconds=summary.get("drain_after_last_arrival_seconds", -1),
            after_last_arrival_seconds=summary.get("measurement_seconds", -1),
            local_cache_fraction=summary.get("local_cache_fraction", -1),
            peak_outstanding=hm.get("peak_outstanding", -1),
            host_cpu_mean_percent=res.get("host_cpu_mean_percent", -1),
            host_cpu_peak_percent=peak if peak is not None else -1,
            process_failure=str(hm.get("process_failure", -1)).lower(),
            local_cache_hit_events=ev.get("local_cache_hit", -1),
            provider_hit_events=ev.get("provider_hit", -1),
            boundary_seconds=timing.get("boundary_seconds", -1),
            total_wall_seconds=summary.get("total_wall_seconds", -1),
            finalization_seconds=summary.get("finalization_seconds", -1),
            raw=str(latest), result_sha256=sha(latest / "result.json"),
            host_limited=str(bool(peak is not None and peak >= HOST_LIMITED_PERCENT)).lower(),
            query_lifetime_seconds=cfg.get("query_lifetime_seconds", -1),
            activation_ok=str(timing.get("activation_ok", "false")).lower(),
            warmup_gate_passed=str(gate_ev.get("passed", "")).lower() if gate_ev else "-1",
            warmup_gate_slope_A_ns_per_bin=dom("A", "slope_ns_per_bin"),
            warmup_gate_ci_A_ns_per_bin=dom("A", "ci_high_ns_per_bin"),
            warmup_gate_slope_B_ns_per_bin=dom("B", "slope_ns_per_bin"),
            warmup_gate_ci_B_ns_per_bin=dom("B", "ci_high_ns_per_bin"),
            image_id=campaign["image_id"], campaign_id=campaign["fingerprint"],
            schedule_sha256=plan.get("plan_sha256", ""),
            slots_in_parallel=(slots_in_parallel if slots_in_parallel is not None else -1),
            offered_mib_per_s=offered))

        with (attempt / "batch_timeline.csv").open(newline="") as handle:
            native = list(csv.DictReader(handle))
        for row in delivery_bins(native, slot):
            batches.append(dict(
                family=cfg["family"], hosts=cfg["hosts"], size=cfg["size"], catalog=cfg["catalog"],
                distribution=cfg["distribution"], model=cfg["model"], rate=cfg["rate"],
                method=cfg["method"], seed=cfg["seed"], repetition=cfg["repetition"], **row))
        provenance.append(dict(slot=slot, condition=cfg["condition"], attempt=latest.name,
                               raw=str(latest), result_sha256=sha(latest / "result.json"),
                               summary_sha256=sha(attempt / "summary.json"),
                               batch_timeline_sha256=sha(attempt / "batch_timeline.csv"),
                               qualified=gate.get("qualified"),
                               isolation_expiry=gate.get("isolation_expiry"),
                               slots_in_parallel=slots_in_parallel,
                               offered_mib_per_s=offered))

    def write(name, rows, fields):
        with (out / name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f"{name}: {len(rows)} rows")

    write("slides_v5_slot_metrics.csv", slots, SLOT_FIELDS)
    write("slides_v5_batch_latency.csv", batches, BATCH_FIELDS)
    (out / "PROVENANCE.json").write_text(json.dumps(dict(
        campaign=campaign["fingerprint"], branch=campaign["branch"], commit=campaign["commit"],
        image_id=campaign["image_id"], stage=STAGE, track="B (volcengine-3)",
        plan=dict(slots_file="vldb/exp/supplementary_slots_v2.csv",
                  order_seed="concurrency-supplement-v2:order-v1",
                  conditions_sha256=hashlib.sha256(json.dumps(
                      campaign["supplement_v2"]["conditions"], sort_keys=True).encode()).hexdigest()),
        changes=dict(
            R1_bandwidth="4.2a: all three object sizes at lambda=32 (offered 0.03 / 2 / 32 MiB/s); "
                         "4.2b keeps the lambda=128 64 KiB anchor against the frozen baseline",
            R2_host_load="<=2 slots in parallel; slots_in_parallel recorded per slot",
            R3_per_class="federation batch rows are per class (32 pre x 2 + 48 post x 4 = 256 per "
                         "slot), never aggregated into a mixed row"),
        aggregation_formula=("whole-system mean per bin = sum_over_classes(mean_ms_class * "
                             "planned_class) / sum_over_classes(planned_class); each row carries its "
                             "own planned/completed request counts, so the weighted mean and the "
                             "split view are both recoverable"),
        host_limited_rule=f"host_cpu_peak_percent >= {HOST_LIMITED_PERCENT}",
        binning=f"non-federation delivery bins hold {BIN_TARGET} requests (controlled merges four "
                f"native 32-request rounds); federation rows are per class and bin",
        extraction="source/experiments/concurrency_slides_v5/extract_supplement_v2.py (read-only)",
        run_status=run_status, slots=provenance), indent=2, sort_keys=True) + "\n")
    print("PROVENANCE.json: %d slots" % len(provenance))


if __name__ == "__main__":
    main()