"""Cross-seed capacity aggregation: saturation curves and knee estimates.

Input is the list of per-run capacity records produced by
``capacity_reports.record``. Output is descriptive: completion capacity,
queue-stable capacity, latency knee, and whether the host CPU was the binding
constraint. No automatic "convergence passed" claim is made.
"""
import csv
import json
import math
import statistics
from pathlib import Path

import analysis as a

# Operational definitions. They are fixed up front so the same numbers cannot be
# re-interpreted after seeing the data.
DRAIN_STABLE_SECONDS = 2.0
BACKLOG_SLOPE_STABLE = 0.05      # requests/second of (issued - completed)
LATENCY_KNEE_FACTOR = 3.0        # multiple of the lowest-rate p95
HOST_CPU_LIMITED_PERCENT = 90.0
T_TABLE_95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447,
              8: 2.365, 9: 2.306, 10: 2.262}


def mean_ci95(values):
    values = [v for v in values if v is not None]
    n = len(values)
    if n == 0:
        return dict(n=0, mean=None, ci95_low=None, ci95_high=None)
    mean = statistics.mean(values)
    if n == 1:
        return dict(n=1, mean=mean, ci95_low=None, ci95_high=None)
    half = T_TABLE_95.get(n, 1.96) * statistics.stdev(values) / math.sqrt(n)
    return dict(n=n, mean=mean, ci95_low=mean - half, ci95_high=mean + half)


def knee(xs, ys):
    """Elbow of a monotone-ish curve by two contiguous least-squares segments.

    Returns the x of the last point in the low segment (the rate just before the
    curve turns up), or ``None`` when there are too few points or splitting does
    not materially improve on a single line.
    """
    points = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(points) < 4:
        return None

    def sse(segment):
        if len(segment) < 2:
            return 0.0
        xm = statistics.mean(p[0] for p in segment)
        ym = statistics.mean(p[1] for p in segment)
        den = sum((p[0] - xm) ** 2 for p in segment)
        if den == 0:
            return 0.0
        slope = sum((p[0] - xm) * (p[1] - ym) for p in segment) / den
        intercept = ym - slope * xm
        return sum((p[1] - (slope * p[0] + intercept)) ** 2 for p in segment)

    whole = sse(points)
    best = None
    for split in range(2, len(points) - 1):
        two = sse(points[:split]) + sse(points[split:])
        if best is None or two < best[0]:
            best = (two, points[split - 1][0])
    if best is None or best[0] >= whole * 0.98:
        return None
    return best[1]


def _group_key(record):
    return (record['family'], record['hosts'], record['method'])


def _by_rate(records):
    groups = {}
    for record in records:
        groups.setdefault(_group_key(record), {}).setdefault(record['rate'], []).append(record)
    return groups


def estimate(records):
    """Per (family, hosts, method): per-rate aggregates and the three knees."""
    curves = []
    knees = []
    for (family, hosts, method), rates in sorted(_by_rate(records).items()):
        ordered = sorted(rates)
        per_rate = []
        for rate in ordered:
            runs = rates[rate]
            def col(name):
                return [run.get(name) for run in runs]
            entry = dict(
                family=family, hosts=hosts, method=method, rate=rate,
                seeds=len(runs),
                completion_fraction=mean_ci95(col('completion_fraction')),
                drain_seconds=mean_ci95(col('drain_seconds')),
                p95_ms=mean_ci95(col('p95_ms')),
                p99_ms=mean_ci95(col('p99_ms')),
                goodput=mean_ci95(col('goodput')),
                peak_outstanding=mean_ci95(col('peak_outstanding')),
                peak_concurrency=mean_ci95(col('global_peak_concurrency')),
                backlog_slope=mean_ci95(col('backlog_slope')),
                host_cpu_peak=max((run.get('host_cpu_peak') or 0) for run in runs),
                overload_runs=sum(1 for run in runs if run.get('outcome') == 'overload'),
            )
            per_rate.append(entry)
            curves.append(entry)

        baseline = next((e['p95_ms']['mean'] for e in per_rate if e['p95_ms']['mean']), None)
        done = [e['rate'] for e in per_rate
                if e['completion_fraction']['mean'] == 1.0
                and all(r.get('completion_fraction') == 1.0 for r in rates[e['rate']])]
        stable = [e['rate'] for e in per_rate
                  if e['drain_seconds']['mean'] is not None
                  and e['drain_seconds']['mean'] <= DRAIN_STABLE_SECONDS
                  and (e['backlog_slope']['mean'] is None
                       or e['backlog_slope']['mean'] <= BACKLOG_SLOPE_STABLE)]
        lat = [e['rate'] for e in per_rate
               if baseline and e['p95_ms']['mean'] and e['p95_ms']['mean'] > LATENCY_KNEE_FACTOR * baseline]
        max_cpu_below_done = max(
            (e['host_cpu_peak'] for e in per_rate if e['rate'] in done), default=0)
        knees.append(dict(
            family=family, hosts=hosts, method=method,
            lambda_done=max(done) if done else None,
            lambda_stable=max(stable) if stable else None,
            lambda_latency=min(lat) if lat else None,
            drain_knee=knee(ordered, [e['drain_seconds']['mean'] for e in per_rate]),
            latency_knee=knee(ordered, [e['p95_ms']['mean'] for e in per_rate]),
            host_limited=bool(max_cpu_below_done >= HOST_CPU_LIMITED_PERCENT),
            host_cpu_peak_below_lambda_done=max_cpu_below_done,
            definitions=dict(drain_stable_seconds=DRAIN_STABLE_SECONDS,
                             backlog_slope_stable=BACKLOG_SLOPE_STABLE,
                             latency_knee_factor=LATENCY_KNEE_FACTOR,
                             host_cpu_limited_percent=HOST_CPU_LIMITED_PERCENT),
        ))
    return curves, knees


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text('')
        return
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _flat_curve_rows(curves):
    rows = []
    for entry in curves:
        row = dict(family=entry['family'], hosts=entry['hosts'], method=entry['method'],
                   rate=entry['rate'], seeds=entry['seeds'],
                   overload_runs=entry['overload_runs'], host_cpu_peak=entry['host_cpu_peak'])
        for metric in ('completion_fraction', 'drain_seconds', 'p95_ms', 'p99_ms',
                       'goodput', 'peak_outstanding', 'peak_concurrency', 'backlog_slope'):
            for suffix in ('mean', 'ci95_low', 'ci95_high'):
                row[f'{metric}_{suffix}'] = entry[metric][suffix]
        rows.append(row)
    return rows


