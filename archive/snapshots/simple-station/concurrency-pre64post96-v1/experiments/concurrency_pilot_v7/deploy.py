"""Create only a fresh worker-c host-tool branch, preserving old evidence."""
import hashlib,json,pathlib,shutil,socket,subprocess,sys,time
ROOT=pathlib.Path('/data/networkcache-concurrency-pilot-v7');OLD=pathlib.Path('/data/networkcache-concurrency-pilot-v6');SRC=ROOT/'source';STAGE=pathlib.Path(__file__).resolve().parent
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert socket.gethostname()=='simpleifz' and not out(['docker','ps','-q'])
assert out(['systemctl','show','networkcache-concurrency-pilot-v6','-p','ActiveState','--value'])=='inactive'
assert not out(['git','-C',str(OLD/'source'),'status','--porcelain'])
parent=json.loads((OLD/'campaign.json').read_text());assert parent['fingerprint']=='71c1085b4866088ef9de9b8579aa2d9fea7facd4bced248171bc137494146954'
for name,d in parent['sources'].items():assert sha(OLD/'source'/name)==d,name
assert out(['docker','image','inspect','--format','{{.Id}}','rsync-router:concurrency-pilot-v4'])==parent['image_id']
ROOT.mkdir(exist_ok=False);(ROOT/'baseline').mkdir();(ROOT/'recovery').mkdir()
shutil.copy2(OLD/'campaign.json',ROOT/'baseline/parent_campaign.json')
subprocess.run(['git','clone','--no-hardlinks',str(OLD/'source'),str(SRC)],check=True)
subprocess.run(['git','-C',str(SRC),'checkout','-b','codex/single-cluster-concurrency-pilot-v7'],check=True)
for key in ('user.name','user.email'):subprocess.run(['git','-C',str(SRC),'config',key,out(['git','-C',str(OLD/'source'),'config',key])],check=True)
for name in parent['sources']:
 p=SRC/name
 if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/'source'/name,p)
HERE=SRC/'experiments/concurrency_pilot_v7';HERE.mkdir()
for name in ('controller.py','pilot.py','analysis.py','tcp_node.py','test_pilot.py','test_polling.py','compare_cutoff.py','compare_polling.py','README.md','deploy.py'):shutil.copy2(STAGE/name,HERE/name)
for name,d in parent['sources'].items():assert sha(SRC/name)==d,name
with (ROOT/'python-tests.log').open('w') as h:subprocess.run(['python3','-m','unittest','discover','-s',str(HERE),'-p','test*.py'],stdout=h,stderr=subprocess.STDOUT,check=True)
sys.path.insert(0,str(HERE));import pilot,controller
baseline={}
for model in ('poisson','burst'):
 raw=OLD/'raw'/f'{model}-r128-nc-ttl30'/'attempt001'
 assert json.loads((raw/'result.json').read_text())['status']=='ok'
 assert json.loads((raw/'measurement-1/result.json').read_text())['completed']==4096
 assert json.loads((raw/'result.json').read_text())['finalization_errors']=={}
 names=('campaign_snapshot.json','schedule.csv','environment.json','measurement-1/requests.csv','measurement-1/arrival_manifest.json','measurement-1/result.json','result.json')
 baseline[model]=dict(raw=str(raw),sha256={n:sha(raw/n) for n in names})
for config in pilot.matrix():
 setup,groups,digest=pilot.input_rows(config);assert len(groups[0])==4096
 raw=pathlib.Path(baseline[config['model']]['raw']);prior=json.loads((raw/'measurement-1/arrival_manifest.json').read_text())
 arrival,phase=controller.offsets(4096,config['model'],128)
 assert prior['planned_offsets_ns']==arrival and prior['phase_seconds']==phase and prior['request_ids']==[r['request_index'] for r in groups[0]]
 assert all(r['schedule_sha256']==digest for r in __import__('csv').DictReader((raw/'measurement-1/requests.csv').open()))
 env=pilot.environment(ROOT/'lab/check',ROOT/'raw/check/attempt001','nc',digest,30,config['loop_sleep_ms'])
 env={k:v for k,v in env.items() if k.startswith(('RSYNC_','KATHARA_')) or k in ('IMAGE_TAG','SKIP_BUILD')}
 oldenv=json.loads((raw/'environment.json').read_text())
 allowed={'KATHARA_NODE_LOOP_SLEEP_MS','RSYNC_EVENT_TRACE_RUN_ID','RSYNC_EXPERIMENT_CONTROL_DIR','RSYNC_EXPERIMENT_DATA_DIR'}
 assert {k:v for k,v in env.items() if k not in allowed}=={k:v for k,v in oldenv.items() if k not in allowed},'unexpected environment delta'
subprocess.run(['git','-C',str(SRC),'add','--','experiments/concurrency_pilot_v7'],check=True)
subprocess.run(['git','-C',str(SRC),'commit','-qm','Add isolated 25/5/1ms polling sensitivity at fixed 30s query lifetime'],check=True)
files=set(out(['git','-C',str(SRC),'ls-files']).splitlines())|set(parent['sources'])
record=dict(worker='worker-c',branch='codex/single-cluster-concurrency-pilot-v7',commit=out(['git','-C',str(SRC),'rev-parse','HEAD']),image_id=parent['image_id'],runtime_build=parent['runtime_build'],parent_campaign=parent['fingerprint'],runtime_rebuilt=False,
 sources={n:sha(SRC/n) for n in sorted(files) if (SRC/n).is_file()},matrix=pilot.matrix(),per_node_inflight=None,concurrency_cap=None,seed=1,repetitions=1,arrival_seed=9173,requests_per_load=4096,evidence='EXPLORATORY_POLLING_ABLATION',baseline_inputs=baseline,reused_runtime_smokes=parent['reused_runtime_smokes'],timeout_policy=pilot.timeout_policy(),loop_sleep_ms=[25,5,1],epoch_ns=time.time_ns())
record['fingerprint']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
(ROOT/'campaign.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
(ROOT/'recovery/complete.json').write_text(json.dumps(dict(cleanup_complete=True,action='old v6 inactive, no containers; no cleanup necessary'))+'\n')
print((ROOT/'python-tests.log').read_text());print(json.dumps(pilot.preflight()),flush=True)
