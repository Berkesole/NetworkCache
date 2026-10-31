"""Create immutable backend-only candidate from v1; never launch a load."""
import hashlib,json,pathlib,shutil,socket,subprocess,sys,time
ROOT=pathlib.Path('/data/networkcache-concurrency-slides-v4');OLD=pathlib.Path('/data/networkcache-concurrency-slides-v1')
SRC=ROOT/'source';STAGE=pathlib.Path(__file__).resolve().parent
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,sort_keys=True,indent=2)+'\n')
def logged(a,p,**kw):
 with p.open('w') as h:subprocess.run(a,stdout=h,stderr=subprocess.STDOUT,check=True,**kw)
assert socket.gethostname()=='simpleifz' and not out(['docker','ps','-q'])
parent=json.loads((OLD/'campaign.json').read_text())
assert parent['fingerprint']=='d722b5f10d7b86ed12a3b5f954f6a6962fdd7cc1f67e5860d34ebd52453c64c2'
for n,d in parent['sources'].items():assert sha(OLD/'source'/n)==d,n
for n,d in parent['inputs'].items():assert sha(OLD/'inputs'/n)==d,n
assert out(['docker','image','inspect','--format','{{.Id}}','rsync-router:concurrency-slides-v1'])==parent['image_id']
assert subprocess.run(['docker','image','inspect','rsync-router:concurrency-slides-v4'],capture_output=True).returncode!=0
plugin=json.loads(out(['docker','plugin','inspect','kathara/katharanp:amd64']))[0]
assert plugin['Enabled']
ROOT.mkdir(exist_ok=False);(ROOT/'bin').mkdir();(ROOT/'recovery').mkdir()
shutil.copy2(OLD/'campaign.json',ROOT/'parent_campaign.json')
subprocess.run(['git','clone','--no-hardlinks',str(OLD/'source'),str(SRC)],check=True)
subprocess.run(['git','-C',str(SRC),'checkout','-b','codex/concurrency-slides-v4'],check=True)
for key in ('user.name','user.email'):subprocess.run(['git','-C',str(SRC),'config',key,out(['git','-C',str(OLD/'source'),'config',key])],check=True)
for n,d in parent['sources'].items():
 p=SRC/n
 if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/'source'/n,p)
 assert sha(p)==d,n
shutil.copytree(OLD/'inputs',ROOT/'inputs')
HERE=SRC/'experiments/concurrency_slides_v4';HERE.mkdir()
names=['pilot.py','controller.py','matrix.py','reports.py','analysis.py','federation.py','tcp_node.py','compare_cutoff.py','test_pilot.py','test_suite.py','test_backend.py','network_backend.py','kathara_bridge.py','deploy_backend.py','regression_backend.py','README_BACKEND.md']
for n in names:shutil.copy2(STAGE/n,HERE/n)
shutil.copy2(HERE/'kathara_bridge.py',ROOT/'bin/kathara');(ROOT/'bin/kathara').chmod(0o755)
global_config=pathlib.Path('/root/.config/kathara.conf');config=json.loads(global_config.read_text())
assert config['network_plugin']=='kathara/katharanp_vde'
config.update(network_plugin='kathara/katharanp',last_checked=time.time())
dump(ROOT/'kathara.conf',config)
backend=dict(plugin_name='kathara/katharanp:amd64',plugin_id=plugin['Id'],plugin_reference=plugin['PluginReference'],plugin_rootfs=plugin['Config']['rootfs'],
 config_sha256=sha(ROOT/'kathara.conf'),global_config_path=str(global_config),global_config_sha256=sha(global_config),entrypoint_sha256=sha(ROOT/'bin/kathara'),
 implementation='official Linux bridge/veth, not VDE/TAP',scope='this campaign only, only point-to-point LANs',auto_plugin_upgrade=False,offload_policy='gso/gro/tso disabled on lab veth pairs before application requests',
 upstream_artifacts=json.loads(pathlib.Path('/data/networkcache-bridge-plugin-official-20260915/provenance.json').read_text()),
 kernel=out(['uname','-r']),physical_parameters=dict(mtu=1500,delay_ms=2,rate_mbit=1000,netem_queue_packets=1000))
dump(ROOT/'backend.json',backend)
subprocess.run(['docker','tag',parent['image_id'],'rsync-router:concurrency-slides-v4'],check=True)
logged(['python3','-m','unittest','discover','-s',str(HERE),'-p','test*.py'],ROOT/'python-tests.log')
targets=['test_core','test_packet_codec','test_query_table_fallback','test_node_staged_fallback','test_runtime_neighbor','test_tcp_reuse','test_probe_control']
for t in targets:
 logged(['docker','run','--rm','--network','none','--entrypoint','/app/build/'+t,'rsync-router:concurrency-slides-v4'],ROOT/(t+'.log'),timeout=120)
 print('RUNTIME_TEST_OK',t,flush=True)
sys.path.insert(0,str(HERE));import matrix,pilot
assert matrix.conditions()==parent['matrix']
assert {s:matrix.slots(s) for s in ('smoke','pilot','formal')}==parent['assignments']
for n,d in parent['sources'].items():assert sha(SRC/n)==d,n
subprocess.run(['git','-C',str(SRC),'add','--','experiments/concurrency_slides_v4'],check=True)
subprocess.run(['git','-C',str(SRC),'commit','-qm','Add isolated bridge backend candidate; retain original runtime and arrival workload'],check=True)
files=set(out(['git','-C',str(SRC),'ls-files']).splitlines())|set(parent['sources'])
record=dict(parent)
record.pop('fingerprint')
record.update(parent_campaign=parent['fingerprint'],branch='codex/concurrency-slides-v4',commit=out(['git','-C',str(SRC),'rev-parse','HEAD']),image_id=parent['image_id'],
 sources={n:sha(SRC/n) for n in sorted(files) if (SRC/n).is_file()},network_backend=backend,backend_sha256=sha(ROOT/'backend.json'),
 runtime_unchanged_from_v1=True,transport_revision='original v1 shared peer TCP connection; failed v2 lanes not adopted',epoch_ns=time.time_ns(),
 source_change_scope='lab backend, provenance, tests, and orchestration only; all parent source hashes unchanged')
record['fingerprint']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
dump(ROOT/'campaign.json',record)
dump(ROOT/'recovery/complete.json',dict(cleanup_complete=False,reason='backend regression not yet qualified'))
print((ROOT/'python-tests.log').read_text(),flush=True)
print(json.dumps(dict(status='FROZEN_NOT_STARTED',campaign=record['fingerprint'],image=record['image_id'],backend=backend)),flush=True)
