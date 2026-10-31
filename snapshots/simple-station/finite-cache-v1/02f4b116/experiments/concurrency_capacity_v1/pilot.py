#!/usr/bin/env python3
"""Concurrency capacity campaign: open-arrival saturation, overload and bursts.

Reuses the slides-v5 runtime, lab lifecycle, health supervision and evidence
layout verbatim. What differs:
  * the condition matrix is the capacity ladder (capacity_matrix),
  * the measurement sequence is the frozen schedule expanded for high rates
    (capacity_inputs),
  * overloaded runs are accepted as data points (capacity_reports).
"""
import argparse, collections, csv, fcntl, hashlib, json, os, re, shutil, socket, subprocess, sys, time
from pathlib import Path
import controller as ctl
import capacity_matrix as mat
import capacity_inputs as cin

ROOT = Path(os.environ.get('NC_ROOT', str(Path(__file__).resolve().parents[3])))
SOURCE = ROOT / 'source'
HERE = Path(__file__).resolve().parent
RAW = ROOT / 'raw'
LAB = ROOT / 'lab'
DERIVED = ROOT / 'derived'
IMAGE = 'rsync-router:concurrency-slides-v5'
NS = 'concurrency-slides-v5'
UNIT = 'networkcache-concurrency-capacity-v1'
WORK = None
SCENARIO = None
MAPPED = None
NODES = []


def out(args):
    return subprocess.check_output(args, text=True).strip()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def matrix():
    return mat.conditions()


def context(config):
    global WORK, SCENARIO, MAPPED, NODES, RAW
    fed = config['family'] == 'federation'
    WORK = ROOT / 'inputs' / ('federation' if fed else 'single')
    if fed:
        original = (f"kua-dc-v4-federation-v4-bf1024-h3-formal-catalog{config['catalog']}"
                    f"-{config['distribution']}-seed{config['seed']:03d}")
    else:
        original = (f"kua-dc-v4-{config['hosts']}-latency-v4-bf1024-h3-c512"
                    f"-{config['distribution']}-b{config['size']}-seed{config['seed']:03d}")
    MAPPED = WORK / 'mapped' / original
    SCENARIO = (original.replace('kua-dc-v4-federation-v4-bf1024-h3-formal',
                                 'kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot')
                if fed else original)
    text = (WORK / 'scenarios' / f'{SCENARIO}.yaml').read_text()
    NODES = sorted(map(int, re.findall(r'^\s*-\s*id:\s*(\d+)\s*$', text, re.M)))
    assert NODES == list(range(1, config['hosts'] + 1))
    RAW = ROOT / 'raw' / config['stage']
    return original


def timeout_policy():
    return dict(started_request_abort_seconds=None, slow_request_marker_seconds=8,
                unstarted_dispatch_abort_seconds=8, protocol_query_lifetime_seconds=30,
                origin_fallback_seconds=1.5, watchdog_seconds_after_last_arrival=60,
                serial_watchdog_seconds_after_current_release=60,
                tcp_direct_exchange_timeout_seconds=5, quiescence_timeout_seconds=20,
                quiescence_stable_seconds=.5)


def bootstrap(lab=None):
    evidence = {}
    for name in ('start-node.sh', 'start-switch.sh'):
        path = SOURCE / 'kathara/ruikekkk/shared' / name
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ctl.InfrastructureError('missing executable ' + name)
        subprocess.run(['sh', '-n', str(path)], check=True, capture_output=True)
        if lab:
            target = lab / 'shared' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            assert sha(target) == sha(path) and os.access(target, os.X_OK)
        evidence[name] = sha(path)
    return evidence


