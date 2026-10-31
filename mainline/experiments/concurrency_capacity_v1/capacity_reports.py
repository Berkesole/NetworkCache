"""Capacity validation, per-run capacity records and cross-seed aggregation.

Difference from the slides-v5 validator: an overloaded run is a *data point*,
not a failure. ``validate_capacity`` accepts ``status in ('ok','load_timeout')``
and requires only that (a) provenance matches the frozen campaign, (b) no
process/transport/controller/correctness failure occurred, and (c) every
completed request was delivered exactly once with the right bytes. Incomplete
requests stay in the denominator.
"""
import collections
import hashlib
import json
import math
from pathlib import Path

import analysis as a
import capacity_analysis as ca
import capacity_matrix as matrix
import controller as ctl
import network_backend

PROVENANCE_ENV = dict(
    RSYNC_QUERY_TIMEOUT_MS='30000', RSYNC_FALLBACK_DELAY_MS='1500',
    KATHARA_NODE_LOOP_SLEEP_MS='25', RSYNC_BLOOM_BITS='1024',
    RSYNC_BLOOM_HASH_COUNT='3', RSYNC_WIRE_VERSION='3',
    KATHARA_LINK_DELAY_MS='2', KATHARA_LINK_RATE_MBIT='1000',
    KATHARA_LINK_MTU='1500', KATHARA_LINK_QUEUE_PACKETS='1000')
SCHEDULE_KEYS = ('request_index', 'phase', 'bin', 'round', 'key', 'requester',
                 'owner', 'value_size_bytes', 'expected_fnv1a64', 'schedule_sha256')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def measured(raw):
    return [r for p in sorted(Path(raw).glob('measurement-*/requests.csv')) for r in a.read(p)]


def _max_overlap(events):
    active = maximum = 0
    for _, delta in sorted(events):
        active += delta
        maximum = max(maximum, active)
    return maximum


def peak(rows):
    """Peak overlapping [node_start, completed] intervals, per requester node."""
    groups = collections.defaultdict(list)
    for row in rows:
        start = a.num(row, 'node_start_mono_ns')
        end = a.num(row, 'completed_mono_ns')
        if start is not None and end is not None:
            groups[str(row['requester'])].append((start, 1))
            groups[str(row['requester'])].append((end, -1))
    return {node: _max_overlap(events) for node, events in groups.items()}


def peak_global(rows):
    """Peak overlapping intervals across all requester nodes."""
    events = []
    for row in rows:
        start = a.num(row, 'node_start_mono_ns')
        end = a.num(row, 'completed_mono_ns')
        if start is not None and end is not None:
            events += [(start, 1), (end, -1)]
    return _max_overlap(events)


def _phase_clock(raw):
    phases = []
    for path in sorted(Path(raw).glob('measurement-*/result.json')):
        phases.append((path.parent, json.loads(path.read_text())))
    return phases


def _provenance(raw, root, campaign):
    assert (raw / 'runtime_image_id.before_launch').read_text().strip() == campaign['image_id'], 'image mismatch'
    containers = json.loads((raw / 'containers.before.json').read_text())
    assert all(x['Image'] == campaign['image_id'] for x in containers), 'container image mismatch'
    assert not any(x['State'].get('OOMKilled') for x in json.loads((raw / 'containers.after.json').read_text())), 'OOM'
    bootstrap = json.loads((raw / 'bootstrap_manifest.json').read_text())
    assert bootstrap['start-node.sh'] == campaign['logging_policy']['startup_sha256'], 'startup provenance mismatch'
    backend = json.loads((raw / 'backend_snapshot.json').read_text())
    assert backend['backend'] == campaign['network_backend'], 'backend provenance mismatch'
    assert all(n['Driver'] == campaign['network_backend']['plugin_name'] and len(n['Containers']) == 2
               for n in backend['networks']), 'wrong backend or non-point-to-point LAN'
    assert backend['point_to_point_lans'] == json.loads(json.dumps(
        network_backend.point_to_point((raw / 'lab.conf').read_text()))), 'topology mismatch'
    for state in backend['interfaces']:
        for q in state['qdisc']:
            if q['kind'] == 'netem':
                network_backend.validate_qdisc(q['options'])
    offload = json.loads((raw / 'offload_snapshot.json').read_text())
    assert len(offload['interfaces']) == 4 * len(backend['point_to_point_lans']), 'offload evidence incomplete'
    for iface in offload['interfaces']:
        for feature in ('generic-segmentation-offload', 'generic-receive-offload', 'tcp-segmentation-offload'):
            assert any(line in (feature + ': off', feature + ': off [fixed]') for line in iface['after'].splitlines()), 'offload enabled'
    env = json.loads((raw / 'environment.json').read_text())
    for key, value in PROVENANCE_ENV.items():
        assert env[key] == value, key


