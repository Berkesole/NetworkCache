import hashlib,json,pathlib,shutil,socket,subprocess,sys,time
ROOT=pathlib.Path('/data/networkcache-concurrency-pilot-v8');OLD=pathlib.Path('/data/networkcache-concurrency-pilot-v7');SRC=ROOT/'source';STAGE=pathlib.Path(__file__).resolve().parent
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert socket.gethostname()=='simpleifz' and not out(['docker','ps','-q'])
assert out(['systemctl','show','networkcache-concurrency-pilot-v7','-p','ActiveState','--value'])=='inactive'
assert not out(['git','-C',str(OLD/'source'),'status','--porcelain'])
parent=json.loads((OLD/'campaign.json').read_text());assert parent['fingerprint']=='0f839e92a5d5532803210a10fbe823121b5dab176f037f1aa208f30665259722'
for n,d in parent['sources'].items():assert sha(OLD/'source'/n)==d,n
assert out(['docker','image','inspect','--format','{{.Id}}','rsync-router:concurrency-pilot-v4'])==parent['image_id']
assert subprocess.run(['docker','image','inspect','rsync-router:concurrency-pilot-v8'],capture_output=True).returncode!=0
ROOT.mkdir(exist_ok=False);(ROOT/'baseline').mkdir();(ROOT/'recovery').mkdir();shutil.copy2(OLD/'campaign.json',ROOT/'baseline/parent_campaign.json')
subprocess.run(['git','clone','--no-hardlinks',str(OLD/'source'),str(SRC)],check=True)
subprocess.run(['git','-C',str(SRC),'checkout','-b','codex/single-cluster-concurrency-pilot-v8'],check=True)
for key in ('user.name','user.email'):subprocess.run(['git','-C',str(SRC),'config',key,out(['git','-C',str(OLD/'source'),'config',key])],check=True)
for n in parent['sources']:
 p=SRC/n
 if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(OLD/'source'/n,p)
HERE=SRC/'experiments/concurrency_pilot_v8';HERE.mkdir()
for n in ('controller.py','pilot.py','analysis.py','tcp_node.py','test_pilot.py','test_transport.py','compare_cutoff.py','compare_transport.py','README.md','deploy.py'):shutil.copy2(STAGE/n,HERE/n)
allowed={'src/network/tcp_network.cpp','src/network/tcp_network.hpp'}
for n in allowed:shutil.copy2(STAGE/pathlib.Path(n).name,SRC/n)
shutil.copy2(STAGE/'test_tcp_reuse.cpp',SRC/'tests/test_tcp_reuse.cpp');shutil.copy2(STAGE/'Dockerfile.reuse',SRC/'Dockerfile.reuse')
for n,d in parent['sources'].items():
 if n not in allowed:assert sha(SRC/n)==d,n
with (ROOT/'python-tests.log').open('w') as h:subprocess.run(['python3','-m','unittest','discover','-s',str(HERE),'-p','test*.py'],stdout=h,stderr=subprocess.STDOUT,check=True)
sys.path.insert(0,str(HERE));import pilot,controller
baseline={}
for model in ('poisson','burst'):
 raw=OLD/'raw'/f'{model}-r128-nc-poll25'/'attempt001';r=json.loads((raw/'result.json').read_text());assert r['status']=='ok' and not r['finalization_errors']
 assert json.loads((raw/'measurement-1/result.json').read_text())['completed']==4096
 names=('campaign_snapshot.json','schedule.csv','environment.json','measurement-1/requests.csv','measurement-1/arrival_manifest.json','measurement-1/result.json','result.json')
 baseline[model]=dict(raw=str(raw),sha256={n:sha(raw/n) for n in names})
for c in pilot.matrix():
 setup,groups,digest=pilot.input_rows(c)
 if c['stage']=='smoke':assert sum(map(len,groups))==16;continue
 raw=pathlib.Path(baseline[c['model']]['raw']);prior=json.loads((raw/'measurement-1/arrival_manifest.json').read_text());arrival,phase=controller.offsets(4096,c['model'],128)
 assert prior['planned_offsets_ns']==arrival and prior['phase_seconds']==phase and prior['request_ids']==[r['request_index'] for r in groups[0]]
 assert all(r['schedule_sha256']==digest for r in __import__('csv').DictReader((raw/'measurement-1/requests.csv').open()))
subprocess.run(['git','-C',str(SRC),'add','--','experiments/concurrency_pilot_v8',*sorted(allowed),'tests/test_tcp_reuse.cpp','Dockerfile.reuse'],check=True)
subprocess.run(['git','-C',str(SRC),'commit','-qm','Add default-off TCP connection reuse and same-image gated pilot'],check=True)
print('PYTHON_TESTS', (ROOT/'python-tests.log').read_text(),flush=True)
with (ROOT/'build-runtime.log').open('w') as h:subprocess.run(['docker','build','--network','none','-f','Dockerfile.reuse','-t',pilot.IMAGE,'.'],cwd=SRC,stdout=h,stderr=subprocess.STDOUT,check=True)
targets=['test_core','test_packet_codec','test_query_table_fallback','test_node_staged_fallback','test_runtime_neighbor','test_tcp_reuse']
for target in targets:
 with (ROOT/(target+'.log')).open('w') as h:subprocess.run(['docker','run','--rm','--network','none','--entrypoint','/app/build/'+target,pilot.IMAGE],stdout=h,stderr=subprocess.STDOUT,check=True,timeout=120)
 print('RUNTIME_TEST_OK',target,flush=True)
build=dict(image_id=out(['docker','image','inspect','--format','{{.Id}}',pilot.IMAGE]),base_image_id=parent['image_id'],runtime_commit=out(['git','-C',str(SRC),'rev-parse','HEAD']),runtime_inputs={n:sha(SRC/n) for n in sorted(allowed|{'tests/test_tcp_reuse.cpp','Dockerfile.reuse'})},unchanged_protocol_files={n:sha(SRC/n) for n in ('src/node.cpp','src/node.hpp','src/query_table.hpp','src/union_window.hpp','src/bloomfilter.hpp','src/network/packet_codec.cpp','app/rsync_node_main.cpp')},tests=targets,epoch_ns=time.time_ns())
(ROOT/'runtime_build.json').write_text(json.dumps(build,indent=2)+'\n')
files=set(out(['git','-C',str(SRC),'ls-files']).splitlines())|set(parent['sources'])
record=dict(worker='worker-c',branch='codex/single-cluster-concurrency-pilot-v8',commit=build['runtime_commit'],image_id=build['image_id'],runtime_build=build,parent_campaign=parent['fingerprint'],runtime_rebuilt=True,sources={n:sha(SRC/n) for n in sorted(files) if (SRC/n).is_file()},matrix=pilot.matrix(),per_node_inflight=None,concurrency_cap=None,seed=1,repetitions=1,arrival_seed=9173,requests_per_load=4096,evidence='EXPLORATORY_CONNECTION_REUSE_ABLATION',baseline_inputs=baseline,timeout_policy=pilot.timeout_policy(),loop_sleep_ms=25,epoch_ns=time.time_ns())
record['fingerprint']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest();(ROOT/'campaign.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
(ROOT/'recovery/complete.json').write_text(json.dumps(dict(cleanup_complete=True,action='v7 inactive; test containers removed'))+'\n');print(json.dumps(pilot.preflight()),flush=True)