def preflight():
    assert socket.gethostname() in set(os.environ.get('NC_ALLOWED_HOSTS', 'simpleai').split(','))
    assert out(['git', '-C', str(SOURCE), 'branch', '--show-current']) == os.environ.get(
        'NC_BRANCH', 'simple-station/concurrency-capacity-v1')
    for unit in ('networkcache-concurrency-slides-v1', 'networkcache-concurrency-slides-v4',
                 'networkcache-concurrency-slides-v5', 'networkcache-concurrency-pilot-v8',
                 'networkcache-concurrency-pilot-v9'):
        assert out(['systemctl', 'show', unit, '-p', 'ActiveState', '--value']) in ('failed', 'inactive'), unit
    import network_backend
    network_backend.preflight(ROOT)
    assert not out(['docker', 'ps', '-q']), 'worker not idle'
    campaign = json.loads((ROOT / 'campaign.json').read_text())
    digest = campaign.pop('fingerprint')
    assert hashlib.sha256(json.dumps(campaign, sort_keys=True).encode()).hexdigest() == digest, 'campaign hash'
    assert sha(ROOT / 'backend.json') == campaign['backend_sha256'], 'backend fingerprint mismatch'
    assert campaign['matrix'] == matrix(), 'matrix mismatch'
    assert campaign['per_node_inflight'] is None and campaign['concurrency_cap'] is None
    assert campaign['timeout_policy'] == timeout_policy(), 'timeout policy mismatch'
    assert campaign['loop_sleep_ms'] == 25
    assert out(['docker', 'image', 'inspect', '--format', '{{.Id}}', IMAGE]) == campaign['image_id']
    for name, recorded in campaign['sources'].items():
        assert sha(SOURCE / name) == recorded, name
    for name, recorded in campaign['inputs'].items():
        assert sha(ROOT / 'inputs' / name) == recorded, name
    bootstrap()
    mem = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines()
                   if l.startswith('MemAvailable:'))) * 1024
    assert mem >= 32 * 1024 ** 3 and shutil.disk_usage(ROOT).free >= 200 * 1024 ** 3
    assert json.loads((ROOT / 'recovery/complete.json').read_text())['cleanup_complete']
    return dict(status='PREFLIGHT_OK', campaign=digest, image=campaign['image_id'],
                available_memory_bytes=mem, free_disk_bytes=shutil.disk_usage(ROOT).free)


