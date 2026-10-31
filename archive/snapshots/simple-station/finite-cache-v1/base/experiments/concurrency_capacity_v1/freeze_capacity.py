#!/usr/bin/env python3
"""Freeze the concurrency capacity campaign on simple-station.

Deployment-local freeze for the capacity campaign. It reuses the slides-v5
campaign only for lineage/protocol constants (timeout policy, logging policy,
image); the matrix, slots and gating are the capacity ones. worker-c and the
slides-v5 campaign are never modified.
"""
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import time

ROOT = pathlib.Path(os.environ.get(
    'NC_ROOT', '/home/simpleai/zhengzihao/networkcache-concurrency-capacity-v1'))
SOURCE = ROOT / 'source'
HERE = SOURCE / 'experiments' / 'concurrency_capacity_v1'
SLIDES = pathlib.Path('/home/simpleai/zhengzihao/networkcache-concurrency-slides-v5')
IMAGE = 'rsync-router:concurrency-slides-v5'
BRANCH = 'simple-station/concurrency-capacity-v1'

TIMEOUT_POLICY = dict(
    started_request_abort_seconds=None, slow_request_marker_seconds=8,
    unstarted_dispatch_abort_seconds=8, protocol_query_lifetime_seconds=30,
    origin_fallback_seconds=1.5, watchdog_seconds_after_last_arrival=60,
    serial_watchdog_seconds_after_current_release=60,
    tcp_direct_exchange_timeout_seconds=5, quiescence_timeout_seconds=20,
    quiescence_stable_seconds=.5)


def out(args):
    return subprocess.check_output(args, text=True).strip()


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def dump(path, payload):
    pathlib.Path(path).write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n')


def main() -> int:
    assert socket.gethostname() == 'simpleai', socket.gethostname()
    assert out(['git', '-C', str(SOURCE), 'branch', '--show-current']) == BRANCH, \
        out(['git', '-C', str(SOURCE), 'branch', '--show-current'])

    plugin = json.loads(out(['docker', 'plugin', 'inspect', 'kathara/katharanp:amd64']))[0]
    assert plugin['Enabled'], 'bridge plugin is not enabled'

    global_config = pathlib.Path.home() / '.config' / 'kathara.conf'
    config = json.loads(global_config.read_text())
    assert config['network_plugin'] == 'kathara/katharanp_vde', config['network_plugin']
    config.update(network_plugin='kathara/katharanp', last_checked=time.time())
    dump(ROOT / 'kathara.conf', config)

    (ROOT / 'bin').mkdir(exist_ok=True)
    shutil.copy2(HERE / 'kathara_bridge.py', ROOT / 'bin' / 'kathara')
    (ROOT / 'bin' / 'kathara').chmod(0o755)

    backend = dict(
        plugin_name='kathara/katharanp:amd64',
        plugin_id=plugin['Id'],
        plugin_reference=plugin['PluginReference'],
        plugin_rootfs=plugin['Config']['rootfs'],
        config_sha256=sha(ROOT / 'kathara.conf'),
        global_config_path=str(global_config),
        global_config_sha256=sha(global_config),
        entrypoint_sha256=sha(ROOT / 'bin' / 'kathara'),
        implementation='official Linux bridge/veth, not VDE/TAP',
        scope='this campaign only, only point-to-point LANs',
        auto_plugin_upgrade=False,
        offload_policy='gso/gro/tso disabled on lab veth pairs before application requests',
        upstream_artifacts=json.loads(
            (ROOT / 'networkcache-bridge-plugin-official-20260915' / 'provenance.json').read_text()),
        kernel=out(['uname', '-r']),
        physical_parameters=dict(mtu=1500, delay_ms=2, rate_mbit=1000, netem_queue_packets=1000),
    )
    dump(ROOT / 'backend.json', backend)

    slides_campaign = json.loads((SLIDES / 'campaign.json').read_text())
    slides_logging = slides_campaign['logging_policy']

    import sys
    sys.path.insert(0, str(HERE))
    import capacity_matrix as matrix

    files = set(out(['git', '-C', str(SOURCE), 'ls-files']).splitlines())
    sources = {name: sha(SOURCE / name) for name in sorted(files) if (SOURCE / name).is_file()}
    inputs = {}
    for path in sorted((ROOT / 'inputs').rglob('*')):
        if path.is_file():
            inputs[str(path.relative_to(ROOT / 'inputs'))] = sha(path)

    image_id = out(['docker', 'image', 'inspect', '--format', '{{.Id}}', IMAGE])
    record = dict(
        campaign_kind='concurrency-capacity-v1',
        worker='simple-station',
        branch=BRANCH,
        commit=out(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD']),
        epoch_ns=time.time_ns(),
        parent_campaign=slides_campaign['fingerprint'],
        port=dict(host=socket.gethostname(), root=str(ROOT),
                  ported_from_campaign=slides_campaign['fingerprint'],
                  note='capacity campaign; reuses the slides-v5 runtime image and protocol source'),
        image_id=image_id,
        parent_image_id=slides_campaign['image_id'],
        matrix=matrix.conditions(),
        assignments={stage: matrix.slots(stage) for stage in ('smoke', 'pilot', 'formal')},
        per_node_inflight=None,
        concurrency_cap=None,
        timeout_policy=TIMEOUT_POLICY,
        loop_sleep_ms=25,
        repetitions=len(matrix.SEEDS),
        seeds=list(matrix.SEEDS),
        formal_slots=len(matrix.slots('formal')),
        evidence='CAPACITY_NO_CONVERGENCE_GATE',
        network_backend=backend,
        backend_sha256=sha(ROOT / 'backend.json'),
        logging_policy=dict(slides_logging, parent_startup_sha256=slides_logging['startup_sha256'],
                            startup_sha256=sha(SOURCE / 'kathara/ruikekkk/shared/start-node.sh')),
        sources=sources,
        inputs=inputs,
        protocol_sources_unchanged=True,
        runtime_unchanged_from_v1=True,
        source_change_scope='new experiment orchestrator only; protocol, TCP, Node, QueryTable, UnionWindow, cache and runtime image unchanged',
        transport_revision='same as slides-v5: original v1 shared peer TCP connection for NC; per-request TCP for direct',
    )
    record['fingerprint'] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    dump(ROOT / 'campaign.json', record)

    (ROOT / 'recovery').mkdir(exist_ok=True)
    dump(ROOT / 'recovery' / 'complete.json', dict(
        cleanup_complete=True, epoch_ns=time.time_ns(),
        reason='capacity campaign environment frozen before smoke'))

    print(json.dumps(dict(status='FROZEN', campaign=record['fingerprint'], image=image_id,
                          plugin=backend['plugin_id'], branch=BRANCH, commit=record['commit'],
                          matrix_conditions=len(record['matrix']),
                          stages=matrix.stage_targets()), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())