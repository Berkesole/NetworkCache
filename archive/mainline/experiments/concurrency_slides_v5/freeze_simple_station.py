#!/usr/bin/env python3
"""Re-freeze the concurrency-slides-v5 campaign for the simple-station port.

Deployment-local re-freeze: records THIS host's plugin ID, Kathara configuration,
source/input hashes and image ID. worker-c's frozen campaign is never modified.

Run with the campaign wrapper first in PATH so `which kathara` resolves to
ROOT/bin/kathara (network_backend.preflight asserts this):

    PATH=/home/simpleai/zhengzihao/networkcache-concurrency-slides-v5/bin:$PATH \
      python3 .../freeze_simple_station.py
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
    'NC_ROOT', '/home/simpleai/zhengzihao/networkcache-concurrency-slides-v5'))
SOURCE = ROOT / 'source'
HERE = SOURCE / 'experiments' / 'concurrency_slides_v5'
IMAGE = 'rsync-router:concurrency-slides-v5'


def out(args):
    return subprocess.check_output(args, text=True).strip()


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def dump(path, payload):
    pathlib.Path(path).write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n')


def main() -> int:
    assert socket.gethostname() == 'simpleai', socket.gethostname()

    plugin = json.loads(out(['docker', 'plugin', 'inspect', 'kathara/katharanp:amd64']))[0]
    assert plugin['Enabled'], 'bridge plugin is not enabled'

    # Campaign-local Kathara settings: inherit this host's global config, switch
    # the network plugin from VDE to the official bridge backend.
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

    parent = json.loads((ROOT / 'campaign.json').read_text())
    record = dict(parent)
    record.pop('fingerprint', None)
    record['worker'] = 'simple-station'
    record['branch'] = out(['git', '-C', str(SOURCE), 'branch', '--show-current'])
    record['commit'] = out(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'])
    record['network_backend'] = backend
    record['backend_sha256'] = sha(ROOT / 'backend.json')
    record['image_id'] = out(['docker', 'image', 'inspect', '--format', '{{.Id}}', IMAGE])
    record['port'] = dict(host=socket.gethostname(), root=str(ROOT),
                          ported_from_campaign=parent.get('fingerprint'),
                          note='deployment-local re-freeze for simple-station; worker-c raw never imported')
    files = set(out(['git', '-C', str(SOURCE), 'ls-files']).splitlines()) | set(record['sources'])
    record['sources'] = {n: sha(SOURCE / n) for n in sorted(files) if (SOURCE / n).is_file()}
    record['inputs'] = {n: sha(ROOT / 'inputs' / n) for n in record['inputs']}
    record['logging_policy'] = dict(
        record['logging_policy'],
        startup_sha256=sha(SOURCE / 'kathara/ruikekkk/shared/start-node.sh'))
    record['fingerprint'] = hashlib.sha256(
        json.dumps(record, sort_keys=True).encode()).hexdigest()
    dump(ROOT / 'campaign.json', record)

    dump(ROOT / 'recovery' / 'complete.json', dict(
        cleanup_complete=True,
        epoch_ns=time.time_ns(),
        reason='simple-station port: environment frozen before smoke; no worker-c raw imported',
        port=dict(host=socket.gethostname(), root=str(ROOT))))

    print(json.dumps(dict(status='REFROZEN', campaign=record['fingerprint'],
                          image=record['image_id'], plugin=backend['plugin_id'],
                          branch=record['branch'], commit=record['commit']), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())