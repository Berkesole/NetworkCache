#!/usr/bin/env python3
"""Read-only extraction of the supplement derived tree -> the three delivery files.

Same column names as vldb/exp/open-arrival-concurrency/extract_slides_v5.py (so the
local data/ pipeline and plot scripts keep working) plus the supplement extras:
host_limited, query_lifetime_seconds, activation_ok and the warm-up gate evidence.

Batch binning: the delivery reports 128-request bins. The iid families and the
federation post phase are already at or above that granularity, so their native
batches pass through unchanged; the controlled family has native 32-request batches
(one per round) and consecutive rounds are merged until 128 requests accumulate,
giving 32 bins over 4096 requests. Federation pre/post phases are never merged
across the phase boundary, so 4.4 keeps its 32 pre + 48 post bins.

Usage: extract_supplement.py <campaign_root> <out_dir>
"""
import csv
import hashlib
import json
import pathlib
import sys

STAGE = "supplement"
BIN_TARGET = 128

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
               'warmup_gate_slope_B_ns_per_bin,warmup_gate_ci_B_ns_per_bin').split(',')

BATCH_FIELDS = ('family,hosts,size,catalog,distribution,model,rate,method,seed,repetition,'
                'phase,direction,request_class,batch_index,requests_in_bin,planned,completed,'
                'completion_fraction,local_cache_fraction,mean_ms,execution_ms,node_queue_ms,'
                'scheduler_delay_ms,source').split(',')

HOST_LIMITED_PERCENT = 90


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def split_phase(phase):
    if ':' in phase:
        ph, direction, cls = phase.split(':', 2)
        cls = cls if cls in ('local_control', 'remote') else 'local'
        return ph, direction, cls
    return phase, '', 'local'


def delivery_bins(rows, source):
    """Aggregate the native batch_timeline rows into the delivery bins.

    Federation (4.4): group by (pre/post, bin) across the direction/request_class
    variants of that bin - A_local 64 + B_local 64 for pre, and A->B 64 + B->A 64 +
    A_local 16 + B_local 16 for post - so the delivery carries exactly the 32 pre and
    48 post bins the specification defines (128 and 160 requests).
    Everything else: native bins are walked in order and consecutive ones merge until
    BIN_TARGET requests accumulate (controlled has 32-request rounds -> four rounds per
    bin; the iid families are already 128-request bins).
    """
    def emit(group, phase, direction, cls, index):
        planned = sum(num(g['planned']) for g in group)
        completed = sum(num(g['completed']) for g in group)

        def weighted(field):
            total, weight = 0.0, 0.0
            for g in group:
                v, w = num(g.get(field)), num(g['planned'])
                if v >= 0 and w > 0:
                    total += v * w
                    weight += w
            return round(total / weight, 12) if weight else -1

        return {
            'phase': phase, 'direction': direction, 'request_class': cls, 'batch_index': index,
            'requests_in_bin': int(completed), 'planned': int(planned), 'completed': int(completed),
            'completion_fraction': round(completed / planned, 12) if planned > 0 else -1,
            'local_cache_fraction': weighted('local_cache_fraction'),
            'mean_ms': weighted('total_latency_ms'), 'execution_ms': weighted('execution_ms'),
            'node_queue_ms': weighted('node_queue_ms'),
            'scheduler_delay_ms': weighted('scheduler_delay_ms'), 'source': source}

    federation = any(row['phase'].split(':')[0] in ('pre', 'post') for row in rows)
    out = []
    if federation:
        grouped = {}
        for row in rows:
            family, _direction, _cls = split_phase(row['phase'])
            grouped.setdefault((family, int(row['batch'])), []).append(row)
        for index, key in enumerate(sorted(grouped, key=lambda k: (k[0] != 'pre', k[1])), 1):
            out.append(emit(grouped[key], key[0], 'mixed', 'mixed', index))
        return out

    acc, index = [], 0
    for row in rows:
        if not acc:
            index += 1
        acc.append(row)
        if sum(num(g['planned']) for g in acc) >= BIN_TARGET:
            out.append(emit(acc, split_phase(acc[0]['phase'])[0], '', 'local', index))
            acc = []
    if acc:
        out.append(emit(acc, split_phase(acc[0]['phase'])[0], '', 'local', index))
    return out


