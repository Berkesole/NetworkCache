"""Immutable capacity condition/slot enumeration. No I/O.

This campaign measures the system's *load capacity* under open arrival: how much
offered request rate it can absorb before completion degrades, queueing diverges
or the run fails to drain. It reuses the frozen 4096-request schedules; the rate
is applied by the controller at runtime, so the workload never has to be
regenerated.

Window policy: a single 4096-request pass at rate ``r`` lasts 4096/r seconds.
For high rates that is too short to observe backlog, so the measurement sequence
is repeated ``repeats`` times (with relabelled request IDs) until the arrival
window is at least ``T_MIN_SECONDS``. ``repeats`` is clamped so the request count
stays bounded.

Two experiment families:
  * ``capacity`` -- Poisson arrival ladder, the saturation sweep.
  * ``burst``    -- piecewise-burst arrival ladder, burst absorption/recovery.
"""
LAMBDA_LADDER = (32, 64, 128, 256, 512, 1024, 2048, 4096)
BURST_LADDER = (64, 128, 256)
HOSTS = (16, 32)
METHODS = ('nc', 'tcp-direct')
SEEDS = (1, 2, 3, 4, 5)

# The TCP-direct helper opens one connection per request with no client-side
# concurrency cap (by design, unchanged from slides-v5). At 4096 req/s on 16
# hosts the seed-001 pilot exhausted the helper's file descriptors
# ("OSError: [Errno 24] Too many open files"), aborting the run after 48 %.
# That is a harness ceiling, not a protocol result, so TCP Direct is capped
# here; NC has no such ceiling in the pilot and keeps the full ladder.
TCP_DIRECT_MAX_RATE = 2048

REQUESTS_PER_PASS = 4096
T_MIN_SECONDS = 30
# Cap passes so a single run stays bounded (32768 requests is already proven
# feasible and keeps the per-request control evidence manageable). The two top
# rates therefore run a shorter window and are flagged window_floor_hit.
MAX_REPEATS = 8
SIZE = 65536
CATALOG = 512
DISTRIBUTION = 'iid-zipf'

# Frozen protocol/runtime knobs, identical to the slides-v5 campaign.
QUERY_LIFETIME_SECONDS = 30
LOOP_SLEEP_MS = 25
REQUEST_TIMEOUT_ABORT = False

# Trace policy: per-node event traces are the dominant on-disk cost, and the
# capacity question is answered by the request ledger plus the 100 ms resource
# timeline. Traces stay off except for one designated mechanism subset per
# method, which keeps a mechanism-evidence sample without 8x trace volume.
TRACE_ON_CONDITIONS = frozenset({
    'capacity-n32-poisson-r128-nc',
    'capacity-n32-poisson-r128-tcp-direct',
})


def repeats_for(rate: int) -> int:
    """Passes needed so the arrival window reaches T_MIN_SECONDS."""
    passes = -(-(T_MIN_SECONDS * rate) // REQUESTS_PER_PASS)  # ceil division
    return max(1, min(MAX_REPEATS, passes))


def windows_for(rate: int) -> dict:
    repeats = repeats_for(rate)
    requests = REQUESTS_PER_PASS * repeats
    window = requests / rate
    return dict(repeats=repeats, requests=requests, window_seconds=window,
                window_floor_hit=window < T_MIN_SECONDS)


def capacity_conditions() -> list[dict]:
    out = []
    for family, ladder in (('capacity', LAMBDA_LADDER), ('burst', BURST_LADDER)):
        model = 'poisson' if family == 'capacity' else 'burst'
        for hosts in HOSTS:
            for rate in ladder:
                for method in METHODS:
                    if method == 'tcp-direct' and rate > TCP_DIRECT_MAX_RATE:
                        continue
                    ident = f'{family}-n{hosts}-{model}-r{rate}-{method}'
                    out.append(dict(
                        condition=ident, family=family, hosts=hosts, size=SIZE,
                        catalog=CATALOG, distribution=DISTRIBUTION, model=model,
                        rate=rate, method=method, stage='load',
                        query_lifetime_seconds=QUERY_LIFETIME_SECONDS,
                        loop_sleep_ms=LOOP_SLEEP_MS,
                        reuse_connections=method == 'nc',
                        request_timeout_abort=REQUEST_TIMEOUT_ABORT,
                        repeats=repeats_for(rate),
                        trace=(ident in TRACE_ON_CONDITIONS)))
    assert len(out) == 42, len(out)
    assert len({c['condition'] for c in out}) == 42
    return out


def _federation_smoke(method: str) -> dict:
    """64-host logical-federation smoke, validating the reused federation path."""
    return dict(condition=f'federation-n64-zipf-poisson-r32-{method}',
                family='federation', hosts=64, size=SIZE, catalog=CATALOG,
                distribution='zipf', model='poisson', rate=32, method=method,
                stage='smoke', query_lifetime_seconds=QUERY_LIFETIME_SECONDS,
                loop_sleep_ms=LOOP_SLEEP_MS, reuse_connections=method == 'nc',
                request_timeout_abort=REQUEST_TIMEOUT_ABORT, repeats=1,
                trace=True)


def smoke_conditions() -> list[dict]:
    cs = capacity_conditions()
    out = []
    for hosts in HOSTS:
        for method in METHODS:
            c = next(c for c in cs if c['hosts'] == hosts
                     and c['method'] == method and c['family'] == 'capacity'
                     and c['rate'] == 32)
            out.append(dict(c, stage='smoke', repeats=1, trace=True))
    for method in METHODS:
        out.append(_federation_smoke(method))
    assert len(out) == 6, len(out)
    return out


def slot(c: dict, stage: str, seed: int = 1) -> dict:
    return dict(c, stage=stage, seed=seed, arrival_seed=9172 + seed,
                id=f"{c['condition']}--s{seed:03d}")


def conditions() -> list[dict]:
    return capacity_conditions()


def slots(stage: str) -> list[dict]:
    if stage == 'smoke':
        return [slot(c, 'smoke') for c in smoke_conditions()]
    if stage == 'pilot':
        return [slot(c, 'pilot') for c in capacity_conditions()]
    if stage == 'formal':
        return [slot(c, 'formal', seed) for seed in SEEDS for c in capacity_conditions()]
    raise ValueError(stage)


def request_count(c: dict) -> int:
    """Measurement requests for one run of this condition."""
    if c['family'] == 'federation':
        return 3584
    return REQUESTS_PER_PASS * int(c.get('repeats', 1))


def stage_targets() -> dict:
    return dict(smoke=len(slots('smoke')), pilot=len(slots('pilot')),
                formal=len(slots('formal')))


if __name__ == '__main__':
    import json
    print(json.dumps(dict(stage=stage_targets(),
                          ladder=dict(capacity=list(LAMBDA_LADDER), burst=list(BURST_LADDER)),
                          windows={r: windows_for(r) for r in LAMBDA_LADDER}),
                     indent=2, sort_keys=True))