def input_rows(config):
    context(config)
    original = ctl.read_tsv(MAPPED / 'schedule.tsv')
    setup = [r for r in original if r['phase'] == 'setup']
    measurement = [r for r in original if r['phase'] != 'setup']
    fed = config['family'] == 'federation'
    if fed:
        assert len(measurement) == mat.request_count(config)
        groups = [[r for r in measurement if r['phase'] == phase] for phase in ('pre', 'post')]
        assert list(map(len, groups)) == [1024, 2560]
    elif config['stage'] != 'smoke':
        measurement = cin.expand_measurement(measurement, config.get('repeats', 1))
        cin.validate_expansion([r for r in original if r['phase'] != 'setup'], measurement,
                               config.get('repeats', 1))
        assert len(measurement) == mat.request_count(config)
        groups = [measurement]
    else:
        keys = [r for r in ctl.read_tsv(MAPPED / 'catalog.tsv')
                if r['catalog_class'] == 'formal' and int(r['owner']) >= config['hosts'] // 2 + 1][:6]
        assert len(keys) == 6
        specs = [[(1, k) for k in keys[:4]], [(1, keys[4])] * 4,
                 [(n, keys[5]) for n in (1, 2, 3, 4)], [(1, keys[0])] * 4]
        groups = []
        index = 500000
        for group_number, spec in enumerate(specs, 1):
            group = []
            for node, key in spec:
                index += 1
                group.append(dict(key, request_index=str(index), requester=str(node),
                                  phase='measurement', bin=str(group_number), round='0'))
            groups.append(group)
    digest = hashlib.sha256(json.dumps([setup, groups], sort_keys=True).encode()).hexdigest()
    for row in setup + [r for g in groups for r in g]:
        row['schedule_sha256'] = digest
    return setup, groups, digest


def environment(lab, raw, method, digest, query_lifetime_seconds, loop_sleep_ms, trace):
    assert query_lifetime_seconds == 30 and loop_sleep_ms == 25
    return dict(os.environ, LAB_DIR=str(lab), IMAGE_TAG=IMAGE, SKIP_BUILD='1',
                SCENARIOS_DIR=str(WORK / 'scenarios'),
                KATHARA_NODE_LOOP_SLEEP_MS=str(loop_sleep_ms),
                KATHARA_LINK_DELAY_MS='2', KATHARA_LINK_RATE_MBIT='1000',
                KATHARA_LINK_QUEUE_PACKETS='1000', KATHARA_LINK_MTU='1500',
                RSYNC_WIRE_VERSION='3', RSYNC_BLOOM_BITS='1024', RSYNC_BLOOM_HASH_COUNT='3',
                RSYNC_STAGED_FALLBACK='1', RSYNC_FALLBACK_DELAY_MS='1500',
                RSYNC_QUERY_TIMEOUT_MS=str(query_lifetime_seconds * 1000),
                RSYNC_REDIS_TIMEOUT_MS='1000', RSYNC_REDIS_MAXMEMORY='0',
                RSYNC_REDIS_MAXMEMORY_POLICY='noeviction', RSYNC_REDIS_REQUIRE_EMPTY='1',
                RSYNC_ORACLE_UNICAST='0', RSYNC_LOOP_ITERATIONS='100000000',
                RSYNC_SKIP_APP='1' if method == 'tcp-direct' else '0',
                RSYNC_EVENT_TRACE='1' if trace else '0', RSYNC_EVENT_TRACE_FLUSH_EVERY='1',
                RSYNC_EVENT_TRACE_RUN_ID=raw.parent.name + '-' + raw.name,
                RSYNC_EXPERIMENT_CONTROL_DIR=f'/shared/{NS}/control',
                RSYNC_EXPERIMENT_DATA_DIR=f'/shared/{NS}/data',
                RSYNC_EXPERIMENT_SCHEDULE_HASH=digest,
                RUNTIME_PROVENANCE_FILE=str(raw / 'runtime_image_id.before_launch'))


def containers_for(lab, expected=None):
    ids = out(['docker', 'ps', '-aq', '--filter', 'label=app=kathara']).splitlines()
    all_containers = json.loads(out(['docker', 'inspect', *ids])) if ids else []
    selected = [c for c in all_containers
                if any(m['Source'] == str(lab / 'shared') for m in c['Mounts'])]
    if expected is not None and len(selected) != expected:
        raise ctl.InfrastructureError(f'expected {expected} lab containers, got {len(selected)}')
    return selected


class Health:
    def __init__(self, containers, method, helpers, raw):
        self.helpers = helpers
        self.last = 0
        self.pids = {}
        self.raw = raw
        if method == 'nc':
            for c in containers:
                if int(c['Config']['Labels']['name'].removeprefix('node')) not in NODES:
                    continue
                text = out(['docker', 'top', c['Id'], '-eo', 'pid,args'])
                candidates = [line.split(None, 1)[0] for line in text.splitlines()[1:]
                              if len(line.split(None, 1)) == 2
                              and line.split(None, 1)[1].startswith('/app/build/rsync-node-dev')]
                if len(candidates) != 1:
                    raise ctl.InfrastructureError('cannot locate node host PID: ' + c['Name'])
                pid = candidates[0]
                self.pids[pid] = Path('/proc', pid, 'stat').read_text().split()[21]
            if len(self.pids) != len(NODES):
                raise ctl.InfrastructureError('NC PID inventory mismatch')
        ctl.dump(raw / 'process-pids.json', self.pids)

    def __call__(self):
        now = time.monotonic()
        if now - self.last < .2:
            return
        self.last = now
        problem = None
        if any(p.poll() is not None for p in self.helpers):
            problem = 'TCP helper exited'
        for pid, start in self.pids.items():
            try:
                fields = Path('/proc', pid, 'stat').read_text().split()
                if fields[21] != start or fields[2] == 'Z':
                    problem = 'node process exited/replaced ' + pid
            except FileNotFoundError:
                problem = 'node process disappeared ' + pid
        if problem:
            ctl.dump(self.raw / 'process-failure.json', dict(reason=problem, mono_ns=time.monotonic_ns()))
            raise ctl.ProcessFailure(problem)


def wait_ready(control, helpers):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in helpers):
            raise ctl.InfrastructureError('helper exited before ready')
        if {int(p.stem.removeprefix('node')) for p in (control / 'ready').glob('node*.ready')} == set(NODES):
            return
        time.sleep(.1)
    raise ctl.InfrastructureError('readiness timeout')


