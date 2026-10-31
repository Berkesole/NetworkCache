#!/usr/bin/env python3
"""Add the dual-track v2 slot set to the supplement matrix (minimal diff).

V2 blocks (supplementary_slots_v2.csv, 180 slots = 140 required + 40 now-confirmed
4.1-opt):
  4.1     controlled n32 r128                        20   (unchanged condition)
  4.2a    size n32 b{1024,65536,1048576} r32         60   R1: all three sizes at lambda=32
  4.2b    size n32 b65536 r128                       20   R1 anchor, same condition as v1's arm
  4.4     federation n64 zipf r128                   20   (unchanged condition, per-class rows)
  E       size n32 b65536 r128 -lt7s                 20   lifetime 7 s, same scenario as 4.2b
  4.1-opt zipf/uniform n32 r128                       40   condition already in the lineage matrix

Nothing in the lineage or v1 helpers changes; only new helpers plus a new stage.
"""
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text()
assert "def supplement_v2_conditions" not in s, "already patched"

anchor = "def slot(c, stage, seed=1, rep=1):"
assert s.count(anchor) == 1
addition = '''def supplement_v2_conditions():
    """The 18 conditions of the dual-track v2 campaign (supplementary_slots_v2.csv).

    Conditions shared with the lineage (controlled n32, federation n64, zipf/uniform
    n32) are returned with the lineage field values, so a v2 slot equals its lineage
    counterpart and reports.validate's membership check holds for either object.
    """
    lineage = {c['condition']: c for c in conditions()}
    out = []

    def add(family, hosts, size, catalog, distribution, method, rate=128, lifetime=30,
            suffix=''):
        ident = (f'{family}-n{hosts}-b{size}-c{catalog}-{distribution}-poisson-r{rate}-'
                 f'{method}{suffix}')
        shared = lineage.get(ident)
        if shared is not None:
            out.append(shared)
            return
        out.append(dict(condition=ident, family=family, hosts=hosts, size=size,
                        catalog=catalog, distribution=distribution, model='poisson',
                        rate=rate, method=method, stage='load',
                        query_lifetime_seconds=lifetime, loop_sleep_ms=25,
                        reuse_connections=method == 'nc', request_timeout_abort=False))

    for method in ('nc', 'tcp-direct'):                        # 4.1
        add('controlled', 32, 65536, 512, 'controlled', method)
    for size in (1024, 65536, 1048576):                        # 4.2a (R1: lambda=32)
        for method in ('nc', 'tcp-direct'):
            add('size', 32, size, 512, 'iid-zipf', method, rate=32)
    for method in ('nc', 'tcp-direct'):                        # 4.2b anchor
        add('size', 32, 65536, 512, 'iid-zipf', method)
    for method in ('nc', 'tcp-direct'):                        # 4.4
        add('federation', 64, 65536, 512, 'zipf', method)
    for method in ('nc', 'tcp-direct'):                        # E (size family + -lt7s)
        add('size', 32, 65536, 512, 'iid-zipf', method, lifetime=7, suffix='-lt7s')
    for family, distribution in (('zipf', 'iid-zipf'), ('uniform', 'iid-uniform')):
        for method in ('nc', 'tcp-direct'):                    # 4.1-opt
            add(family, 32, 65536, 512, distribution, method)
    assert len(out) == 18 and len({c['condition'] for c in out}) == 18
    return out


ORDER_SEED_V2 = "concurrency-supplement-v2:order-v1"


'''
s = s.replace(anchor, addition + anchor, 1)

old = "    if stage == 'supplement':"
assert s.count(old) == 1
new = '''    if stage == 'supplement_v2':
        v2 = supplement_v2_conditions()
        out = [slot(c, stage, seed, 1) for c in v2 for seed in SEEDS]
        random.Random(ORDER_SEED_V2).shuffle(out)
        assert len(out) == 180 and len({s['id'] for s in out}) == 180
        assert len({(s['condition'], s['seed']) for s in out}) == 180
        return out
    if stage == 'supplement':'''
s = s.replace(old, new, 1)
p.write_text(s)
print("patched", p.name)