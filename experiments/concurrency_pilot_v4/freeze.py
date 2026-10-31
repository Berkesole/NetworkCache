"""Freeze host tooling after immutable runtime tests, before any v2 raw attempt."""
import hashlib,json,pathlib,socket,subprocess,sys,time
ROOT=pathlib.Path('/data/networkcache-concurrency-pilot-v4');SRC=ROOT/'source';HERE=SRC/'experiments/concurrency_pilot_v4'
def out(a):return subprocess.check_output(a,text=True).strip()
assert socket.gethostname()=='simpleifz' and not out(['docker','ps','-q'])
assert not list((ROOT/'raw').glob('*/attempt*'))
runtime=json.loads((ROOT/'runtime_build.json').read_text());old=json.loads((ROOT/'baseline/parent_campaign.json').read_text())
for name,digest in runtime['runtime_inputs'].items():assert hashlib.sha256((SRC/name).read_bytes()).hexdigest()==digest,name
for name in ('src/query_table.hpp','src/bloomfilter.hpp','src/union_window.hpp','src/sync_table.hpp','src/node.hpp','app/rsync_node_main.cpp'):
 assert hashlib.sha256((SRC/name).read_bytes()).hexdigest()==old['sources'][name],name
with (ROOT/'python-tests.log').open('w') as h:
 subprocess.run(['python3','-m','unittest','discover','-s',str(HERE),'-p','test_pilot.py'],stdout=h,stderr=subprocess.STDOUT,check=True)
sys.path.insert(0,str(HERE));import pilot,controller
for config in pilot.matrix():
 setup,groups,digest=pilot.input_rows(config)
 if config['stage']=='load':
  assert len(groups)==1 and len(groups[0])==4096
  peer=dict(config,method='tcp-direct' if config['method']=='nc' else 'nc');assert pilot.input_rows(peer)==(setup,groups,digest)
 else:assert len(groups)==4 and all(len(g)==4 for g in groups)
prefix='experiments/concurrency_pilot_v4/'
subprocess.run(['git','-C',str(SRC),'add','--',prefix],check=True)
subprocess.run(['git','-C',str(SRC),'commit','-qm','Add matched 4096-request serial baseline and NC repair reproduction gates'],check=True)
files=out(['git','-C',str(SRC),'ls-files']).splitlines();files=sorted(set(files)|set(old['sources']))
sources={n:hashlib.sha256((SRC/n).read_bytes()).hexdigest() for n in files if (SRC/n).is_file()}
record=dict(worker='worker-c',branch='codex/single-cluster-concurrency-pilot-v4',commit=out(['git','-C',str(SRC),'rev-parse','HEAD']),
 runtime_build=runtime,image_id=runtime['image_id'],parent_campaign=old['fingerprint'],sources=sources,matrix=pilot.matrix(),
 per_node_inflight=None,concurrency_cap=None,command_budget_per_tick=16,evidence='EXPLORATORY_PILOT_V4',arrival_seed=9173,seed=1,repetitions=1,
 requests_per_load=4096,horizon_extension=False,convergence_gate=False,epoch_ns=time.time_ns(),tests='C++ regression + asynchronous application association + Python pilot tests')
assert out(['docker','image','inspect','--format','{{.Id}}',pilot.IMAGE])==record['image_id']
record['fingerprint']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
assert not (ROOT/'campaign.json').exists()
(ROOT/'campaign.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
print(json.dumps(pilot.preflight(),indent=2));print('HOST_TESTS', (ROOT/'python-tests.log').read_text())