def is_temporary(name):
    return name.startswith('.') and ('.tmp' in name) or bool(re.search(r'\.tmp(?:\.|$)', name))


def copy_evidence(source, dest):
    shutil.copytree(source, dest, ignore=lambda _d, names: [n for n in names if is_temporary(n)])
    for p in source.rglob('*'):
        if p.is_file() and not is_temporary(p.name):
            q = dest / p.relative_to(source)
            if not q.is_file() or sha(p) != sha(q):
                raise RuntimeError('unstable/missing evidence ' + str(p))


def finalize(raw, result, stop, archive, clean):
    ctl.dump(raw / 'measurement_outcome.json', result)
    errors = {}
    started = time.monotonic()
    try:
        try:
            stop()
        except Exception as exc:  # noqa: BLE001
            errors['stop'] = repr(exc)
        if 'stop' not in errors:
            try:
                archive()
            except Exception as exc:  # noqa: BLE001
                errors['archive'] = repr(exc)
        else:
            errors['archive'] = 'not attempted while writers may be alive'
    finally:
        try:
            clean()
        except Exception as exc:  # noqa: BLE001
            errors['cleanup'] = repr(exc)
    result = dict(result, measurement_status=result['status'], finalization_errors=errors,
                  finalization_seconds=time.monotonic() - started)
    if errors:
        result['status'] = 'finalization_failure'
    return result


def peak(rows, per_node=False):
    groups = collections.defaultdict(list)
    for row in rows:
        if row.get('node_start_mono_ns') and row.get('completed_mono_ns'):
            groups[row['requester'] if per_node else 'all'] += [
                (int(row['node_start_mono_ns']), 1), (int(row['completed_mono_ns']), -1)]
    result = {}
    for node, events in groups.items():
        active = maximum = 0
        for _, delta in sorted(events):
            active += delta
            maximum = max(maximum, active)
        result[node] = maximum
    return result


def validate_runtime_logs(logs, ttl, poll, reuse):
    for n in NODES:
        text = (logs / f'node{n}.log').read_text()
        for key, value in (('query_timeout_ms', ttl * 1000), ('fallback_delay_ms', 1500),
                           ('loop_sleep_ms', poll), ('pilot_connection_reuse', int(reuse))):
            assert re.search(r'\b' + key + r'\s*[:=]\s*' + str(value) + r'\b', text), f'node{n}: {key} mismatch'