def _plot_curves(dest, curves, metric, label):
    for hosts in sorted({c['hosts'] for c in curves}):
        rows = []
        for rate in sorted({c['rate'] for c in curves}):
            row = {'rate': rate}
            for method in sorted({c['method'] for c in curves}):
                entry = next((c for c in curves if c['hosts'] == hosts
                              and c['method'] == method and c['rate'] == rate), None)
                row[method] = entry[metric]['mean'] if entry else None
            rows.append(row)
        methods = [(m, 'NC' if m == 'nc' else 'TCP Direct')
                   for m in sorted({c['method'] for c in curves})]
        path = dest / f'capacity-{metric}-n{hosts}.svg'
        a.plot(path, rows, 'rate', f'Capacity curve: {label} (hosts={hosts}, seed means)',
               methods)


def write_outputs(root, records):
    dest = Path(root) / 'derived' / 'capacity'
    dest.mkdir(parents=True, exist_ok=True)
    curves, knees = estimate(records)
    _write_csv(dest / 'capacity_curves.csv', _flat_curve_rows(curves))
    _write_csv(dest / 'knees.csv', knees)
    _write_csv(dest / 'runs.csv', records)
    with (dest / 'capacity.json').open('w') as handle:
        json.dump(dict(curves=curves, knees=knees, runs=len(records)), handle,
                  indent=2, sort_keys=True)
    for metric, label in (('completion_fraction', 'completion fraction'),
                          ('drain_seconds', 'drain after last arrival (s)'),
                          ('p95_ms', 'p95 user latency (ms)'),
                          ('backlog_slope', 'steady backlog slope (req/s)')):
        _plot_curves(dest, curves, metric, label)
    lines = ['# 并发能力实验：容量与拐点', '',
             '描述性结果；过载 run 保留为数据点。三个拐点按预先固定的口径计算。', '',
             '| 家族 | 节点 | 方法 | λ_done | λ_stable | λ_latency | drain 拐点 | 时延拐点 | 主机受限 |',
             '|---|---:|---|---:|---:|---:|---:|---:|---|']
    for item in knees:
        def fmt(v):
            return '—' if v is None else str(v)
        lines.append('| {family} | {hosts} | {method} | {a} | {b} | {c} | {d} | {e} | {f} |'.format(
            family=item['family'], hosts=item['hosts'], method=item['method'],
            a=fmt(item['lambda_done']), b=fmt(item['lambda_stable']),
            c=fmt(item['lambda_latency']), d=fmt(item['drain_knee']),
            e=fmt(item['latency_knee']), f='yes' if item['host_limited'] else 'no'))
    lines += ['', '口径：λ_done=所有 seed 完成率 100% 的最大 λ；λ_stable=drain ≤ {0}s 且积压斜率 ≤ {1} req/s；'
              'λ_latency=p95 超过最低速率基线 {2}× 的最小 λ。'.format(
                  DRAIN_STABLE_SECONDS, BACKLOG_SLOPE_STABLE, LATENCY_KNEE_FACTOR),
              '主机受限=λ_done 以内主机 CPU 峰值 ≥ {0}%；此时不把该点解释为协议上限。'.format(
                  HOST_CPU_LIMITED_PERCENT),
              '未完成/未发出请求保留在分母；不发布收敛通过声明。']
    (dest / 'CAPACITY_RESULTS.md').write_text('\n'.join(lines) + '\n')
    return dict(curves=len(curves), knees=len(knees), destination=str(dest))