def expected_offsets(config, count):
    """Arrival offsets the request ledger must match for this condition.

    Non-federation smoke uses a fixed 1 ms ladder to force same-node overlap and
    warm-cache evidence; every other condition uses the controller's open-arrival
    generator.
    """
    if config['stage'] != 'smoke' or config['family'] == 'federation':
        return ctl.offsets(count, config['model'], config['rate'], config['arrival_seed'])[0]
    return [i * 1_000_000 for i in range(count)]


def _trace_evidence(raw):
    """Trace-derived counts for the conditions that keep event traces on."""
    counts = collections.Counter()
    expiries = []
    tcp_probe_data = 0
    for folder in ('traces', 'tcp-traces'):
        for path in (raw / folder).glob('*.jsonl'):
            for line in path.open():
                event = json.loads(line)
                counts[event.get('event')] += 1
                if str(event.get('app_request_id', '')).startswith('900000') and event.get('packet_type') == 'DATA':
                    tcp_probe_data += 1
                if event.get('event') == 'query_expired_unsatisfied':
                    expiries.append(event)
    return counts, expiries, tcp_probe_data


def _federation_isolation(raw, config, phases):
    """Port the slides-v5 federation isolation checks; returns the probe IDs."""
    import federation
    iso = json.loads((raw / 'isolation.json').read_text())
    act = json.loads((raw / 'activation.json').read_text())
    assert iso['status'] == 'ok' and len(iso['probes']) == 2, 'isolation probes'
    probes = {p['request_index'] for p in iso['probes']}
    assert probes == {'900000001', '900000002'}, probes
    assert int(phases[0]['last_completion_mono_ns']) < iso['lower_mono_ns'] < iso['upper_mono_ns'] \
        <= act['issued_mono_ns'], 'isolation window ordering'
    assert len(act['ack_mono_ns']) == 2 and min(act['ack_mono_ns']) >= act['issued_mono_ns'], 'activation acks'
    assert act['post_ready_mono_ns'] - max(act['ack_mono_ns']) >= 500_000_000, 'stable window'
    assert phases[1]['base_mono_ns'] >= act['post_ready_mono_ns'], 'post phase before activation'
    counts, expiries, tcp_probe_data = _trace_evidence(raw)
    if config['method'] == 'nc':
        assert len(federation.expiry_evidence(expiries, iso['probes'],
                                              {int(k): v for k, v in iso['starts'].items()},
                                              iso['lower_mono_ns'], iso['upper_mono_ns'])) == 2, 'probe expiry evidence'
        assert len(list((raw / 'control/probe-retired').glob('*.tsv'))) == 2, 'probe retirement'
        assert not any((raw / 'control/completions' / f'{i}.tsv').exists() for i in probes), 'late probe DATA'
    else:
        assert not expiries and tcp_probe_data == 0, 'tcp probe produced DATA or expiry'
        for probe in iso['probes']:
            fields = (raw / 'control/completions' / f"{probe['request_index']}.tsv").read_text().strip().split('\t')
            assert fields == probe['failed_completion'] and fields[3] == 'io_error' \
                and fields[4] == '0' and fields[11] == '0', 'tcp probe completion shape'
    return probes


def _schedule_and_audit(raw, config):
    assert not list((raw / 'control/fatal').glob('*')), 'runtime fatal'
    expected = [x for x in a.read(raw / 'schedule.csv') if x['phase'] != 'setup']
    rows = measured(raw)
    assert len(rows) == len(expected), 'measurement count'
    assert len({x['request_index'] for x in rows}) == len(rows), 'duplicate request id'
    assert [[x[k] for k in SCHEDULE_KEYS] for x in rows] == [[x[k] for k in SCHEDULE_KEYS] for x in expected], 'measurement schedule mismatch'
    for x in rows:
        if x['status'] == 'ok':
            times = [int(x[k]) for k in ('planned_mono_ns', 'admitted_mono_ns', 'issued_mono_ns',
                                         'node_start_mono_ns', 'completed_mono_ns', 'observed_completion_mono_ns')]
            assert times == sorted(times), 'request clock order'
            assert int(x['total_latency_ns']) == times[4] - times[0], 'latency arithmetic'
            assert int(x['execution_ns']) == times[4] - times[3], 'latency arithmetic'
    phase_records = []
    for folder, phase in _phase_clock(raw):
        phase_records.append(phase)
        manifest = json.loads((folder / 'arrival_manifest.json').read_text())
        rr = a.read(folder / 'requests.csv')
        if config['stage'] != 'smoke' or config['family'] == 'federation':
            offsets, shift = ctl.offsets(len(rr), config['model'], config['rate'], config['arrival_seed'])
            assert manifest['planned_offsets_ns'] == offsets and manifest['phase_seconds'] == shift, 'arrival mismatch'
            assert manifest['seed'] == config['arrival_seed'], 'arrival seed mismatch'
        assert manifest['concurrency_cap'] is None, 'closed-loop cap present'
        assert [int(x['planned_mono_ns']) - manifest['base_mono_ns'] for x in rr] == \
            expected_offsets(config, len(rr)), 'arrival clock changed'
    probes = set()
    if config['family'] == 'federation':
        probes = _federation_isolation(raw, config, phase_records)
    setup = a.read(raw / 'setup/requests.csv') if (raw / 'setup/requests.csv').exists() else []
    ctl.validate_audit(raw / 'control', rows + setup, probes)
    return expected, rows