def one(config, attempt):
    context(config)
    begin = time.monotonic_ns()
    raw = RAW / config['id'] / f'attempt{attempt:03d}'
    raw.mkdir(parents=True, exist_ok=False)
    lab = LAB / config['stage'] / config['id'] / raw.name
    shared = lab / 'shared' / NS
    control = shared / 'control'
    control.mkdir(parents=True)
    helpers = []
    handles = []
    containers = []
    ledger = []
    timing = {}
    probe_ids = set()
    result = dict(status='infrastructure_failure', reason='startup')
    setup, groups, digest = input_rows(config)
    ctl.table(raw / 'schedule.csv', setup + [r for g in groups for r in g])
    ctl.dump(raw / 'config.json', config)
    shutil.copy2(ROOT / 'campaign.json', raw / 'campaign_snapshot.json')
    try:
        for name in ('commands', 'started', 'completions', 'ready', 'stopped',
                     'delivery-audit', 'quiesce', 'quiesced', 'fatal'):
            (control / name).mkdir()
        if config['reuse_connections']:
            ctl.atomic(control / 'tcp-connection-reuse.enabled', 'enabled\n')
        ctl.dump(raw / 'transport_policy.json', dict(
            reuse_connections=config['reuse_connections'], nodelay_changed=False,
            per_peer_frames_per_io_tick=1, ambiguous_frame_replay=False))
        ctl.atomic(control / 'async-v2.enabled', 'enabled\n')
        shutil.copytree(MAPPED / 'data', shared / 'data')
        shutil.copy2(HERE / 'tcp_node.py', shared / 'tcp_node.py')
        ctl.dump(raw / 'bootstrap_manifest.json', bootstrap(lab))
        shutil.copy2(MAPPED / 'schedule.tsv', raw / 'original_schedule.tsv')
        shutil.copy2(WORK / 'scenarios' / f'{SCENARIO}.yaml', raw / 'scenario.yaml')
        with (raw / 'driver.stdout.log').open('w') as stdout, (raw / 'driver.stderr.log').open('w') as stderr:
            env = environment(lab, raw, config['method'], digest, config['query_lifetime_seconds'],
                              config['loop_sleep_ms'], config.get('trace'))
            if config['family'] == 'federation':
                env['RSYNC_FEDERATION_METHOD'] = config['method']
            ctl.dump(raw / 'environment.json', {k: v for k, v in env.items()
                                                if k.startswith(('RSYNC_', 'KATHARA_')) or k in ('IMAGE_TAG', 'SKIP_BUILD')})
            subprocess.run([str(SOURCE / 'scripts/kathara/run_ruikekkk_scenario.sh'), SCENARIO],
                           env=env, stdout=stdout, stderr=stderr, check=True, timeout=600)
        device_ids = set(re.findall(r'^(node\d+)\[image\]', (lab / 'lab.conf').read_text(), re.M))
        if not device_ids:
            raise ctl.InfrastructureError('missing generated device inventory')
        containers = containers_for(lab, len(device_ids))
        ctl.dump(raw / 'containers.before.json', containers)
        if {c['Config']['Labels']['name'] for c in containers} != device_ids:
            raise ctl.InfrastructureError('device inventory mismatch')
        image = json.loads((ROOT / 'campaign.json').read_text())['image_id']
        assert all(c['Image'] == image for c in containers)
        network = []
        for c in containers:
            text = out(['docker', 'exec', c['Id'], 'sh', '-c',
                        'cat /proc/self/timens_offsets; ip -j link; ip -j route; tc -j qdisc'])
            if text.splitlines()[:2] != Path('/proc/self/timens_offsets').read_text().strip().splitlines():
                raise ctl.InfrastructureError('monotonic namespace mismatch')
            network.append(dict(container=c['Id'], state=text))
        ctl.dump(raw / 'network.before.json', network)
        if config['method'] == 'tcp-direct':
            for n in NODES:
                log = (raw / f'node{n}.helper.log').open('w')
                handles.append(log)
                helpers.append(subprocess.Popen(
                    ['kathara', 'exec', '-d', str(lab), '--wait', f'node{n}', '--', 'python3',
                     f'/shared/{NS}/tcp_node.py', str(n), f'/shared/{NS}/data/node{n}.tsv',
                     f'/shared/{NS}/control', digest, '--trace', f'/shared/{NS}/tcp-traces/node{n}.jsonl'],
                    stdout=log, stderr=subprocess.STDOUT))
        wait_ready(control, helpers)
        if config['method'] == 'nc':
            validate_runtime_logs(lab / 'shared/logs', config['query_lifetime_seconds'],
                                  config['loop_sleep_ms'], config['reuse_connections'])
        import network_backend
        network_backend.prepare_lab(raw, containers)
        network_backend.verify_lab(ROOT, lab, raw, containers)
        health = Health(containers, config['method'], helpers, raw)
        timing['startup_seconds'] = (time.monotonic_ns() - begin) / 1e9
        started = time.monotonic_ns()
        if setup:
            result, rows = ctl.execute(setup, control, raw / 'setup', config['method'], 'setup',
                                       arrival=[i * 25_000_000 for i in range(len(setup))],
                                       group='setup', health=health, nodes=len(NODES))
            ledger += rows
        else:
            ctl.quiesce(control, '800000000', health, nodes=len(NODES))
            result = dict(status='ok', reason='')
        timing['setup_seconds'] = (time.monotonic_ns() - started) / 1e9
        if result['status'] == 'ok':
            for i, group in enumerate(groups, 1):
                if config['family'] == 'federation' and i == 2:
                    import federation
                    bstart = time.monotonic_ns()
                    probe_ids = federation.boundary(control, raw, MAPPED, config['method'], digest,
                                                    health, lab / 'shared/traces')
                    timing['boundary_seconds'] = (time.monotonic_ns() - bstart) / 1e9
                result, rows = ctl.execute(
                    group, control, raw / f'measurement-{i}', config['method'], config['model'],
                    config['rate'],
                    arrival=[j * 1_000_000 for j in range(len(group))]
                    if config['stage'] == 'smoke' and config['family'] != 'federation' else None,
                    group=f'measurement-{i}', health=health,
                    request_timeout_abort=config.get('request_timeout_abort', True),
                    nodes=len(NODES), arrival_seed=config['arrival_seed'])
                ledger += rows
                if result['status'] != 'ok':
                    break
        if result['status'] == 'ok':
            ctl.validate_audit(control, ledger, probe_ids)
            measured_rows = [r for r in ledger if r.get('phase') != 'setup']
            if config['stage'] == 'smoke' and config['family'] != 'federation':
                first = [r for r in measured_rows if r.get('bin') == '1']
                peaks = peak(first, True)
                if max(peaks.values(), default=0) < 2:
                    raise ctl.CorrectnessError('no verified same-node overlap')
                last = [r for r in measured_rows if r.get('bin') == '4']
                if config['method'] == 'nc' and any(r['via'] != 'local_cache' for r in last):
                    raise ctl.CorrectnessError('warm-cache smoke not served locally')
                ctl.dump(raw / 'smoke_evidence.json', dict(same_node_peak=peaks, completed=len(ledger),
                                                           audit='exactly_once',
                                                           warm_key_via=[r['via'] for r in last]))
            elif peak(measured_rows).get('all', 0) < 2:
                raise ctl.CorrectnessError('open arrival has no actual execution overlap')
    except ctl.CorrectnessError as exc:
        result = dict(status='correctness_failure', reason=str(exc))
    except Exception as exc:  # noqa: BLE001
        result = dict(status='controller_failure' if any((control / 'commands').glob('node*/*.tsv'))
                      else 'infrastructure_failure', reason=repr(exc))
    finally:
        result.update(config=config, started_mono_ns=begin,
                      measurement_finished_mono_ns=time.monotonic_ns(), timing=timing,
                      requests_issued=any((control / 'commands').glob('node*/*.tsv')))

        def stop():
            ctl.atomic(control / 'stop', 'stop\n')
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if len(list((control / 'stopped').glob('node*.stopped'))) == len(NODES) \
                        and all(p.poll() is not None for p in helpers):
                    break
                if not containers:
                    break
                time.sleep(.1)
            current = containers_for(lab)
            if current:
                subprocess.run(['docker', 'stop', '--time', '5', *[c['Id'] for c in current]],
                               capture_output=True, check=True, timeout=60)
                after = json.loads(out(['docker', 'inspect', *[c['Id'] for c in current]]))
                ctl.dump(raw / 'containers.after.json', after)
                if any(c['State'].get('OOMKilled') for c in after):
                    raise ctl.ProcessFailure('container OOM killed')
                if result['status'] == 'ok' and len(list((control / 'stopped').glob('node*.stopped'))) != len(NODES):
                    raise RuntimeError('graceful stop acknowledgement missing')
            for p in helpers:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.terminate()
                    p.wait(timeout=5)
            for h in handles:
                h.close()

        def archive():
            sources = [(control, 'control'), (lab / 'shared/logs', 'node-logs'),
                       (lab / 'shared/generated', 'network-scripts')]
            trace_dir = shared / 'tcp-traces' if config['method'] == 'tcp-direct' else lab / 'shared/traces'
            trace_label = 'tcp-traces' if config['method'] == 'tcp-direct' else 'traces'
            sources.append((trace_dir, trace_label))
            for path, label in sources:
                optional_trace = label in ('traces', 'tcp-traces') and not config.get('trace')
                if path.exists():
                    copy_evidence(path, raw / label)
                elif optional_trace:
                    # Capacity runs keep event traces off except for one mechanism
                    # subset, so an absent trace directory is expected evidence.
                    ctl.dump(raw / (label + '.absent.json'),
                             dict(reason='event trace disabled for this capacity condition'))
                elif result['requests_issued']:
                    raise RuntimeError('required evidence missing ' + label)
            for name in ('lab.conf', 'lab.dep'):
                if (lab / name).exists():
                    shutil.copy2(lab / name, raw / name)

        def clean():
            with (raw / 'cleanup.log').open('w') as log:
                subprocess.run(['kathara', 'lclean', '-d', str(lab)], stdout=log,
                               stderr=subprocess.STDOUT, check=True, timeout=120)
            if containers_for(lab):
                raise RuntimeError('lab containers remain')

        result = finalize(raw, result, stop, archive, clean)
        result['ended_mono_ns'] = time.monotonic_ns()
        result['total_wall_seconds'] = (result['ended_mono_ns'] - begin) / 1e9
        ctl.dump(raw / 'result.json', result)
    return result


