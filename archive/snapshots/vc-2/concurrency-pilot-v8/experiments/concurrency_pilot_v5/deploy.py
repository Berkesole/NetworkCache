"""Worker-c-only host policy branch; preserves every v4 runtime/source/raw byte."""
import hashlib,json,pathlib,shutil,socket,subprocess,sys,time
ROOT=pathlib.Path('/data/networkcache-concurrency-pilot-v5');OLD=pathlib.Path('/data/networkcache-concurrency-pilot-v4');SRC=ROOT/'source';STAGE=pathlib.Path(__file__).resolve().parent
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert socket.gethostname()=='simpleifz' and not out(['docker','ps','-q'])
assert out(['systemctl','show','networkcache-concurrency-pilot-v4','-p','ActiveState','--value'])=='inactive'
assert not out(['git','-C',str(OLD/'source'),'status','--porcelain'])
parent=json.loads((OLD/'campaign.json').read_text());assert parent['fingerprint']=='a8e846839c9886c2f34ab398fb2d1705ca722c325caad37321c810d2afe7981e'
for name,digest in parent['sources'].items():assert sha(OLD/'source'/name)==digest,name
assert out(['docker','image','inspect','--format','{{.Id}}','rsync-router:concurrency-pilot-v4'])==parent['image_id']
ROOT.mkdir(exist_ok=False);(ROOT/'baseline').mkdir();(ROOT/'recovery').mkdir()
shutil.copy2(OLD/'campaign.json',ROOT/'baseline/parent_campaign.json');shutil.copy2(OLD/'runtime_build.json',ROOT/'baseline/runtime_build.json')
subprocess.run(['git','clone','--no-hardlinks',str(OLD/'source'),str(SRC)],check=True)
subprocess.run(['git','-C',str(SRC),'checkout','-b','codex/single-cluster-concurrency-pilot-v5'],check=True)
for key in ('user.name','user.email'):subprocess.run(['git','-C',str(SRC),'config',key,out(['git','-C',str(OLD/'source'),'config',key])],check=True)
for name in parent['sources']:
 p=SRC/name
 if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/'source'/name,p)
HERE=SRC/'experiments/concurrency_pilot_v5';HERE.mkdir()
for name in ('controller.py','pilot.py','analysis.py','tcp_node.py','test_pilot.py','compare_cutoff.py','README.md','deploy.py'):
 shutil.copy2(STAGE/name,HERE/name)
# All previous tracked/ignored sources, including protocol and runtime, unchanged.
for name,digest in parent['sources'].items():assert sha(SRC/name)==digest,name
with (ROOT/'python-tests.log').open('w') as h:subprocess.run(['python3','-m','unittest','discover','-s',str(HERE),'-p','test_pilot.py'],stdout=h,stderr=subprocess.STDOUT,check=True)
sys.path.insert(0,str(HERE));import pilot,controller
baseline={}
for config in pilot.matrix():
 setup,groups,digest=pilot.input_rows(config);assert len(setup)==32 and len(groups)==1 and len(groups[0])==4096
 raw=OLD/'raw'/config['id']/'attempt001'
 schedule=controller.read_tsv(pilot.MAPPED/'schedule.tsv')
 prior=json.loads((raw/'measurement-1/arrival_manifest.json').read_text());arrival,phase=controller.offsets(4096,config['model'],128)
 assert prior['planned_offsets_ns']==arrival and prior['phase_seconds']==phase and prior['request_ids']==[r['request_index'] for r in groups[0]]
 assert all(r['schedule_sha256']==digest for r in __import__('csv').DictReader((raw/'measurement-1/requests.csv').open()))
 names=('campaign_snapshot.json','schedule.csv','measurement-1/requests.csv','measurement-1/arrival_manifest.json','measurement-1/result.json','result.json')
 baseline[config['id']]=dict(raw=str(raw),sha256={n:sha(raw/n) for n in names})
smokes={}
for name in ('smoke-nc','smoke-tcp-direct'):
 r=OLD/'raw'/name/'attempt001';assert json.loads((r/'result.json').read_text())['status']=='ok'
 smokes[name]=dict(raw=str(r),result_sha256=sha(r/'result.json'),evidence_sha256=sha(r/'smoke_evidence.json'))
subprocess.run(['git','-C',str(SRC),'add','--','experiments/concurrency_pilot_v5'],check=True)
subprocess.run(['git','-C',str(SRC),'commit','-qm','Add same-runtime ablation disabling only started-request eight-second abort'],check=True)
files=set(out(['git','-C',str(SRC),'ls-files']).splitlines())|set(parent['sources'])
record=dict(worker='worker-c',branch='codex/single-cluster-concurrency-pilot-v5',commit=out(['git','-C',str(SRC),'rev-parse','HEAD']),
 image_id=parent['image_id'],runtime_build=parent['runtime_build'],parent_campaign=parent['fingerprint'],runtime_rebuilt=False,sources={n:sha(SRC/n) for n in sorted(files) if (SRC/n).is_file()},matrix=pilot.matrix(),
 per_node_inflight=None,concurrency_cap=None,seed=1,repetitions=1,arrival_seed=9173,requests_per_load=4096,evidence='EXPLORATORY_TIMEOUT_ABLATION',baseline_inputs=baseline,reused_runtime_smokes=smokes,
 timeout_policy=dict(started_request_abort_seconds=None,slow_request_marker_seconds=8,unstarted_dispatch_abort_seconds=8,protocol_query_lifetime_seconds=7,origin_fallback_seconds=1.5,watchdog_seconds_after_last_arrival=60),epoch_ns=time.time_ns())
record['fingerprint']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
(ROOT/'campaign.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
(ROOT/'recovery/complete.json').write_text(json.dumps(dict(cleanup_complete=True,action='no cleanup needed: v4 completed, zero containers'))+'\n')
print('TESTS', (ROOT/'python-tests.log').read_text());print('PREFLIGHT',json.dumps(pilot.preflight()),flush=True)
