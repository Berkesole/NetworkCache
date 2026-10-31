#!/usr/bin/env python3
"""Track A: install the regenerated inputs into the new root's inputs/ and verify.

The new root starts without inputs, so the frozen parent inputs are copied first and the
regenerated files are merged over them (same-named files replaced, extras kept). The
invariants that must hold: controlled 4096, federation 11776 (pre 4096 / post 7680),
iid-zipf at 1 KiB and 1 MiB present, and every untouched family byte-identical to the
parent.
"""
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path('/home/simpleai/zhengzihao/networkcache-concurrency-supplement-v1')
OLD = Path('/home/simpleai/zhengzihao/networkcache-concurrency-slides-v5')
W = ROOT / 'source/experiments/content_discovery/delay_find_content/workloads'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


single = ROOT / 'inputs/single'
federation = ROOT / 'inputs/federation'
if not (ROOT / 'inputs').is_dir():
    shutil.copytree(OLD / 'inputs', ROOT / 'inputs')
    print('copied the frozen parent inputs', flush=True)
for sub in ('canonical', 'mapped', 'scenarios'):
    src = W / 'latency-v4-bf1024-h3-c512' / sub
    if src.is_dir():
        shutil.copytree(src, single / sub, dirs_exist_ok=True)
for sub in ('mapped', 'scenarios'):
    src = W / 'federation-v4-bf1024-h3-formal' / sub
    if src.is_dir():
        shutil.copytree(src, federation / sub, dirs_exist_ok=True)
print('installed regenerated inputs', flush=True)

fail = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else ' ' + str(detail)))
    if not ok:
        fail.append(name)


def phases(path):
    rows = [l.split('\t') for l in Path(path).read_text().splitlines()[1:]]
    return rows


c = single / 'mapped/kua-dc-v4-32-latency-v4-bf1024-h3-c512-controlled-b65536-seed001/schedule.tsv'
rows = phases(c) if c.is_file() else []
check('controlled_4096', len([r for r in rows if r[1] != 'setup']) == 4096,
      len([r for r in rows if r[1] != 'setup']))
check('controlled_bins_128', len({r[3] for r in rows if r[1] != 'setup'}) == 128,
      len({r[3] for r in rows if r[1] != 'setup'}))

f = federation / 'mapped/kua-dc-v4-federation-v4-bf1024-h3-formal-catalog512-zipf-seed001/schedule.tsv'
frows = phases(f) if f.is_file() else []
pre = len([r for r in frows if r[1] == 'pre'])
post = len([r for r in frows if r[1] == 'post'])
check('federation_11776', pre + post == 11776, (pre, post))
check('federation_pre4096_post7680', (pre, post) == (4096, 7680), (pre, post))

for size in (1024, 1048576):
    d = single / f'mapped/kua-dc-v4-32-latency-v4-bf1024-h3-c512-iid-zipf-b{size}-seed001'
    check(f'new_size_arm_b{size}', d.is_dir())

for fam in ('kua-dc-v4-32-latency-v4-bf1024-h3-c512-iid-zipf-b65536',
            'kua-dc-v4-32-latency-v4-bf1024-h3-c512-iid-uniform-b65536',
            'kua-dc-v4-16-latency-v4-bf1024-h3-c512-iid-zipf-b65536'):
    same = True
    for seed in range(1, 11):
        a = single / 'mapped' / f'{fam}-seed{seed:03d}' / 'schedule.tsv'
        b = OLD / 'inputs/single/mapped' / f'{fam}-seed{seed:03d}' / 'schedule.tsv'
        if not (a.is_file() and b.is_file() and sha(a) == sha(b)):
            same = False
            break
    check('unchanged_' + fam.split('h3-c512-')[1], same)

counts = {}
for k, pat in (('single_mapped', single / 'mapped'), ('federation_mapped', federation / 'mapped')):
    counts[k] = len(list(pat.glob('*/'))) if pat.is_dir() else 0
print('counts:', counts)
print('FAILURES:', len(fail), fail)
raise SystemExit(1 if fail else 0)