def validate_capacity(raw, root):
    """Gate a single attempt. Overload is accepted; correctness failures are not."""
    raw = Path(raw)
    root = Path(root)
    try:
        result = json.loads((raw / 'result.json').read_text())
        config = result['config']
        campaign = json.loads((root / 'campaign.json').read_text())
        assert config in matrix.slots(config['stage']), 'unknown slot'
        assert json.loads((raw / 'campaign_snapshot.json').read_text()) == campaign, 'campaign mismatch'
        assert result['status'] in ('ok', 'load_timeout'), result.get('reason') or result['status']
        assert not result.get('finalization_errors'), result['finalization_errors']
        _provenance(raw, root, campaign)
        if config['method'] == 'nc':
            for n in range(1, config['hosts'] + 1):
                assert 'stdout_mode=direct-file' in (raw / 'node-logs' / f'node{n}.startup.log').read_text(), 'logging marker missing'
        expected, rows = _schedule_and_audit(raw, config)
        completed = sum(1 for x in rows if x['status'] == 'ok')
        if result['status'] == 'ok':
            assert completed == len(expected), 'complete run has unfinished requests'
        expiry_count = 0
        if config.get('trace'):
            counts, expiries, _ = _trace_evidence(raw)
            assert counts['pilot_transport_error'] == 0, 'transport error'
            if config['method'] == 'nc':
                assert counts['pilot_tcp_connect'] > 0 and counts['pilot_tcp_reuse'] > 0, 'reuse missing'
            expiry_count = len(expiries)
        outcome = 'clean' if result['status'] == 'ok' and completed == len(expected) else 'overload'
        return dict(qualified=True, reason='ok', outcome=outcome,
                    planned=len(expected), completed=completed,
                    completion_fraction=completed / len(expected) if expected else None,
                    expiry_events=expiry_count,
                    status=result['status'], campaign=campaign['fingerprint'])
    except Exception as exc:  # noqa: BLE001 - gate must never raise
        return dict(qualified=False, reason=f'{type(exc).__name__}: {exc}')


def _resources(samples):
    cpus = []
    previous = None
    for sample in samples:
        values = list(map(int, sample['cpu'].split()[1:9]))
        total, idle = sum(values), values[3] + values[4]
        if previous and total > previous[0]:
            cpus.append(100 * (1 - (idle - previous[1]) / (total - previous[0])))
        previous = (total, idle)
    available = [int(next(l.split()[1] for l in s['meminfo'].splitlines() if l.startswith('MemAvailable:'))) * 1024
                 for s in samples]
    return dict(host_cpu_mean_percent=a.average(cpus), host_cpu_peak_percent=max(cpus, default=None),
                minimum_available_memory_bytes=min(available, default=None),
                peak_outstanding=max((s['outstanding'] for s in samples), default=0))


