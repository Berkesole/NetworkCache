"""One-shot isolated deployment. No experiment is issued by this script."""
import hashlib,json,pathlib,shutil,socket,subprocess,sys,tarfile,time
ROOT=pathlib.Path('/data/networkcache-concurrency-slides-v1')
OLD=pathlib.Path('/data/networkcache-concurrency-pilot-v9')
SRC=ROOT/'source';STAGE=pathlib.Path(__file__).resolve().parent
IMAGE_ID='sha256:daa791093c488e86343fd90edcb1f3dae56e072c9012799f6bd3d29bf80d8c8e'
PARENT='981064e52f32a4240baa8ebb5d1fc6b24baaab2001564978aecb75ad4939b807'
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def logged(args,path,**kwargs):
 with path.open('w') as h:subprocess.run(args,stdout=h,stderr=subprocess.STDOUT,check=True,**kwargs)
assert socket.gethostname()=='simpleifz'
assert not out(['docker','ps','-q'])
assert out(['systemctl','show','networkcache-concurrency-pilot-v9','-p','ActiveState','--value'])=='inactive'
assert out(['systemctl','show','networkcache-concurrency-slides-v1','-p','ActiveState','--value'])=='inactive'
assert not out(['git','-C',str(OLD/'source'),'status','--porcelain'])
parent=json.loads((OLD/'campaign.json').read_text());assert parent['fingerprint']==PARENT
body=dict(parent);body.pop('fingerprint');assert hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()==PARENT
for name,d in parent['sources'].items():assert sha(OLD/'source'/name)==d,name
assert out(['docker','image','inspect','--format','{{.Id}}','rsync-router:concurrency-pilot-v8'])==IMAGE_ID==parent['image_id']
assert subprocess.run(['docker','image','inspect','rsync-router:concurrency-slides-v1'],capture_output=True).returncode!=0,'new tag already exists'
baseline={}
for c in parent['matrix']:
 raw=OLD/'raw'/c['id']/'attempt001';r=json.loads((raw/'result.json').read_text())
 assert r['status']=='ok' and not r['finalization_errors']
 assert json.loads((raw/'measurement-1/result.json').read_text())['completed']==4096
 baseline[c['id']]=dict(raw=str(raw),result_sha256=sha(raw/'result.json'))
ROOT.mkdir(exist_ok=False);(ROOT/'baseline').mkdir();(ROOT/'recovery').mkdir();(ROOT/'inputs').mkdir()
shutil.copy2(OLD/'campaign.json',ROOT/'baseline/parent_campaign.json')
subprocess.run(['git','clone','--no-hardlinks',str(OLD/'source'),str(SRC)],check=True)
subprocess.run(['git','-C',str(SRC),'checkout','-b','codex/concurrency-slides-v1'],check=True)
for key in ('user.name','user.email'):subprocess.run(['git','-C',str(SRC),'config',key,out(['git','-C',str(OLD/'source'),'config',key])],check=True)
for name in parent['sources']:
 p=SRC/name
 if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/'source'/name,p)
with tarfile.open(STAGE/'inputs.tar.gz') as archive:
 for m in archive.getmembers():
  p=pathlib.PurePosixPath(m.name)
  assert not p.is_absolute() and '..' not in p.parts and not m.issym() and not m.islnk(),m.name
 archive.extractall(ROOT/'inputs')
for old,new in (('latency-v4-bf1024-h3-c512','single'),('federation-v4-bf1024-h3-formal','federation')):(ROOT/'inputs'/old).rename(ROOT/'inputs'/new)
aliases={}
for p in (ROOT/'inputs/federation/scenarios').glob('kua-dc-v4-*.yaml'):
 target=p.with_name(p.name.replace('kua-dc-v4-federation-v4-bf1024-h3-formal','kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot'))
 assert target!=p and not target.exists();shutil.copy2(p,target);assert sha(p)==sha(target)
 aliases[str(target.relative_to(ROOT/'inputs'))]=dict(original=str(p.relative_to(ROOT/'inputs')),sha256=sha(p),reason='frozen renderer scenario-name adapter; byte-identical topology')
assert len(aliases)==40
(ROOT/'baseline/scenario_aliases.json').write_text(json.dumps(aliases,indent=2)+'\n')
HERE=SRC/'experiments/concurrency_slides_v1';HERE.mkdir()
for p in STAGE.iterdir():
 if p.suffix in ('.py','.md'):shutil.copy2(p,HERE/p.name)
