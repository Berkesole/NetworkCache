#!/usr/bin/env python3
"""Track A freeze: /home/simpleai/zhengzihao/networkcache-concurrency-supplement-v1.

The source, patches and regenerated inputs are prepared by the stage-1/stage-2
bootstrap, so this script only produces the campaign-local artifacts (kathara wrapper,
configuration, backend.json, tests) and writes campaign.json + recovery/complete.json.
It never writes the three pre-existing simple-station roots.
"""
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path('/home/simpleai/zhengzihao/networkcache-concurrency-supplement-v1')
OLD = pathlib.Path('/home/simpleai/zhengzihao/networkcache-concurrency-slides-v5')
SRC = ROOT / 'source'
HERE = pathlib.Path(__file__).resolve().parent
BRANCH = 'simple-station/concurrency-supplement-v1'
UNIT = 'networkcache-concurrency-supplement-v1-ss'
PEER = os.environ.get('PEER_CAMPAIGN', '')


def out(a):
    return subprocess.check_output(a, text=True).strip()


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def dump(p, x):
    pathlib.Path(p).write_text(json.dumps(x, sort_keys=True, indent=2) + '\n')


def logged(a, p, **kw):
    with open(p, 'w') as h:
        subprocess.run(a, stdout=h, stderr=subprocess.STDOUT, check=True, **kw)


assert socket.gethostname() == 'simpleai', socket.gethostname()
assert not out(['docker', 'ps', '-q']), 'worker not idle'
assert ROOT.is_dir() and (ROOT / 'inputs').is_dir(), 'run stage 2 before freezing'
assert (ROOT / 'base_manifest.json').is_file(), 'base_manifest.json missing'
reference = json.loads((OLD / 'campaign.json').read_text())
image = out(['docker', 'image', 'inspect', '--format', '{{.Id}}',
             'rsync-router:concurrency-slides-v5'])
assert image == reference['image_id'], 'image must be the frozen slides-v5 image'
plugin = json.loads(out(['docker', 'plugin', 'inspect', 'kathara/katharanp:amd64']))[0]
assert plugin['Enabled']
assert out(['git', '-C', str(SRC), 'branch', '--show-current']) == BRANCH
commit = out(['git', '-C', str(SRC), 'rev-parse', 'HEAD'])
print('preconditions ok; freezing', ROOT, 'at', commit[:8], flush=True)

shutil.copy2(HERE / 'kathara_bridge.py', ROOT / 'bin/kathara')
(ROOT / 'bin/kathara').chmod(0o755)
global_config = pathlib.Path.home() / '.config/kathara.conf'
if not global_config.is_file():
    global_config = pathlib.Path('/home/simpleai/.config/kathara.conf')
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
                   '/data/networkcache-bridge-plugin-official-20260915/provenance.json').read_text())
               if pathlib.Path('/data/networkcache-bridge-plugin-official-20260915/provenance.json').is_file()
               else {},
               kernel=out(['uname', '-r']),
               physical_parameters=dict(mtu=1500, delay_ms=2, rate_mbit=1000,
                                        netem_queue_packets=1000))
dump(ROOT / 'backend.json', backend)
print('backend frozen', flush=True)

sys.path.insert(0, str(HERE))
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

startup = SRC / 'kathara/ruikekkk/shared/start-node.sh'
assert startup.is_file(), startup
files = set(out(['git', '-C', str(SRC), 'ls-files']).splitlines())
manifest = json.loads((ROOT / 'base_manifest.json').read_text())
record = dict(
    parent_campaign=reference['fingerprint'],
    dual_track_peer_campaign=PEER,
    branch=BRANCH, commit=commit, track='A (simple-station)',
    image_id=reference['image_id'], image_tag='rsync-router:concurrency-slides-v5',
    worker='simple-station', unit=UNIT,
    matrix=matrix.conditions(),
    assignments={s: matrix.slots(s) for s in ('smoke', 'pilot', 'formal', 'supplement_v2')},
    sources={n: sha(SRC / n) for n in sorted(files) if (SRC / n).is_file()},
    inputs={str(p.relative_to(ROOT / 'inputs')): sha(p)
            for p in sorted((ROOT / 'inputs').rglob('*')) if p.is_file()},
    per_node_inflight=None, concurrency_cap=None, loop_sleep_ms=25,
    arrival_seed_rule='9172 + workload_seed; repetitions identical',
    seeds=list(range(1, 11)), repetitions=1,
    timeout_policy=pilot.timeout_policy(),
    network_backend=backend, backend_sha256=sha(ROOT / 'backend.json'),
    logging_policy=dict(stdout='direct regular file, no line timestamp subprocess',
                        structured_trace='unchanged epoch and monotonic timestamps',
                        startup_sha256=sha(startup)),
    protocol_sources_unchanged=reference.get('protocol_sources_unchanged'),
    transport_revision=reference.get('transport_revision'),
    evidence='SUPPLEMENT_V2_DUAL_TRACK_TRACK_A', epoch_ns=time.time_ns(),
    reused_modules=manifest['reused_modules_sha256'],
    reused_modules_rule=manifest['reused_modules_rule'],
    changes_patch_sha256=sha(ROOT / 'recovery' / 'trackA-changes.patch')
    if (ROOT / 'recovery' / 'trackA-changes.patch').is_file() else None,
    source_change_scope=('track A reproduction with the same semantic changes as track B: '
                         'federation warm-up gate + pre/post, per-slot query lifetime, '
                         'controlled 4096, iid-zipf 1 KiB / 1 MiB arms, federation 11776, '
                         'slots_in_parallel recording, <=2 slot quiet runs, per-class rows'),
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
     dict(cleanup_complete=True, reason='track A freeze complete',
          campaign=record['fingerprint']))
print(json.dumps(dict(status='FROZEN_NOT_STARTED', campaign=record['fingerprint'],
                      commit=record['commit'], conditions=len(record['matrix']),
                      v2_slots=len(record['assignments']['supplement_v2']),
                      inputs=len(record['inputs'])), indent=2), flush=True)