def record(raw, root):
    """Per-run capacity record used by capacity_analysis.aggregate."""
    raw = Path(raw)
    result = json.loads((raw / 'result.json').read_text())
    config = result['config']
    rows = measured(raw)
    ok = [r for r in rows if r['status'] == 'ok']
    phases = [json.loads(p.read_text()) for p in sorted(raw.glob('measurement-*/result.json'))]
    samples = [json.loads(line) for p in sorted(raw.glob('measurement-*/timeline.jsonl'))
               for line in p.read_text().splitlines()]
    expected = [x for x in a.read(raw / 'schedule.csv') if x['phase'] != 'setup']
    window = sum((p['last_planned_mono_ns'] - p['base_mono_ns']) / 1e9 for p in phases)
    measurement = sum((p['ended_mono_ns'] - p['base_mono_ns']) / 1e9 for p in phases)
    drain = sum(max(0, p['ended_mono_ns'] - p['last_planned_mono_ns']) / 1e9 for p in phases)
    per_node = peak(rows)
    backlog = []
    for folder, phase in _phase_clock(raw):
        rr = a.read(folder / 'requests.csv')
        manifest = json.loads((folder / 'arrival_manifest.json').read_text())
        last = (phase['last_planned_mono_ns'] - manifest['base_mono_ns']) / 1e9
        for point in a.timeline_analysis(rr, manifest['base_mono_ns'], phase['ended_mono_ns']):
            if point['second'] <= math.ceil(last):
                backlog.append((point['second'], point['issued_minus_completed']))
    resources = _resources(samples) if samples else {}
    return dict(
        slot=config['id'], condition=config['condition'], family=config['family'],
        hosts=config['hosts'], method=config['method'], model=config['model'],
        rate=config['rate'], seed=config['seed'], stage=config['stage'],
        repeats=config.get('repeats', 1), requests=len(expected), trace=bool(config.get('trace')),
        status=result['status'], outcome='clean' if result['status'] == 'ok' and len(ok) == len(expected) else 'overload',
        completion_fraction=len(ok) / len(expected) if expected else None,
        requested_window_seconds=len(expected) / config['rate'] if config['rate'] else None,
        actual_window_seconds=window, measurement_seconds=measurement, drain_seconds=drain,
        goodput=len(ok) / measurement if measurement else None,
        p50_ms=a.percentile([a.num(r, 'total_latency_ns') / 1e6 for r in ok], .5),
        p95_ms=a.percentile([a.num(r, 'total_latency_ns') / 1e6 for r in ok], .95),
        p99_ms=a.percentile([a.num(r, 'total_latency_ns') / 1e6 for r in ok], .99),
        peak_outstanding=resources.get('peak_outstanding'),
        global_peak_concurrency=peak_global(rows),
        per_node_peak_max=max(per_node.values(), default=0),
        host_cpu_mean=resources.get('host_cpu_mean_percent'),
        host_cpu_peak=resources.get('host_cpu_peak_percent'),
        minimum_available_memory_bytes=resources.get('minimum_available_memory_bytes'),
        backlog_slope=a.slope(backlog),
        total_wall_seconds=result.get('total_wall_seconds'))


def diagnose(raw, root):
    raw = Path(raw)
    dest = Path(root) / 'derived' / raw.relative_to(Path(root) / 'raw')
    dest.mkdir(parents=True, exist_ok=True)
    analysis_record = a.one(raw, dest)
    gate = validate_capacity(raw, root)
    capacity = record(raw, root)
    ctl.dump(dest / 'capacity_record.json', capacity)
    ctl.dump(dest / 'validation.json', gate)
    return dict(analysis=analysis_record, capacity=capacity, gate=gate)


def build(root):
    root = Path(root)
    selections, states = {}, {}
    for stage in ('smoke', 'pilot', 'formal'):
        selected, state = [], []
        for config in matrix.slots(stage):
            paths = sorted((root / 'raw' / stage / config['id']).glob('attempt*/result.json'))
            gate = validate_capacity(paths[-1].parent, root) if paths else dict(qualified=False, reason='not_started')
            state.append(dict(slot=config['id'], qualified=gate['qualified'], outcome=gate.get('outcome'), reason=gate['reason']))
            if gate['qualified']:
                selected.append(dict(slot=config['id'], config=config, raw=str(paths[-1].parent)))
        selections[stage], states[stage] = selected, state
    ctl.dump(root / 'derived/selection.json', selections)
    ctl.dump(root / 'derived/run_status.json', states)
    ctl.table(root / 'derived/run_status.csv', [dict(stage=stage, **x) for stage, items in states.items() for x in items])
    counts = {k: len(v) for k, v in selections.items()}
    targets = matrix.stage_targets()
    report = ['# 并发能力实验（capacity / burst）', '',
              'Status: ' + ('ANALYZED' if counts['formal'] == targets['formal'] else 'COLLECTING_NO_PARTIAL_STATS'),
              '', '| Stage | Qualified | Target |', '|---|---:|---:|']
    for stage in ('smoke', 'pilot', 'formal'):
        report.append(f'| {stage} | {counts[stage]} | {targets[stage]} |')
    report += ['', '过载 run 计入合格数据；未完成请求保留在分母。不设收敛通过阈值。']
    (root / 'derived/report.md').write_text('\n'.join(report) + '\n')
    records = [record(Path(item['raw']), root) for item in selections['formal']]
    result = dict(counts=counts, targets=targets, records=len(records))
    if records:
        result['capacity'] = ca.write_outputs(root, records)
    return result