"""Deploy isolated v9 orchestration; reuse exact v8 runtime, never rebuild."""
import hashlib,json,pathlib,shutil,socket,subprocess,sys,time
ROOT=pathlib.Path('/data/networkcache-concurrency-pilot-v9')
OLD=pathlib.Path('/data/networkcache-concurrency-pilot-v8')
SRC=ROOT/'source';STAGE=pathlib.Path(__file__).resolve().parent
IMAGE_ID='sha256:daa791093c488e86343fd90edcb1f3dae56e072c9012799f6bd3d29bf80d8c8e'
PARENT='4642e8007620e5210d699d042577472e5e285fb7b1b955547ed282735b2fefac'
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert socket.gethostname()=='simpleifz'
assert not out(['docker','ps','-q'])
assert out(['systemctl','show','networkcache-concurrency-pilot-v8','-p','ActiveState','--value'])=='inactive'
assert out(['systemctl','show','networkcache-concurrency-pilot-v9','-p','ActiveState','--value'])=='inactive'
assert not out(['git','-C',str(OLD/'source'),'status','--porcelain'])
parent=json.loads((OLD/'campaign.json').read_text());assert parent['fingerprint']==PARENT
body=dict(parent);body.pop('fingerprint');assert hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()==PARENT
for name,d in parent['sources'].items():assert sha(OLD/'source'/name)==d,name
assert out(['docker','image','inspect','--format','{{.Id}}','rsync-router:concurrency-pilot-v8'])==IMAGE_ID==parent['image_id']
assert sha(STAGE/'tcp_node.py')==sha(OLD/'source/experiments/concurrency_pilot_v8/tcp_node.py')
baseline={}
for config in parent['matrix']:
 raw=OLD/'raw'/config['id']/'attempt001';r=json.loads((raw/'result.json').read_text())
 assert r['status']=='ok' and not r['finalization_errors']
 if config['stage']=='load':assert json.loads((raw/'measurement-1/result.json').read_text())['completed']==4096
 gate=OLD/'derived'/config['id']/'attempt001/transport_gate.json';assert json.loads(gate.read_text())['status']=='OK'
 baseline[config['id']]=dict(raw=str(raw),result_sha256=sha(raw/'result.json'),gate_sha256=sha(gate))
ROOT.mkdir(exist_ok=False);(ROOT/'baseline').mkdir();(ROOT/'recovery').mkdir()
shutil.copy2(OLD/'campaign.json',ROOT/'baseline/parent_campaign.json')
subprocess.run(['git','clone','--no-hardlinks',str(OLD/'source'),str(SRC)],check=True)
subprocess.run(['git','-C',str(SRC),'checkout','-b','codex/single-cluster-concurrency-pilot-v9'],check=True)
for key in ('user.name','user.email'):subprocess.run(['git','-C',str(SRC),'config',key,out(['git','-C',str(OLD/'source'),'config',key])],check=True)
for name in parent['sources']:
 p=SRC/name
 if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/'source'/name,p)
HERE=SRC/'experiments/concurrency_pilot_v9';HERE.mkdir()
for p in STAGE.iterdir():
 if p.suffix in ('.py','.md'):shutil.copy2(p,HERE/p.name)
for name,d in parent['sources'].items():assert sha(SRC/name)==d,name
with (ROOT/'python-tests.log').open('w') as h:subprocess.run(['python3','-m','unittest','discover','-s',str(HERE),'-p','test*.py'],stdout=h,stderr=subprocess.STDOUT,check=True)
sys.path.insert(0,str(HERE));import pilot,controller
fingerprints=set()
for c in pilot.matrix():
 setup,groups,digest=pilot.input_rows(c);assert len(groups)==1 and len(groups[0])==4096;fingerprints.add(digest)
 if c['model'] in ('poisson','burst'):
  old=OLD/'raw'/f"{c['model']}-r128-nc-reuse"/'attempt001/measurement-1/arrival_manifest.json'
  manifest=json.loads(old.read_text());arrivals,shift=controller.offsets(4096,c['model'],128)
  assert (arrivals,shift)==(manifest['planned_offsets_ns'],manifest['phase_seconds'])
  assert manifest['request_ids']==[r['request_index'] for r in groups[0]]
assert len(fingerprints)==1
subprocess.run(['git','-C',str(SRC),'add','--','experiments/concurrency_pilot_v9'],check=True)
subprocess.run(['git','-C',str(SRC),'commit','-qm','Compare serial quiescence and open arrival on frozen v8 runtime'],check=True)
targets=['test_core','test_packet_codec','test_query_table_fallback','test_node_staged_fallback','test_runtime_neighbor','test_tcp_reuse']
for target in targets:
 with (ROOT/(target+'.log')).open('w') as h:subprocess.run(['docker','run','--rm','--network','none','--entrypoint','/app/build/'+target,pilot.IMAGE],stdout=h,stderr=subprocess.STDOUT,check=True,timeout=120)
 print('RUNTIME_TEST_OK',target,flush=True)
files=set(out(['git','-C',str(SRC),'ls-files']).splitlines())|set(parent['sources'])
record=dict(worker='worker-c',branch='codex/single-cluster-concurrency-pilot-v9',commit=out(['git','-C',str(SRC),'rev-parse','HEAD']),image_id=IMAGE_ID,
 runtime_rebuilt=False,parent_campaign=PARENT,parent_smoke_and_load_gates=baseline,runtime_tests=targets,
 sources={name:sha(SRC/name) for name in sorted(files) if (SRC/name).is_file()},matrix=pilot.matrix(),per_node_inflight=None,concurrency_cap=None,
 seed=1,repetitions=1,arrival_seed=9173,requests_per_load=4096,evidence='EXPLORATORY_SERIAL_OPEN_METHOD',timeout_policy=pilot.timeout_policy(),loop_sleep_ms=25,
 schedule_sha256=next(iter(fingerprints)),epoch_ns=time.time_ns(),
 comparison_policy='same method only; endogenous serial releases versus exogenous open arrivals; no cross-version speedup or max-throughput claim')
record['fingerprint']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
(ROOT/'campaign.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
(ROOT/'recovery/complete.json').write_text(json.dumps(dict(cleanup_complete=True,action='v8 inactive, no lab containers; immutable v8 runtime tests passed'))+'\n')
print('PYTHON_TESTS', (ROOT/'python-tests.log').read_text(),flush=True)
print(json.dumps(pilot.preflight()),flush=True)
