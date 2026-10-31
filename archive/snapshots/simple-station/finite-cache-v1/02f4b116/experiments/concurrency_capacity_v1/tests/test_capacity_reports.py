import csv
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SUITE = HERE.parent
sys.path.insert(0, str(SUITE))
import capacity_matrix as mat  # noqa: E402
import capacity_reports as reports  # noqa: E402

SCHEDULE_FIELDS = ('request_index', 'phase', 'bin', 'round', 'key', 'requester', 'owner',
                   'value_size_bytes', 'expected_fnv1a64', 'schedule_sha256')


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_raw(root, config, status='ok'):
    raw = pathlib.Path(root) / 'raw' / config['stage'] / config['id'] / 'attempt001'
    raw.mkdir(parents=True)
    campaign = {'fingerprint': 'x', 'image_id': 'sha256:deadbeef'}
    (pathlib.Path(root) / 'campaign.json').write_text(json.dumps(campaign))
    (raw / 'campaign_snapshot.json').write_text(json.dumps(campaign))
    (raw / 'result.json').write_text(json.dumps(dict(
        config=config, status=status, total_wall_seconds=12.0, timing={},
        finalization_errors={})))
    base = 1_000_000_000
    rows = []
    for i in range(4):
        planned = base + i * 1_000_000_000
        rows.append(dict(request_index=str(i + 1), phase='measurement', bin='1', round='0',
                         key=f'key{i}', requester='1', owner='1', value_size_bytes='65536',
                         expected_fnv1a64=f'{i:016x}', schedule_sha256='sched',
                         status='ok', planned_mono_ns=str(planned),
                         admitted_mono_ns=str(planned + 1), issued_mono_ns=str(planned + 2),
                         node_start_mono_ns=str(planned + 100_000_000),
                         completed_mono_ns=str(planned + 200_000_000),
                         observed_completion_mono_ns=str(planned + 200_000_000),
                         total_latency_ns=str(200_000_000), execution_ns=str(100_000_000)))
    write_csv(raw / 'schedule.csv', [{k: r[k] for k in SCHEDULE_FIELDS} for r in rows])
    phase = raw / 'measurement-1'
    phase.mkdir(parents=True)
    write_csv(phase / 'requests.csv', rows)
    (phase / 'result.json').write_text(json.dumps(dict(
        status='ok', base_mono_ns=base, ended_mono_ns=base + 4_500_000_000,
        last_planned_mono_ns=base + 3_000_000_000)))
    (phase / 'arrival_manifest.json').write_text(json.dumps(dict(base_mono_ns=base)))
    samples = [
        dict(mono_ns=base, outstanding=0, cpu='cpu  100 0 100 800 0 0 0 0 0 0',
             meminfo='MemTotal: 1000 kB\nMemAvailable: 500 kB'),
        dict(mono_ns=base + 1_000_000_000, outstanding=2, cpu='cpu  200 0 200 1400 0 0 0 0 0 0',
             meminfo='MemTotal: 1000 kB\nMemAvailable: 400 kB'),
    ]
    (phase / 'timeline.jsonl').write_text('\n'.join(json.dumps(s) for s in samples) + '\n')
    return raw