def can_skip(config, blocked):
    return config['rate'] > blocked.get((config['method'], config['model']), float('inf'))


def status():
    report = {}
    targets = mat.stage_targets()
    for stage in ('smoke', 'pilot', 'formal'):
        results = []
        for config in mat.slots(stage):
            files = sorted((ROOT / 'raw' / stage / config['id']).glob('attempt*/result.json'))
            if files:
                r = json.loads(files[-1].read_text())
                v = DERIVED / stage / config['id'] / files[-1].parent.name / 'validation.json'
                gate = json.loads(v.read_text()) if v.exists() else {}
                results.append(dict(slot=config['id'], status=r['status'],
                                    validated=gate.get('qualified', False),
                                    outcome=gate.get('outcome'),
                                    completion_fraction=gate.get('completion_fraction'),
                                    wall_seconds=r.get('total_wall_seconds')))
        report[stage] = dict(target=targets[stage],
                             qualified=sum(r['validated'] for r in results),
                             finished=len(results),
                             failed=[r for r in results if not r['validated']])
    return dict(stages=report, unit=subprocess.run(
        ['systemctl', 'show', UNIT, '-p', 'ActiveState', '-p', 'SubState'],
        capture_output=True, text=True).stdout)


def analyze():
    import capacity_reports
    return capacity_reports.build(ROOT)


