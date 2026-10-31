import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import capacity_analysis as ca  # noqa: E402


def run(rate, completion=1.0, drain=0.1, p95=10.0, backlog=0.0, cpu=30.0, outcome='clean', seed=1):
    return dict(family='capacity', hosts=32, method='nc', model='poisson', rate=rate,
                seed=seed, completion_fraction=completion, drain_seconds=drain,
                p95_ms=p95, p99_ms=p95 * 1.2, goodput=rate * completion,
                peak_outstanding=1, global_peak_concurrency=2,
                backlog_slope=backlog, host_cpu_peak=cpu, outcome=outcome)


class MetricsTest(unittest.TestCase):
    def test_mean_ci95(self):
        self.assertEqual(ca.mean_ci95([])['n'], 0)
        self.assertEqual(ca.mean_ci95([5.0])['mean'], 5.0)
        result = ca.mean_ci95([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(result['n'], 5)
        self.assertLess(result['ci95_low'], result['mean'])
        self.assertGreater(result['ci95_high'], result['mean'])

    def test_knee_on_clear_elbow(self):
        # flat through 80, then a straight rise: the low segment ends at 80.
        xs = [10, 20, 40, 80, 160, 320]
        ys = [1.0, 1.0, 1.0, 1.0, 5.0, 9.0]
        self.assertEqual(ca.knee(xs, ys), 80)

    def test_knee_none_for_straight_line(self):
        self.assertIsNone(ca.knee([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]))

    def test_knee_needs_enough_points(self):
        self.assertIsNone(ca.knee([1, 2], [1, 2]))
        self.assertIsNone(ca.knee([1, 2, 3], [1, 2, 3]))

    def test_estimate_capacities(self):
        records = []
        for seed in (1, 2):
            for rate, drain, completion, backlog in ((32, 0.1, 1.0, 0.0), (64, 0.2, 1.0, 0.0),
                                                     (128, 0.5, 1.0, 0.0), (256, 5.0, 1.0, 0.2),
                                                     (512, 20.0, 0.5, 1.0)):
                records.append(run(rate, completion=completion, drain=drain,
                                   p95=10.0 * (rate / 32), backlog=backlog, seed=seed))
        curves, knees = ca.estimate(records)
        self.assertEqual(len(knees), 1)
        knee = knees[0]
        self.assertEqual(knee['lambda_done'], 256)
        self.assertEqual(knee['lambda_stable'], 128)
        self.assertEqual(knee['lambda_latency'], 128)  # p95 3x baseline at 128
        self.assertFalse(knee['host_limited'])

    def test_host_limited_flag(self):
        records = [run(32, cpu=95.0), run(64, cpu=97.0), run(128, cpu=99.0)]
        _, knees = ca.estimate(records)
        self.assertTrue(knees[0]['host_limited'])

    def test_write_outputs(self):
        records = [run(rate) for rate in (32, 64, 128, 256)]
        with tempfile.TemporaryDirectory() as tmp:
            result = ca.write_outputs(tmp, records)
            dest = pathlib.Path(tmp) / 'derived' / 'capacity'
            for name in ('capacity_curves.csv', 'knees.csv', 'runs.csv',
                         'capacity.json', 'CAPACITY_RESULTS.md'):
                self.assertTrue((dest / name).is_file(), name)
            self.assertEqual(result['knees'], 1)


if __name__ == '__main__':
    unittest.main()