#!/usr/bin/env python3
"""Track A stage 2: v2 matrix stage, tooling, module manifest, input regeneration.

Runs on simple-station after stage 1. Adds only what Track A needs (the v2 stage; this
port has no v1 supplement stage), installs the Track B tooling (queue driver, extractor,
smoke, deploy), writes base_manifest.json with the sha256 of the six reused protocol /
metric modules that must be byte-identical across the two tracks, and launches the
input regeneration (controlled 4096, federation 11776, iid-zipf 1 KiB / 1 MiB arms).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/home/simpleai/zhengzihao/networkcache-concurrency-supplement-v1')
OLD = Path('/home/simpleai/zhengzihao/networkcache-concurrency-slides-v5')
S = ROOT / 'source'
D = S / 'experiments/concurrency_slides_v5'
G = S / 'experiments/content_discovery/delay_find_content/scripts'
WORK = S / 'experiments/content_discovery/delay_find_content/workloads'
REUSED = ['controller.py', 'network_backend.py', 'tcp_node.py', 'kathara_bridge.py',
          'analysis.py', 'start-node.sh']


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------------- 1. v2 stage in this port's matrix ----------------
m = D / 'matrix.py'
s = m.read_text()
assert 'supplement_v2' not in s
anchor = "def slot(c,stage,seed=1,rep=1):"
assert s.count(anchor) == 1, s.count(anchor)
v2 = '''def supplement_v2_conditions():
    """The 18 conditions of the dual-track v2 campaign (supplementary_slots_v2.csv).

    Conditions shared with the lineage are returned with the lineage field values, so a
    v2 slot equals its lineage counterpart and membership checks hold either way.
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

    for method in ('nc', 'tcp-direct'):
        add('controlled', 32, 65536, 512, 'controlled', method)
    for size in (1024, 65536, 1048576):
        for method in ('nc', 'tcp-direct'):
            add('size', 32, size, 512, 'iid-zipf', method, rate=32)
    for method in ('nc', 'tcp-direct'):
        add('size', 32, 65536, 512, 'iid-zipf', method)
    for method in ('nc', 'tcp-direct'):
        add('federation', 64, 65536, 512, 'zipf', method)
    for method in ('nc', 'tcp-direct'):
        add('size', 32, 65536, 512, 'iid-zipf', method, lifetime=7, suffix='-lt7s')
    for family, distribution in (('zipf', 'iid-zipf'), ('uniform', 'iid-uniform')):
        for method in ('nc', 'tcp-direct'):
            add(family, 32, 65536, 512, distribution, method)
    assert len(out) == 18 and len({c['condition'] for c in out}) == 18
    return out


ORDER_SEED_V2 = "concurrency-supplement-v2:order-v1"


'''
s = s.replace(anchor, v2 + anchor, 1)
old = " if stage=='smoke':"
assert s.count(old) == 1
new = ''' if stage=='supplement_v2':
  out=[slot(c,stage,seed,1) for c in supplement_v2_conditions() for seed in range(1,11)]
  random.Random(ORDER_SEED_V2).shuffle(out)
  assert len(out)==180 and len({c['id'] for c in out})==180
  return out
 if stage=='smoke':'''
s = s.replace(old, new, 1)
assert 'import random' in s
m.write_text(s)
print('matrix.py: supplement_v2 stage added', flush=True)

# ---------------- 2. tooling ----------------
tool = D / 'extract_supplement_v2.py'
shutil.copy2('/tmp/extract_supplement_v2.py', tool)
shutil.copy2('/tmp/supplement_queue_v2.py', D / 'supplement_queue_v2.py')
shutil.copy2('/tmp/deploy_supplement_v2_ss.py', D / 'deploy_supplement_v2_ss.py')
shutil.copy2('/tmp/smoke_anchor_v2.py', ROOT / 'recovery' / 'smoke_anchor_v2.py')
print('tooling installed', flush=True)

# ---------------- 3. module manifest ----------------
slides_campaign = json.loads((OLD / 'campaign.json').read_text())
manifest = dict(
    track='A (simple-station)', built_epoch_ns=time.time_ns(),
    base_commit=subprocess.check_output(
        ['git', '-C', str(S), 'rev-parse', 'HEAD'], text=True).strip(),
    reused_modules_sha256={n: sha(D / n) for n in REUSED},
    reused_modules_rule='these six must be byte-identical across Track A and Track B; '
                        'they are compared against the volcengine-3 v2 copy',
    changed_files=sorted(l.split()[-1] for l in subprocess.check_output(
        ['git', '-C', str(S), 'diff', '--name-only'], text=True).splitlines()),
    image_id=slides_campaign['image_id'],
    reference_root=str(OLD),
    reference_campaign=slides_campaign['fingerprint'],
)
(ROOT / 'base_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
print('base_manifest.json written:', json.dumps(manifest['reused_modules_sha256'], indent=2)[:400],
      flush=True)

# ---------------- 4. parent input sanity + regeneration ----------------
parent_iid = OLD / ('inputs/single/mapped/kua-dc-v4-32-latency-v4-bf1024-h3-c512-'
                    'iid-zipf-b65536-seed001/schedule.tsv')
rows = parent_iid.read_text().splitlines()[1:]
meas = [r for r in rows if r.split('\t')[1] != 'setup']
print('parent iid-zipf measurement rows:', len(meas), flush=True)
assert len(meas) == 4096, 'parent inputs must be the 4096-request family'

env = dict(os.environ, LATENCY_V4_IID_REQUESTS='4096', LATENCY_V4_CONTROLLED_ROUNDS='128')
log = (ROOT / 'recovery' / 'generator-latency.log').open('w')
subprocess.Popen(['python3', 'generate_latency_v4_bf1024_h3_c512.py'], cwd=G, env=env,
                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
env2 = dict(os.environ, FEDERATION_PRE_BINS='32', FEDERATION_POST_BINS='48')
log2 = (ROOT / 'recovery' / 'generator-federation.log').open('w')
subprocess.Popen(['python3', 'generate_federation_v4_bf1024_h3_formal.py'], cwd=G, env=env2,
                 stdout=log2, stderr=subprocess.STDOUT, start_new_session=True)
print('generators launched (latency-v4 then federation)', flush=True)
diff = subprocess.run(['git', '-C', str(S), 'diff'], capture_output=True, text=True).stdout
(ROOT / 'recovery' / 'trackA-changes.patch').write_text(diff)
print('STAGE2_OK patch_bytes=%d changed=%d' % (
    len(diff), len([l for l in diff.splitlines() if l.startswith('diff --git')])), flush=True)