def complete_stage(stage):
    import capacity_reports
    for config in mat.slots(stage):
        paths = sorted((ROOT / 'raw' / stage / config['id']).glob('attempt*/result.json'))
        if not paths or not capacity_reports.validate_capacity(paths[-1].parent, ROOT)['qualified']:
            return False
    return True


def capacity_budget():
    pilot = []
    for config in mat.slots('pilot'):
        paths = sorted((ROOT / 'raw/pilot' / config['id']).glob('attempt*/result.json'))
        if not paths:
            raise RuntimeError('pilot incomplete for budget: ' + config['id'])
        pilot.append(paths[-1].parent)
    per_run = sum(json.loads((p / 'result.json').read_text())['total_wall_seconds'] for p in pilot) / len(pilot)
    bytes_per_run = sum(f.stat().st_size for p in pilot for f in p.rglob('*') if f.is_file()) / len(pilot)
    formal = len(mat.slots('formal'))
    report = dict(remaining_formal_runs=formal, estimated_formal_seconds=per_run * formal,
                  estimated_formal_bytes=bytes_per_run * formal,
                  required_free_bytes=int(bytes_per_run * formal * 1.3) + 100 * 1024 ** 3,
                  free_bytes=shutil.disk_usage(ROOT).free)
    ctl.dump(DERIVED / 'formal_budget.json', report)
    if report['free_bytes'] < report['required_free_bytes']:
        raise RuntimeError('formal disk budget insufficient; no data deleted')
    return report


