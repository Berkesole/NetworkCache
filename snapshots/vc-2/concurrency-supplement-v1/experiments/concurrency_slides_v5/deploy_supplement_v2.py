#!/usr/bin/env python3
"""Track B: freeze /data/networkcache-concurrency-supplement-v2 from the v1 root.

The v2 root carries the reworked runs (R1 lambda=32 size arms, R2 quiet <=2-slot
concurrency, R3 per-class federation rows). It is a NEW root: the v1 evidence under
/data/networkcache-concurrency-supplement-v1 is only read, never written. The source
is cloned from the v1 source tree at the new commit, and the frozen slides-v5 root is
used only for the image/plugin/baseline provenance checks.
"""
import hashlib
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path('/data/networkcache-concurrency-supplement-v2')
V1 = pathlib.Path('/data/networkcache-concurrency-supplement-v1')
OLD = pathlib.Path('/data/networkcache-concurrency-slides-v5')
SRC = ROOT / 'source'
HERE = pathlib.Path(__file__).resolve().parent
BRANCH = 'codex/concurrency-supplement-v1'
SLIDES_FINGERPRINT = '590ea5ac58f060794980748917929daeb5b1b8bd54cf12379182addf3dcc6537'


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
assert V1.is_dir(), 'v1 root must exist'
assert not ROOT.exists(), 'v2 root must not exist yet'
slides = json.loads((OLD / 'campaign.json').read_text())
assert slides['fingerprint'] == SLIDES_FINGERPRINT
new_img = out(['docker', 'image', 'inspect', '--format', '{{.Id}}',
               'rsync-router:concurrency-slides-v5'])
assert new_img == slides['image_id'], 'image must be the frozen slides-v5 image'
plugin = json.loads(out(['docker', 'plugin', 'inspect', 'kathara/katharanp:amd64']))[0]
assert plugin['Enabled']
assert out(['git', '-C', str(V1 / 'source'), 'branch', '--show-current']) == BRANCH
commit = out(['git', '-C', str(V1 / 'source'), 'rev-parse', 'HEAD'])
assert commit != 'a17059a666676fc9ce65ead2bd03833bc553f22f', 'v2 needs a new commit'
print('preconditions ok; freezing', ROOT, 'at', commit[:8], flush=True)

(ROOT / 'bin').mkdir(parents=True)
(ROOT / 'recovery').mkdir()
subprocess.run(['git', 'clone', '--no-hardlinks', '--quiet', str(V1 / 'source'), str(SRC)],
               check=True)
shutil.copytree(V1 / 'inputs', ROOT / 'inputs')
shutil.copy2(V1 / 'kathara.conf', ROOT / 'kathara.conf')
shutil.copy2(V1 / 'campaign.json', ROOT / 'parent_campaign.json')

shutil.copy2(HERE / 'kathara_bridge.py', ROOT / 'bin/kathara')
(ROOT / 'bin/kathara').chmod(0o755)
global_config = pathlib.Path('/root/.config/kathara.conf')
config = json.loads(global_config.read_text())
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

sys.path.insert(0, str(SRC / 'experiments/concurrency_slides_v5'))
import matrix  # noqa: E402
import pilot  # noqa: E402

logged(['python3', '-m', 'unittest', 'discover', '-s', str(HERE), '-p', 'test*.py'],
       ROOT / 'python-tests.log')
targets = ['test_core', 'test_packet_codec', 'test_query_table_fallback',
           'test_node_staged_fallback', 'test_runtime_neighbor', 'test_tcp_reuse',
           'test_probe_control']
for t in targets:
    logged(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', '/app/build/' + t,
            'rsync-router:concurrency-slides-v5'], ROOT / (t + '.log'), timeout=600)
    print('RUNTIME_TEST_OK', t, flush=True)

v1 = json.loads((V1 / 'campaign.json').read_text())
files = set(out(['git', '-C', str(SRC), 'ls-files']).splitlines()) | set(slides['sources'])
# build the record explicitly: inheriting the slides-v5 metadata would carry block
# counts and slot tallies that describe a different campaign
record = dict(
    parent_campaign=v1['fingerprint'],
    branch=BRANCH, commit=commit,
    image_id=slides['image_id'], image_tag='rsync-router:concurrency-slides-v5',
    worker='volcengine-3', unit=ROOT.name,
    matrix=matrix.conditions(),
    assignments={s: matrix.slots(s) for s in ('smoke', 'pilot', 'formal',
                                              'supplement', 'supplement_v2')},
    sources={n: sha(SRC / n) for n in sorted(files) if (SRC / n).is_file()},
    inputs={str(p.relative_to(ROOT / 'inputs')): sha(p)
            for p in sorted((ROOT / 'inputs').rglob('*')) if p.is_file()},
    per_node_inflight=None, concurrency_cap=None,
    loop_sleep_ms=25, arrival_seed_rule='9172 + workload_seed; repetitions identical',
    seeds=list(range(1, 11)), repetitions=1,
    timeout_policy=pilot.timeout_policy(),
    network_backend=backend, backend_sha256=sha(ROOT / 'backend.json'),
    logging_policy=dict(stdout='direct regular file, no line timestamp subprocess',
                        structured_trace='unchanged epoch and monotonic timestamps',
                        startup_sha256=sha(SRC / 'kathara/ruikekkk/shared/start-node.sh')),
    protocol_sources_unchanged=slides.get('protocol_sources_unchanged'),
    transport_revision=slides.get('transport_revision'),
    evidence='SUPPLEMENT_V2_DUAL_TRACK', epoch_ns=time.time_ns(),
    source_change_scope=('v2 rework: R1 all three 4.2 sizes at lambda=32 plus the lambda=128 '
                         '64 KiB anchor; R2 quiet <=2-slot concurrency with slots_in_parallel '
                         'recorded; R3 per-class federation batch rows; plus the v1 changes '
                         '(controlled 4096, federation pre 32 bins -> warm-up gate -> post 48 bins, '
                         'per-slot query lifetime override, single continuous measurement segment)'),
    supplement_v2=dict(
        slots_file='vldb/exp/supplementary_slots_v2.csv',
        order_seed=matrix.ORDER_SEED_V2,
        conditions=matrix.supplement_v2_conditions(),
        expected_slots=len(matrix.slots('supplement_v2')),
        blocks=dict(runs_4_1=20, runs_4_2a=60, runs_4_2b=20, runs_4_4=20, runs_E=20,
                    runs_4_1_opt=40),
        max_slots_in_parallel=2,
        offered_mib_per_s_rule='rate * size_bytes / 1048576; must satisfy rate*size <= 62 MiB/s',
        aggregation_formula='sum(mean_ms_class * planned_class) / sum(planned_class) per bin'),
)
record['fingerprint'] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
dump(ROOT / 'campaign.json', record)
dump(ROOT / 'recovery/complete.json',
     dict(cleanup_complete=True, reason='v2 freeze complete', campaign=record['fingerprint']))
print(json.dumps(dict(status='FROZEN_NOT_STARTED', campaign=record['fingerprint'],
                      commit=record['commit'], conditions=len(record['matrix']),
                      v2_slots=len(record['assignments']['supplement_v2']),
                      inputs=len(record['inputs'])), indent=2), flush=True)