for name,dest in (('rsync_node_main.cpp','app/rsync_node_main.cpp'),('probe_control.hpp','app/probe_control.hpp'),('test_probe_control.cpp','tests/test_probe_control.cpp'),('Dockerfile.slides','experiments/concurrency_slides_v1/Dockerfile.slides')):
 shutil.copy2(STAGE/name,SRC/dest)
for name,d in parent['sources'].items():
 if name!='app/rsync_node_main.cpp':assert sha(SRC/name)==d,name
logged(['python3','-m','unittest','discover','-s',str(HERE),'-p','test*.py'],ROOT/'python-tests.log')
sys.path.insert(0,str(HERE));import pilot,controller,matrix
identities={}
for c in matrix.conditions():
 for seed in range(1,11):
  conf=matrix.slot(c,'pilot',seed);setup,groups,digest=pilot.input_rows(conf)
  assert sum(map(len,groups))==matrix.request_count(c)
  identities[conf['id']]=dict(schedule_sha256=digest,setup=len(setup),phases=list(map(len,groups)))
  assert pilot.input_rows(matrix.slot(c,'formal',seed,2))[2]==digest
  for group in groups:
   a,_=controller.offsets(len(group),c['model'],c['rate'],9172+seed)
   assert a==sorted(a) and len(a)==len(group)
for c in matrix.slots('smoke'):pilot.input_rows(c)
(ROOT/'baseline/input_verification.json').write_text(json.dumps(identities,indent=2)+'\n')
print('PYTHON_AND_INPUT_TESTS_OK',flush=True)
logged(['docker','build','-f',str(HERE/'Dockerfile.slides'),'-t',pilot.IMAGE,str(SRC)],ROOT/'image-build.log',timeout=900)
image=out(['docker','image','inspect','--format','{{.Id}}',pilot.IMAGE]);assert image!=IMAGE_ID
targets=['test_core','test_packet_codec','test_query_table_fallback','test_node_staged_fallback','test_runtime_neighbor','test_tcp_reuse','test_probe_control']
for target in targets:
 logged(['docker','run','--rm','--network','none','--entrypoint','/app/build/'+target,pilot.IMAGE],ROOT/(target+'.log'),timeout=120)
 print('RUNTIME_TEST_OK',target,flush=True)
subprocess.run(['git','-C',str(SRC),'add','--','experiments/concurrency_slides_v1','app/rsync_node_main.cpp','app/probe_control.hpp','tests/test_probe_control.cpp'],check=True)
subprocess.run(['git','-C',str(SRC),'commit','-qm','Add isolated 64-condition open-arrival campaign and expiry-proven probe retirement'],check=True)
files=set(out(['git','-C',str(SRC),'ls-files']).splitlines())|set(parent['sources'])
record=dict(worker='worker-c',branch='codex/concurrency-slides-v1',commit=out(['git','-C',str(SRC),'rev-parse','HEAD']),image_id=image,parent_image_id=IMAGE_ID,
 parent_campaign=PARENT,parent_gates=baseline,runtime_tests=targets,protocol_sources_unchanged=True,scenario_aliases=aliases,
 sources={name:sha(SRC/name) for name in sorted(files) if (SRC/name).is_file()},inputs={str(p.relative_to(ROOT/'inputs')):sha(p) for p in sorted((ROOT/'inputs').rglob('*')) if p.is_file()},
 matrix=matrix.conditions(),assignments={s:matrix.slots(s) for s in ('smoke','pilot','formal')},per_node_inflight=None,concurrency_cap=None,
 seeds=list(range(1,11)),repetitions=2,arrival_seed_rule='9172 + workload_seed; repetitions identical',formal_slots=1280,single_cluster_slots=960,federation_slots=320,
 timeout_policy=pilot.timeout_policy(),loop_sleep_ms=25,evidence='OPEN_ARRIVAL_NO_CONVERGENCE_GATE',epoch_ns=time.time_ns(),
 source_change_scope='application probe bookkeeping, async TCP activation, controller and analysis only; no protocol algorithm changes')
record['fingerprint']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
(ROOT/'campaign.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
(ROOT/'recovery/complete.json').write_text(json.dumps(dict(cleanup_complete=True,action='v9 finished, no lab containers; new runtime regression tests passed'))+'\n')
print('PYTHON_TESTS', (ROOT/'python-tests.log').read_text(),flush=True)
print(json.dumps(pilot.preflight()),flush=True)