def queue(stage):
    import capacity_reports
    with (ROOT / 'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if stage in ('pilot', 'formal') and not complete_stage('smoke'):
            raise RuntimeError('smoke gate incomplete')
        if stage == 'formal':
            if not complete_stage('pilot'):
                raise RuntimeError('pilot gate incomplete')
            capacity_budget()
        for config in mat.slots(stage):
            context(config)
            paths = sorted((RAW / config['id']).glob('attempt*/result.json'))
            attempts = list((RAW / config['id']).glob('attempt*'))
            if len(attempts) != len(paths):
                raise RuntimeError('unfinished attempt; manual investigation required')
            prior = [json.loads(p.read_text()) for p in paths]
            if paths and capacity_reports.validate_capacity(paths[-1].parent, ROOT)['qualified']:
                continue
            if len(attempts) >= 2 or any(r.get('requests_issued') or r['status'] != 'infrastructure_failure'
                                         for r in prior):
                raise RuntimeError('failed measurement cannot automatically rerun')
            preflight()
            r = one(config, len(attempts) + 1)
            if r['status'] == 'infrastructure_failure' and not r['requests_issued'] and not attempts:
                capacity_reports.diagnose(RAW / config['id'] / 'attempt001', ROOT)
                preflight()
                r = one(config, 2)
            path = RAW / config['id'] / f"attempt{len(list((RAW / config['id']).glob('attempt*'))):03d}"
            capacity_reports.diagnose(path, ROOT)
            gate = capacity_reports.validate_capacity(path, ROOT)
            ctl.dump(DERIVED / 'checkpoint.json', dict(stage=stage, slot=config['id'],
                                                       validation=gate, epoch_ns=time.time_ns()))
            if not gate['qualified']:
                analyze()
                raise RuntimeError('queue stopped: ' + json.dumps(gate))
            print(json.dumps(dict(stage=stage, slot=config['id'], qualified=True,
                                  outcome=gate.get('outcome'),
                                  completion_fraction=gate.get('completion_fraction'),
                                  wall_seconds=r['total_wall_seconds'])), flush=True)
        return analyze()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('preflight', 'smoke', 'pilot', 'run', 'campaign', 'status', 'analyze'))
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.command in ('smoke', 'pilot', 'run', 'campaign') and not args.execute:
        parser.error('--execute required')
    if args.command == 'campaign':
        result = None
        for stage in ('smoke', 'pilot', 'formal'):
            result = queue(stage)
    elif args.command in ('smoke', 'pilot', 'run'):
        result = queue('formal' if args.command == 'run' else args.command)
    else:
        result = {'preflight': preflight, 'status': status, 'analyze': analyze}[args.command]()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()