class BaseManifestTest(unittest.TestCase):
    def test_copied_modules_unchanged(self):
        manifest = json.loads((SUITE / 'base_manifest.json').read_text())
        for name, expected in manifest.items():
            if name == 'note':
                continue
            actual = hashlib.sha256((SUITE / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, name)


class OverlapTest(unittest.TestCase):
    def test_peak_and_global(self):
        rows = [dict(requester='1', node_start_mono_ns='0', completed_mono_ns='10'),
                dict(requester='1', node_start_mono_ns='5', completed_mono_ns='15'),
                dict(requester='2', node_start_mono_ns='6', completed_mono_ns='7')]
        self.assertEqual(reports.peak(rows), {'1': 2, '2': 1})
        self.assertEqual(reports.peak_global(rows), 3)


class RecordTest(unittest.TestCase):
    def test_record_metrics(self):
        config = mat.slots('pilot')[0]
        with tempfile.TemporaryDirectory() as tmp:
            raw = build_raw(tmp, config)
            result = reports.record(raw, tmp)
            self.assertEqual(result['requests'], 4)
            self.assertEqual(result['completion_fraction'], 1.0)
            self.assertEqual(result['outcome'], 'clean')
            self.assertAlmostEqual(result['p95_ms'], 200.0)
            self.assertEqual(result['global_peak_concurrency'], 1)
            self.assertAlmostEqual(result['drain_seconds'], 1.5)
            self.assertIsNotNone(result['host_cpu_peak'])
            self.assertGreaterEqual(result['host_cpu_peak'], 0)

    def test_record_counts_incomplete_in_denominator(self):
        config = mat.slots('pilot')[0]
        with tempfile.TemporaryDirectory() as tmp:
            raw = build_raw(tmp, config, status='load_timeout')
            with (raw / 'measurement-1/requests.csv').open() as handle:
                ledger = list(csv.DictReader(handle))
            ledger[1]['status'] = 'incomplete'
            ledger[1]['completed_mono_ns'] = ''
            write_csv(raw / 'measurement-1/requests.csv', ledger)
            result = reports.record(raw, tmp)
            self.assertEqual(result['completion_fraction'], 0.75)
            self.assertEqual(result['outcome'], 'overload')


class OffsetsTest(unittest.TestCase):
    def test_smoke_uses_fixed_ladder(self):
        smoke = mat.slots('smoke')[0]
        self.assertEqual(smoke['family'], 'capacity')
        self.assertEqual(reports.expected_offsets(smoke, 4),
                         [0, 1_000_000, 2_000_000, 3_000_000])

    def test_pilot_uses_open_arrival(self):
        import controller as ctl
        slot = mat.slots('pilot')[0]
        self.assertEqual(reports.expected_offsets(slot, 8),
                         ctl.offsets(8, slot['model'], slot['rate'], slot['arrival_seed'])[0])

    def test_federation_smoke_uses_open_arrival(self):
        import controller as ctl
        fed = next(s for s in mat.slots('smoke') if s['family'] == 'federation')
        self.assertEqual(reports.expected_offsets(fed, 4),
                         ctl.offsets(4, fed['model'], fed['rate'], fed['arrival_seed'])[0])


class FederationAuditTest(unittest.TestCase):
    """Regression: federation probe IDs must be excluded from the delivery audit."""

    def _fixture(self, root):
        import controller as ctl
        config = next(s for s in mat.slots('smoke') if s['family'] == 'federation'
                      and s['method'] == 'tcp-direct')
        raw = pathlib.Path(root) / 'raw' / 'smoke' / config['id'] / 'attempt001'
        control = raw / 'control'
        for name in ('fatal', 'delivery-audit', 'completions'):
            (control / name).mkdir(parents=True, exist_ok=True)
        bases = {1: 1_000_000_000, 2: 500_000_500}
        counts = {1: 2, 2: 1}
        all_rows = []
        for index, count in counts.items():
            offsets, _ = ctl.offsets(count, config['model'], config['rate'], config['arrival_seed'])
            base = bases[index]
            subset = []
            for i in range(count):
                planned = base + offsets[i]
                subset.append(dict(
                    request_index=str(10 + len(all_rows) + i),
                    phase='pre' if index == 1 else 'post', bin='1', round='0',
                    key=f'k{i}', requester='1', owner='1', value_size_bytes='65536',
                    expected_fnv1a64='h', schedule_sha256='s', status='ok',
                    planned_mono_ns=str(planned), admitted_mono_ns=str(planned + 1),
                    issued_mono_ns=str(planned + 2), node_start_mono_ns=str(planned + 100),
                    completed_mono_ns=str(planned + 200),
                    observed_completion_mono_ns=str(planned + 200),
                    total_latency_ns='200', execution_ns='100'))
            all_rows += subset
            folder = raw / f'measurement-{index}'
            folder.mkdir(parents=True)
            write_csv(folder / 'requests.csv', subset)
            (folder / 'arrival_manifest.json').write_text(json.dumps(dict(
                base_mono_ns=base, planned_offsets_ns=offsets, phase_seconds=0.0,
                seed=config['arrival_seed'], concurrency_cap=None)))
            phase = dict(status='ok', base_mono_ns=base, ended_mono_ns=base + 400,
                         last_planned_mono_ns=base + offsets[-1])
            if index == 1:
                phase['last_completion_mono_ns'] = 100
            (folder / 'result.json').write_text(json.dumps(phase))
        write_csv(raw / 'schedule.csv', [{k: r[k] for k in SCHEDULE_FIELDS} for r in all_rows])
        failed = ['p', 'tcp-direct', 'key', 'io_error', '0', 'x', '0', '0', '0', '0', '0', '0']
        (raw / 'isolation.json').write_text(json.dumps(dict(
            status='ok', lower_mono_ns=200, upper_mono_ns=300, starts={},
            probes=[dict(request_index='900000001', failed_completion=failed),
                    dict(request_index='900000002', failed_completion=failed)])))
        for probe in ('900000001', '900000002'):
            (control / 'completions' / f'{probe}.tsv').write_text('\t'.join(failed) + '\n')
        (raw / 'activation.json').write_text(json.dumps(dict(
            issued_mono_ns=400, ack_mono_ns=[400, 410], post_ready_mono_ns=500000410)))
        return config, raw

    def test_probes_excluded_from_audit(self):
        captured = {}
        original = reports.ctl.validate_audit
        reports.ctl.validate_audit = lambda control, rows, probes=(): captured.update(probes=set(probes))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config, raw = self._fixture(tmp)
                _, rows = reports._schedule_and_audit(raw, config)
                self.assertEqual(captured['probes'], {'900000001', '900000002'})
                self.assertEqual(len(rows), 3)
        finally:
            reports.ctl.validate_audit = original


class GateTest(unittest.TestCase):
    def test_missing_attempt_is_unqualified(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = reports.validate_capacity(pathlib.Path(tmp) / 'nope', tmp)
            self.assertFalse(gate['qualified'])
            self.assertIn('reason', gate)

    def test_correctness_failure_rejected(self):
        config = mat.slots('pilot')[0]
        with tempfile.TemporaryDirectory() as tmp:
            raw = build_raw(tmp, config, status='correctness_failure')
            gate = reports.validate_capacity(raw, tmp)
            self.assertFalse(gate['qualified'])

    def test_unknown_slot_rejected(self):
        config = dict(mat.slots('pilot')[0], condition='not-a-condition', id='not-a-condition--s001')
        with tempfile.TemporaryDirectory() as tmp:
            raw = build_raw(tmp, config)
            gate = reports.validate_capacity(raw, tmp)
            self.assertFalse(gate['qualified'])


if __name__ == '__main__':
    unittest.main()