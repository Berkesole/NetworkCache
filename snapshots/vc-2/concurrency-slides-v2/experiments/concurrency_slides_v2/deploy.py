"""Build and freeze candidate; no application requests."""
import pathlib,hashlib,json,shutil,subprocess,sys,socket,time
ROOT=pathlib.Path('/data/networkcache-concurrency-slides-v2');OLD=pathlib.Path('/data/networkcache-concurrency-slides-v1');SRC=ROOT/'source';STAGE=pathlib.Path(__file__).resolve().parent
PARENT='d722b5f10d7b86ed12a3b5f954f6a6962fdd7cc1f67e5860d34ebd52453c64c2'
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def logged(args,path,**kwargs):
 with path.open('w') as h:subprocess.run(args,stdout=h,stderr=subprocess.STDOUT,check=True,**kwargs)
assert socket.gethostname()=='simpleifz' and not out(['docker','ps','-q'])
assert out(['systemctl','show','networkcache-concurrency-slides-v1','-p','ActiveState','--value'])=='failed'
assert out(['git','-C',str(SRC),'branch','--show-current'])=='codex/concurrency-slides-v2'
assert not (ROOT/'campaign.json').exists()
parent=json.loads((OLD/'campaign.json').read_text());assert parent['fingerprint']==PARENT
for name,d in parent['sources'].items():assert sha(SRC/name)==sha(OLD/'source'/name)==d,name
for name,d in parent['inputs'].items():assert sha(ROOT/'inputs'/name)==d,name
assert out(['docker','image','inspect','--format','{{.Id}}','rsync-router:concurrency-slides-v1'])==parent['image_id']
assert subprocess.run(['docker','image','inspect','rsync-router:concurrency-slides-v2'],capture_output=True).returncode!=0
HERE=SRC/'experiments/concurrency_slides_v2';HERE.mkdir(exist_ok=False)
for p in STAGE.iterdir():
 if p.suffix in ('.py','.md') or p.name=='Dockerfile.transport':shutil.copy2(p,HERE/p.name)
for name,dest in (('tcp_network.cpp','src/network/tcp_network.cpp'),('tcp_network.hpp','src/network/tcp_network.hpp'),('test_tcp_reuse.cpp','tests/test_tcp_reuse.cpp')):shutil.copy2(STAGE/name,SRC/dest)
allowed={'src/network/tcp_network.cpp','src/network/tcp_network.hpp','tests/test_tcp_reuse.cpp'}
for name,d in parent['sources'].items():
 if name not in allowed:assert sha(SRC/name)==d,name
logged(['python3','-m','unittest','discover','-s',str(HERE),'-p','test*.py'],ROOT/'python-tests.log')
sys.path.insert(0,str(HERE));import matrix,pilot,controller
assert matrix.conditions()==parent['matrix']
assert {s:matrix.slots(s) for s in ('smoke','pilot','formal')}==parent['assignments']
logged(['docker','build','-f',str(HERE/'Dockerfile.transport'),'-t',pilot.IMAGE,str(SRC)],ROOT/'image-build.log',timeout=900)
image=out(['docker','image','inspect','--format','{{.Id}}',pilot.IMAGE]);assert image!=parent['image_id']
targets=['test_core','test_packet_codec','test_query_table_fallback','test_node_staged_fallback','test_runtime_neighbor','test_tcp_reuse','test_probe_control']
for target in targets:
 logged(['docker','run','--rm','--network','none','--entrypoint','/app/build/'+target,pilot.IMAGE],ROOT/(target+'.log'),timeout=120)
 print('RUNTIME_TEST_OK',target,flush=True)
subprocess.run(['git','-C',str(SRC),'add','--','experiments/concurrency_slides_v2',*sorted(allowed)],check=True)
subprocess.run(['git','-C',str(SRC),'commit','-qm','Isolate control from DATA TCP backpressure; preserve workload and NC algorithms'],check=True)
files=set(out(['git','-C',str(SRC),'ls-files']).splitlines())|set(parent['sources'])
record=dict(worker='worker-c',branch='codex/concurrency-slides-v2',commit=out(['git','-C',str(SRC),'rev-parse','HEAD']),parent_campaign=PARENT,image_id=image,parent_image_id=parent['image_id'],
 sources={n:sha(SRC/n) for n in sorted(files) if (SRC/n).is_file()},inputs=parent['inputs'],matrix=matrix.conditions(),assignments=parent['assignments'],
 per_node_inflight=None,concurrency_cap=None,timeout_policy=pilot.timeout_policy(),loop_sleep_ms=25,seeds=list(range(1,11)),repetitions=2,formal_slots=1280,single_cluster_slots=960,federation_slots=320,
 transport_revision='peer-control-data-lanes-v2',runtime_tests=targets,allowed_source_changes=sorted(allowed),protocol_algorithms_unchanged=True,
 validation_rule='new target regression +6smoke +64pilot before new1280formal; no v1 selection reuse',epoch_ns=time.time_ns())
record['fingerprint']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
controller.dump(ROOT/'campaign.json',record);controller.dump(ROOT/'recovery/complete.json',dict(cleanup_complete=False,reason='target regression not yet qualified'))
print((ROOT/'python-tests.log').read_text(),flush=True)
print(json.dumps(dict(status='BUILT_NOT_STARTED',image=image,campaign=record['fingerprint'])),flush=True)
