#!/usr/bin/env python3
"""Freeze the supplementary campaign root from the frozen slides-v5 root.

Mirrors the parent recipe (deploy_backend.py) but, instead of staging new module
files, it freezes what is already committed on codex/concurrency-supplement-v1 and
records the supplement matrix (72 conditions incl. the 120-slot stage), the new
timeout policy with the per-slot lifetime override, and the installed inputs.

The parent root is only read.
"""
import hashlib
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path('/data/networkcache-concurrency-supplement-v1')
OLD = pathlib.Path('/data/networkcache-concurrency-slides-v5')
SRC = ROOT / 'source'
HERE = pathlib.Path(__file__).resolve().parent
BRANCH = 'codex/concurrency-supplement-v1'
PARENT_FINGERPRINT = '590ea5ac58f060794980748917929daeb5b1b8bd54cf12379182addf3dcc6537'


def out(a):
    return subprocess.check_output(a, text=True).strip()


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def dump(p, x):
    pathlib.Path(p).write_text(json.dumps(x, sort_keys=True, indent=2) + '\n')


def logged(a, p, **kw):
    with open(p, 'w') as h:
        subprocess.run(a, stdout=h, stderr=subprocess.STDOUT, check=True, **kw)


assert socket.gethostname() == 'simpleifz', socket.gethostname()
assert not out(['docker', 'ps', '-q']), 'worker not idle'
parent = json.loads((OLD / 'campaign.json').read_text())
assert parent['fingerprint'] == PARENT_FINGERPRINT, parent['fingerprint']
for n, d in parent['sources'].items():
    assert sha(OLD / 'source' / n) == d, n
for n, d in parent['inputs'].items():
    assert sha(OLD / 'inputs' / n) == d, n
assert out(['docker', 'image', 'inspect', '--format', '{{.Id}}',
            'rsync-router:concurrency-slides-v5']) == parent['image_id']
plugin = json.loads(out(['docker', 'plugin', 'inspect', 'kathara/katharanp:amd64']))[0]
assert plugin['Enabled']
assert (ROOT / 'parent_campaign.json').is_file()
assert out(['git', '-C', str(SRC), 'branch', '--show-current']) == BRANCH
assert out(['git', '-C', str(SRC), 'rev-parse', 'HEAD']) != parent['commit'], 'branch must be new'
print('parent checks passed; freezing', ROOT, flush=True)

# ---- campaign-local kathara wrapper and configuration ----
shutil.copy2(HERE / 'kathara_bridge.py', ROOT / 'bin/kathara')
(ROOT / 'bin/kathara').chmod(0o755)
global_config = pathlib.Path('/root/.config/kathara.conf')
config = json.loads(global_config.read_text())
assert config['network_plugin'] in ('kathara/katharanp', 'kathara/katharanp_vde')
config.update(network_plugin='kathara/katharanp', last_checked=time.time())
dump(ROOT / 'kathara.conf', config)

backend = dict(plugin_name='kathara/katharanp:amd64', plugin_id=plugin['Id'],
               plugin_reference=plugin['PluginReference'],
               plugin_rootfs=plugin['Config']['rootfs'],
               config_sha256=sha(ROOT / 'kathara.conf'),
               global_config_path=str(global_config),
               global_config_sha256=sha(global_config),
               entrypoint_sha256=sha(ROOT / 'bin/kathara'),
               implementation='official Linux bridge/veth, not VDE/TAP',
               scope='this campaign only, only point-to-point LANs',
               auto_plugin_upgrade=False,
               offload_policy='gso/gro/tso disabled on lab veth pairs before application requests',
               upstream_artifacts=json.loads(pathlib.Path(
                   '/data/networkcache-bridge-plugin-official-20260915/provenance.json').read_text()),
               kernel=out(['uname', '-r']),
               physical_parameters=dict(mtu=1500, delay_ms=2, rate_mbit=1000,
                                        netem_queue_packets=1000))
dump(ROOT / 'backend.json', backend)
print('backend frozen', flush=True)

# ---- python unit tests ----
logged(['python3', '-m', 'unittest', 'discover', '-s', str(HERE), '-p', 'test*.py'],
       ROOT / 'python-tests.log')
print((ROOT / 'python-tests.log').read_text().strip()[-400:], flush=True)

# ---- runtime regression binaries (same image as the parent) ----
targets = ['test_core', 'test_packet_codec', 'test_query_table_fallback',
           'test_node_staged_fallback', 'test_runtime_neighbor', 'test_tcp_reuse',
           'test_probe_control']
for t in targets:
    logged(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', '/app/build/' + t,
            'rsync-router:concurrency-slides-v5'], ROOT / (t + '.log'), timeout=600)
    print('RUNTIME_TEST_OK', t, flush=True)

sys.path.insert(0, str(HERE))
import matrix  # noqa: E402
import pilot  # noqa: E402

files = set(out(['git', '-C', str(SRC), 'ls-files']).splitlines()) | set(parent['sources'])
inputs = {str(p.relative_to(ROOT / 'inputs')): sha(p)
          for p in sorted((ROOT / 'inputs').rglob('*')) if p.is_file()}
record = dict(parent)
record.pop('fingerprint')
record.update(
    parent_campaign=parent['fingerprint'],
    branch=BRANCH,
    commit=out(['git', '-C', str(SRC), 'rev-parse', 'HEAD']),
    matrix=matrix.conditions(),
    assignments={s: matrix.slots(s) for s in ('smoke', 'pilot', 'formal', 'supplement')},
    sources={n: sha(SRC / n) for n in sorted(files) if (SRC / n).is_file()},
    inputs=inputs,
    timeout_policy=pilot.timeout_policy(),
    network_backend=backend,
    backend_sha256=sha(ROOT / 'backend.json'),
    epoch_ns=time.time_ns(),
    evidence='SUPPLEMENT_OPEN_ARRIVAL',
    source_change_scope=('supplement only: controlled schedule extended to 4096 requests '
                         '(128 rounds x 32 focus keys), iid-zipf 1 KiB / 1 MiB arms, '
                         'federation pre 32 bins -> warm-up gate -> activation -> post 48 bins, '
                         'per-slot query lifetime override, parallel supplement queue; '
                         'all protocol, TCP, Node, QueryTable, UnionWindow and application C++ '
                         'unchanged'),
    logging_policy=dict(parent['logging_policy'],
                        startup_sha256=sha(SRC / 'kathara/ruikekkk/shared/start-node.sh')),
    supplement=dict(
        conditions=matrix.supplement_conditions(),
        slots_file='vldb/exp/supplementary_slots.csv',
        order_seed=matrix.ORDER_SEED,
        expected_slots=len(matrix.slots('supplement')),
        sets=dict(controlled_4096=20, size_arms=60, federation_gate=20, lifetime_7s=20),
        query_lifetime_overrides=list(pilot.timeout_policy()['protocol_query_lifetime_overrides']),
        warmup_gate='both domains: OLS slope of the last 4 pre bins, 95% t CI (df=2) must contain 0',
        extraction='vldb/exp/open-arrival-concurrency/extract_slides_v5.py'),
)
record['fingerprint'] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
dump(ROOT / 'campaign.json', record)
dump(ROOT / 'recovery/complete.json',
     dict(cleanup_complete=True, reason='supplement freeze complete', campaign=record['fingerprint']))
print(json.dumps(dict(status='FROZEN_NOT_STARTED', campaign=record['fingerprint'],
                      image=record['image_id'], commit=record['commit'],
                      conditions=len(record['matrix']),
                      supplement_slots=len(record['assignments']['supplement']),
                      inputs=len(record['inputs'])), indent=2), flush=True)