def main():
    root = pathlib.Path(sys.argv[1])
    out = pathlib.Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    campaign = json.loads((root / 'campaign.json').read_text())
    slots, batches, provenance, status_map = [], [], [], {}
    for config in campaign['assignments'][STAGE]:
        slot = config['id']
        raw_attempts = sorted((root / 'raw' / STAGE / slot).glob('attempt*'))
        if not raw_attempts:
            continue
        # a slot may have been re-run after an interruption, so always take the latest
        latest = raw_attempts[-1]
        attempt = root / 'derived' / STAGE / slot / latest.name
        raw = latest
        if not (attempt / 'summary.json').is_file():
            continue
        summary = json.loads((attempt / 'summary.json').read_text())
        cfg, gate, timing = summary['config'], summary.get('gate', {}), summary.get('timing', {})
        status_map[slot] = dict(qualified=bool(gate.get('qualified')), status=summary.get('status'),
                                reason=gate.get('reason'),
                                isolation_expiry=gate.get('isolation_expiry'))
        if not gate.get('qualified'):
            # only qualified runs enter the delivery, exactly like the formal selection
            continue
        tot, ex = summary.get('total_latency_ms', {}), summary.get('execution_ms', {})
        nq = summary.get('node_queue_ms', {})
        sd = summary.get('scheduler_delay_ms', {})
        hmf = attempt / 'health_and_mechanisms.json'
        hm = json.loads(hmf.read_text()) if hmf.is_file() else {}
        ev, res = hm.get('events', {}), hm.get('resources', {})
        peak = res.get('host_cpu_peak_percent')
        gate_evidence = {}
        wgf = raw / 'warmup_gate.json'
        if wgf.is_file():
            gate_evidence = json.loads(wgf.read_text())

        def g(dic, key, factor=1):
            v = dic.get(key)
            return round(v * factor, 6) if isinstance(v, (int, float)) else -1

        def dom(letter, field):
            d = (gate_evidence.get('domains') or {}).get(letter) or {}
            v = d.get(field)
            return round(v, 6) if isinstance(v, (int, float)) else -1

        slots.append(dict(
            family=cfg['family'], hosts=cfg['hosts'], size=cfg['size'], catalog=cfg['catalog'],
            distribution=cfg['distribution'], model=cfg['model'], rate=cfg['rate'],
            method=cfg['method'], seed=cfg['seed'], repetition=cfg['repetition'], slot_id=slot,
            stage=STAGE, status=summary.get('status', -1),
            verdict=str(gate.get('qualified', -1)).lower(), gate_reason=gate.get('reason', -1),
            requests_planned=summary.get('planned', -1), completed=summary.get('completed', -1),
            completion_fraction=summary.get('completion_fraction', -1),
            failed=int(summary.get('planned', 0)) - int(summary.get('completed', 0)),
            formal_expiry=gate.get('formal_expiry', -1),
            isolation_expiry=gate.get('isolation_expiry', -1),
            total_mean_ms=g(tot, 'mean'), total_p50_ms=g(tot, 'p50'), total_p95_ms=g(tot, 'p95'),
            total_p99_ms=g(tot, 'p99'), execution_mean_ms=g(ex, 'mean'),
            node_queue_mean_ms=g(nq, 'mean'), scheduler_delay_mean_ms=g(sd, 'mean'),
            drain_seconds=summary.get('drain_after_last_arrival_seconds', -1),
            after_last_arrival_seconds=summary.get('measurement_seconds', -1),
            local_cache_fraction=summary.get('local_cache_fraction', -1),
            peak_outstanding=hm.get('peak_outstanding', -1),
            host_cpu_mean_percent=res.get('host_cpu_mean_percent', -1),
            host_cpu_peak_percent=peak if peak is not None else -1,
            process_failure=str(hm.get('process_failure', -1)).lower(),
            local_cache_hit_events=ev.get('local_cache_hit', -1),
            provider_hit_events=ev.get('provider_hit', -1),
            boundary_seconds=timing.get('boundary_seconds', -1),
            total_wall_seconds=summary.get('total_wall_seconds', -1),
            finalization_seconds=summary.get('finalization_seconds', -1),
            raw=str(raw), result_sha256=sha(raw / 'result.json'),
            host_limited=str(bool(peak is not None and peak >= HOST_LIMITED_PERCENT)).lower(),
            query_lifetime_seconds=cfg.get('query_lifetime_seconds', -1),
            activation_ok=str(timing.get('activation_ok', 'false')).lower(),
            warmup_gate_passed=str(gate_evidence.get('passed', '')).lower() if gate_evidence else '-1',
            warmup_gate_slope_A_ns_per_bin=dom('A', 'slope_ns_per_bin'),
            warmup_gate_ci_A_ns_per_bin=dom('A', 'ci_high_ns_per_bin'),
            warmup_gate_slope_B_ns_per_bin=dom('B', 'slope_ns_per_bin'),
            warmup_gate_ci_B_ns_per_bin=dom('B', 'ci_high_ns_per_bin')))

        with (attempt / 'batch_timeline.csv').open(newline='') as handle:
            native = list(csv.DictReader(handle))
        for batch in delivery_bins(native, source=f'{slot}/{latest.name} batch_timeline.csv'):
            batches.append(dict(
                family=cfg['family'], hosts=cfg['hosts'], size=cfg['size'], catalog=cfg['catalog'],
                distribution=cfg['distribution'], model=cfg['model'], rate=cfg['rate'],
                method=cfg['method'], seed=cfg['seed'], repetition=cfg['repetition'],
                **batch))
        provenance.append(dict(slot=slot, condition=cfg['condition'], attempt=latest.name, raw=str(raw),
                               result_sha256=sha(raw / 'result.json'),
                               summary_sha256=sha(attempt / 'summary.json'),
                               batch_timeline_sha256=sha(attempt / 'batch_timeline.csv'),
                               qualified=gate.get('qualified'),
                               isolation_expiry=gate.get('isolation_expiry')))

    def write(name, rows, fields):
        with (out / name).open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f"{name}: {len(rows)} rows")

    write('slides_v5_slot_metrics.csv', slots, SLOT_FIELDS)
    write('slides_v5_batch_latency.csv', batches, BATCH_FIELDS)
    (out / 'PROVENANCE.json').write_text(json.dumps(dict(
        campaign=campaign['fingerprint'], branch=campaign['branch'], commit=campaign['commit'],
        image_id=campaign['image_id'], stage=STAGE,
        plan_sha256=dict(schedule_conditions_sha256=hashlib.sha256(json.dumps(
            campaign['supplement']['conditions'], sort_keys=True).encode()).hexdigest(),
            slots_file=campaign['supplement']['slots_file'],
            order_seed=campaign['supplement']['order_seed']),
        offered_rate_rps=128, seeds=list(range(1, 11)),
        changes=dict(
            controlled_schedule='4096 requests = 128 rounds x 32 focus keys (native 32-request batches)',
            federation='pre 32 bins -> warm-up gate (both domains: OLS slope of the last 4 pre bins, '
                       '95% t CI df=2 must contain 0) -> activation -> post 48 bins; 11776 requests',
            lifetime='per-slot query lifetime override: E family 7 s, everything else 30 s'),
        warmup_gate=campaign['supplement']['warmup_gate'],
        host_limited_rule=f'host_cpu_peak_percent >= {HOST_LIMITED_PERCENT}',
        binning=(f'delivery bins hold {BIN_TARGET} requests; controlled native 32-request rounds '
                 f'merge four at a time, iid families are already {BIN_TARGET}. Federation bins group '
                 f'the direction/request_class variants of one pre/post bin (pre 128, post 160), so 4.4 '
                 f'delivers 32 pre + 48 post bins with direction/request_class marked mixed'),
        extraction='vldb/exp/open-arrival-concurrency/extract_supplement.py (read-only)',
        slots=provenance, run_status=status_map), indent=2, sort_keys=True) + '\n')
    print('PROVENANCE.json: %d slots' % len(provenance))


if __name__ == '__main__':
    main()