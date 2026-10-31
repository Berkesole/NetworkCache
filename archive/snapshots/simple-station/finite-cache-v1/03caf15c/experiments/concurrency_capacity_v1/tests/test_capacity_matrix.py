import json
import pathlib
import unittest

import capacity_matrix as mat

HERE = pathlib.Path(__file__).resolve().parent


class MatrixTest(unittest.TestCase):
    def test_stage_targets(self):
        # nc: 8 rates; tcp-direct: 7 rates (capped at 2048); x 2 hosts; + 12 burst.
        self.assertEqual(mat.stage_targets(), dict(smoke=6, pilot=42, formal=210))

    def test_conditions_unique_and_complete(self):
        cs = mat.conditions()
        self.assertEqual(len(cs), 42)
        self.assertEqual(len({c['condition'] for c in cs}), 42)
        self.assertEqual(sum(1 for c in cs if c['family'] == 'capacity'), 30)
        self.assertEqual(sum(1 for c in cs if c['family'] == 'burst'), 12)
        for c in cs:
            self.assertEqual(c['hosts'] in mat.HOSTS, True)
            self.assertEqual(c['method'] in mat.METHODS, True)

    def test_tcp_direct_is_capped_at_the_harness_ceiling(self):
        cs = mat.conditions()
        nc_rates = {c['rate'] for c in cs if c['method'] == 'nc'}
        tcp_rates = {c['rate'] for c in cs if c['method'] == 'tcp-direct'}
        self.assertIn(4096, nc_rates)
        self.assertNotIn(4096, tcp_rates)
        self.assertEqual(max(tcp_rates), mat.TCP_DIRECT_MAX_RATE)

    def test_window_policy(self):
        expected = {32: 1, 64: 1, 128: 1, 256: 2, 512: 4, 1024: 8, 2048: 8, 4096: 8}
        got = {rate: mat.repeats_for(rate) for rate in mat.LAMBDA_LADDER}
        self.assertEqual(got, expected)
        for rate in mat.LAMBDA_LADDER:
            window = mat.windows_for(rate)
            self.assertLessEqual(window['requests'], mat.REQUESTS_PER_PASS * mat.MAX_REPEATS)
            should_floor = rate >= 2048
            self.assertEqual(window['window_floor_hit'], should_floor, rate)

    def test_smoke_includes_federation(self):
        smoke = mat.slots('smoke')
        self.assertEqual(len(smoke), 6)
        families = {s['family'] for s in smoke}
        self.assertEqual(families, {'capacity', 'federation'})

    def test_formal_is_five_seeds(self):
        formal = mat.slots('formal')
        seeds = sorted({s['seed'] for s in formal})
        self.assertEqual(seeds, [1, 2, 3, 4, 5])
        self.assertEqual(len(formal), 210)
        self.assertEqual(len({s['id'] for s in formal}), 210)

    def test_request_count(self):
        for c in mat.conditions():
            self.assertEqual(mat.request_count(c), 4096 * c['repeats'])
        fed = next(s for s in mat.slots('smoke') if s['family'] == 'federation')
        self.assertEqual(mat.request_count(fed), 3584)

    def test_trace_subset(self):
        traced = [c for c in mat.conditions() if c['trace']]
        self.assertEqual({c['condition'] for c in traced}, set(mat.TRACE_ON_CONDITIONS))
        self.assertEqual({c['method'] for c in traced}, {'nc', 'tcp-direct'})


if __name__ == '__main__':
    unittest.main()