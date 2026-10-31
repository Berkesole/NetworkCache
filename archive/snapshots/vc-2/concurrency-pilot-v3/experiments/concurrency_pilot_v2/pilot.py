#!/usr/bin/env python3
"""Worker-c v2: smoke then 12 open-arrival conditions, no formal statistics."""
import argparse,collections,csv,fcntl,hashlib,json,os,re,shutil,socket,subprocess,sys,time
from pathlib import Path
import controller as ctl
ROOT=Path('/data/networkcache-concurrency-pilot-v2');SOURCE=ROOT/'source';HERE=Path(__file__).resolve().parent
RAW=ROOT/'raw';LAB=ROOT/'lab';DERIVED=ROOT/'derived';IMAGE='rsync-router:concurrency-pilot-v2';NS='concurrency-pilot-v2'
WORK=SOURCE/'experiments/content_discovery/delay_find_content/workloads/latency-v4-bf1024-h3-c512'
SCENARIO='kua-dc-v4-32-latency-v4-bf1024-h3-c512-iid-zipf-b65536-seed001';MAPPED=WORK/'mapped'/SCENARIO
UNIT='networkcache-concurrency-pilot-v2'
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def matrix():
 rows=[dict(id='smoke-'+m,stage='smoke',method=m,model='diagnostic',rate=0) for m in ('nc','tcp-direct')]
 for i,(rate,model) in enumerate((r,m) for r in (8,32,128) for m in ('poisson','burst')):
  for method in (('nc','tcp-direct') if i%2==0 else ('tcp-direct','nc')):
   rows.append(dict(id=f'{model}-r{rate}-{method}',stage='load',method=method,model=model,rate=rate))
 return rows
def bootstrap(lab=None):
 evidence={}
 for name in ('start-node.sh','start-switch.sh'):
  p=SOURCE/'kathara/ruikekkk/shared'/name
  if not p.is_file() or not os.access(p,os.X_OK):raise ctl.InfrastructureError('missing executable '+name)
  subprocess.run(['sh','-n',str(p)],check=True,capture_output=True)
  if lab:
   target=lab/'shared'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
   assert sha(target)==sha(p) and os.access(target,os.X_OK)
  evidence[name]=sha(p)
 return evidence
def preflight():
 assert socket.gethostname()=='simpleifz'
 assert out(['git','-C',str(SOURCE),'branch','--show-current'])=='codex/single-cluster-concurrency-pilot-v2'
 assert not out(['docker','ps','-q']),'worker not idle'
 assert out(['systemctl','show','networkcache-concurrency-pilot-v1','-p','ActiveState','--value']) in ('failed','inactive')
 m=json.loads((ROOT/'campaign.json').read_text());digest=m.pop('fingerprint')
 assert hashlib.sha256(json.dumps(m,sort_keys=True).encode()).hexdigest()==digest,'campaign hash'
 assert m['matrix']==matrix() and m['per_node_inflight'] is None and m['concurrency_cap'] is None
 assert out(['docker','image','inspect','--format','{{.Id}}',IMAGE])==m['image_id']
 for name,d in m['sources'].items():assert sha(SOURCE/name)==d,name
 bootstrap();mem=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
 assert mem>=16*1024**3 and shutil.disk_usage(ROOT).free>=100*1024**3
 assert json.loads((ROOT/'recovery/complete.json').read_text())['cleanup_complete']
 return dict(status='PREFLIGHT_OK',campaign=digest,image=m['image_id'],available_memory_bytes=mem,free_disk_bytes=shutil.disk_usage(ROOT).free)
def input_rows(config):
 original=ctl.read_tsv(MAPPED/'schedule.tsv');setup=[r for r in original if r['phase']=='setup'];measurement=[r for r in original if r['phase']=='measurement']
 assert len(setup)==32 and len(measurement)==4096
 if config['stage']=='load':groups=[measurement]
 else:
  keys=[r for r in ctl.read_tsv(MAPPED/'catalog.tsv') if r['catalog_class']=='formal' and int(r['owner'])>=17][:6]
  assert len(keys)==6
  specs=[[(1,k) for k in keys[:4]],[(1,keys[4])]*4,[(n,keys[5]) for n in (1,2,3,4)],[(1,keys[0])]*4]
  groups=[];index=500000
  for g,spec in enumerate(specs,1):
   group=[]
   for node,key in spec:
    index+=1;group.append(dict(key,request_index=str(index),requester=str(node),phase='measurement',bin=str(g),round='0'))
   groups.append(group)
 digest=hashlib.sha256(json.dumps([setup,groups],sort_keys=True).encode()).hexdigest()
 for r in setup+[r for g in groups for r in g]:r['schedule_sha256']=digest
 return setup,groups,digest
def environment(lab,raw,method,digest):
 return dict(os.environ,LAB_DIR=str(lab),IMAGE_TAG=IMAGE,SKIP_BUILD='1',SCENARIOS_DIR=str(WORK/'scenarios'),
  KATHARA_NODE_LOOP_SLEEP_MS='25',KATHARA_LINK_DELAY_MS='2',KATHARA_LINK_RATE_MBIT='1000',KATHARA_LINK_QUEUE_PACKETS='1000',KATHARA_LINK_MTU='1500',
  RSYNC_WIRE_VERSION='3',RSYNC_BLOOM_BITS='1024',RSYNC_BLOOM_HASH_COUNT='3',RSYNC_STAGED_FALLBACK='1',RSYNC_FALLBACK_DELAY_MS='1500',RSYNC_QUERY_TIMEOUT_MS='7000',
  RSYNC_REDIS_TIMEOUT_MS='1000',RSYNC_REDIS_MAXMEMORY='0',RSYNC_REDIS_MAXMEMORY_POLICY='noeviction',RSYNC_REDIS_REQUIRE_EMPTY='1',RSYNC_ORACLE_UNICAST='0',
  RSYNC_LOOP_ITERATIONS='100000000',RSYNC_SKIP_APP='1' if method=='tcp-direct' else '0',RSYNC_EVENT_TRACE='1',RSYNC_EVENT_TRACE_FLUSH_EVERY='1',
  RSYNC_EVENT_TRACE_RUN_ID=raw.parent.name+'-'+raw.name,RSYNC_EXPERIMENT_CONTROL_DIR=f'/shared/{NS}/control',RSYNC_EXPERIMENT_DATA_DIR=f'/shared/{NS}/data',
  RSYNC_EXPERIMENT_SCHEDULE_HASH=digest,RUNTIME_PROVENANCE_FILE=str(raw/'runtime_image_id.before_launch'))
def containers_for(lab,expected=None):
 ids=out(['docker','ps','-aq','--filter','label=app=kathara']).splitlines()
 allc=json.loads(out(['docker','inspect',*ids])) if ids else []
 cs=[c for c in allc if any(m['Source']==str(lab/'shared') for m in c['Mounts'])]
 if expected is not None and len(cs)!=expected:raise ctl.InfrastructureError(f'expected {expected} lab containers, got {len(cs)}')
 return cs
class Health:
 def __init__(self,cs,method,helpers,raw):
  self.helpers=helpers;self.last=0;self.pids={};self.raw=raw
  if method=='nc':
   for c in cs:
    if int(c['Config']['Labels']['name'].removeprefix('node'))>32:continue
    text=out(['docker','top',c['Id'],'-eo','pid,args'])
    candidates=[line.split(None,1)[0] for line in text.splitlines()[1:] if len(line.split(None,1))==2 and line.split(None,1)[1].startswith('/app/build/rsync-node-dev')]
    if len(candidates)!=1:raise ctl.InfrastructureError('cannot locate node host PID: '+c['Name'])
    pid=candidates[0];self.pids[pid]=Path('/proc',pid,'stat').read_text().split()[21]
   if len(self.pids)!=32:raise ctl.InfrastructureError('expected 32 NC PIDs')
  ctl.dump(raw/'process-pids.json',self.pids)
 def __call__(self):
  now=time.monotonic()
  if now-self.last<.2:return
  self.last=now;problem=None
  if any(p.poll() is not None for p in self.helpers):problem='TCP helper exited'
  for pid,start in self.pids.items():
   try:
    f=Path('/proc',pid,'stat').read_text().split()
    if f[21]!=start or f[2]=='Z':problem='node process exited/replaced '+pid
   except FileNotFoundError:problem='node process disappeared '+pid
  if problem:
   ctl.dump(self.raw/'process-failure.json',dict(reason=problem,mono_ns=time.monotonic_ns()));raise ctl.ProcessFailure(problem)
def wait_ready(control,helpers):
 deadline=time.monotonic()+180
 while time.monotonic()<deadline:
  if any(p.poll() is not None for p in helpers):raise ctl.InfrastructureError('helper exited before ready')
  if len(list((control/'ready').glob('node*.ready')))==32:return
  time.sleep(.1)
 raise ctl.InfrastructureError('readiness timeout')
def is_temporary(name):return name.startswith('.') and ('.tmp' in name) or bool(re.search(r'\.tmp(?:\.|$)',name))
def copy_evidence(source,dest):
 shutil.copytree(source,dest,ignore=lambda _d,names:[n for n in names if is_temporary(n)])
 for p in source.rglob('*'):
  if p.is_file() and not is_temporary(p.name):
   q=dest/p.relative_to(source)
   if not q.is_file() or sha(p)!=sha(q):raise RuntimeError('unstable/missing evidence '+str(p))
def finalize(raw,result,stop,archive,clean):
 # A durable measurement conclusion survives stop, archive, or cleanup failure.
 ctl.dump(raw/'measurement_outcome.json',result);errors={};started=time.monotonic()
 try:
  try:stop()
  except Exception as e:errors['stop']=repr(e)
  if 'stop' not in errors:
   try:archive()
   except Exception as e:errors['archive']=repr(e)
  else:errors['archive']='not attempted while writers may be alive'
 finally:
  try:clean()
  except Exception as e:errors['cleanup']=repr(e)
 result=dict(result,measurement_status=result['status'],finalization_errors=errors,finalization_seconds=time.monotonic()-started)
 if errors:result['status']='finalization_failure'
 return result
def peak(rows,per_node=False):
 groups=collections.defaultdict(list)
 for r in rows:
  if r.get('node_start_mono_ns') and r.get('completed_mono_ns'):
   groups[r['requester'] if per_node else 'all'] += [(int(r['node_start_mono_ns']),1),(int(r['completed_mono_ns']),-1)]
 result={}
 for node,events in groups.items():
  active=maximum=0
  for _,delta in sorted(events):active+=delta;maximum=max(maximum,active)
  result[node]=maximum
 return result
def one(config,attempt):
 begin=time.monotonic_ns();raw=RAW/config['id']/f'attempt{attempt:03d}';raw.mkdir(parents=True,exist_ok=False)
 lab=LAB/config['id']/raw.name;shared=lab/'shared'/NS;control=shared/'control';control.mkdir(parents=True)
 helpers=[];handles=[];cs=[];ledger=[];timing={};result=dict(status='infrastructure_failure',reason='startup')
 setup,groups,digest=input_rows(config);ctl.table(raw/'schedule.csv',setup+[r for g in groups for r in g]);ctl.dump(raw/'config.json',config)
 shutil.copy2(ROOT/'campaign.json',raw/'campaign_snapshot.json')
 try:
  for name in ('commands','started','completions','ready','stopped','delivery-audit','quiesce','quiesced','fatal'):(control/name).mkdir()
  ctl.atomic(control/'async-v2.enabled','enabled\n');shutil.copytree(MAPPED/'data',shared/'data');shutil.copy2(HERE/'tcp_node.py',shared/'tcp_node.py')
  ctl.dump(raw/'bootstrap_manifest.json',bootstrap(lab))
  with (raw/'driver.stdout.log').open('w') as o,(raw/'driver.stderr.log').open('w') as e:
   subprocess.run([str(SOURCE/'scripts/kathara/run_ruikekkk_scenario.sh'),SCENARIO],env=environment(lab,raw,config['method'],digest),stdout=o,stderr=e,check=True,timeout=600)
  cs=containers_for(lab,44);ctl.dump(raw/'containers.before.json',cs)
  image=json.loads((ROOT/'campaign.json').read_text())['image_id'];assert all(c['Image']==image for c in cs)
  network=[]
  for c in cs:
   text=out(['docker','exec',c['Id'],'sh','-c','cat /proc/self/timens_offsets; ip -j link; ip -j route; tc -j qdisc'])
   if text.splitlines()[:2]!=Path('/proc/self/timens_offsets').read_text().strip().splitlines():raise ctl.InfrastructureError('monotonic namespace mismatch')
   network.append(dict(container=c['Id'],state=text))
  ctl.dump(raw/'network.before.json',network)
  if config['method']=='tcp-direct':
   for n in range(1,33):
    log=(raw/f'node{n}.helper.log').open('w');handles.append(log)
    helpers.append(subprocess.Popen(['kathara','exec','-d',str(lab),'--wait',f'node{n}','--','python3',f'/shared/{NS}/tcp_node.py',str(n),f'/shared/{NS}/data/node{n}.tsv',f'/shared/{NS}/control',digest,'--trace',f'/shared/{NS}/tcp-traces/node{n}.jsonl'],stdout=log,stderr=subprocess.STDOUT))
  wait_ready(control,helpers);health=Health(cs,config['method'],helpers,raw);timing['startup_seconds']=(time.monotonic_ns()-begin)/1e9
  st=time.monotonic_ns();result,rows=ctl.execute(setup,control,raw/'setup',config['method'],'setup',arrival=[i*25_000_000 for i in range(len(setup))],group='setup',health=health);ledger+=rows;timing['setup_seconds']=(time.monotonic_ns()-st)/1e9
  if result['status']=='ok':
   for i,group in enumerate(groups,1):
    result,rows=ctl.execute(group,control,raw/f'measurement-{i}',config['method'],config['model'],config['rate'],arrival=[j*1_000_000 for j in range(len(group))] if config['stage']=='smoke' else None,group=f'measurement-{i}',health=health);ledger+=rows
    if result['status']!='ok':break
  if result['status']=='ok':
   ctl.validate_audit(control,ledger)
   if config['stage']=='smoke':
    first=[r for r in ledger if r.get('phase')=='measurement' and r.get('bin')=='1'];peaks=peak(first,True)
    if max(peaks.values(),default=0)<2:raise ctl.CorrectnessError('no verified same-node overlap')
    last=[r for r in ledger if r.get('phase')=='measurement' and r.get('bin')=='4']
    if config['method']=='nc' and any(r['via']!='local_cache' for r in last):raise ctl.CorrectnessError('warm-cache smoke not served locally')
    ctl.dump(raw/'smoke_evidence.json',dict(same_node_peak=peaks,completed=len(ledger),audit='exactly_once',warm_key_via=[r['via'] for r in last]))
 except ctl.CorrectnessError as e:result=dict(status='correctness_failure',reason=str(e))
 except Exception as e:result=dict(status='controller_failure' if any((control/'commands').glob('node*/*.tsv')) else 'infrastructure_failure',reason=repr(e))
 finally:
  result.update(config=config,started_mono_ns=begin,measurement_finished_mono_ns=time.monotonic_ns(),timing=timing,requests_issued=any((control/'commands').glob('node*/*.tsv')))
  def stop():
   ctl.atomic(control/'stop','stop\n');deadline=time.monotonic()+15
   while time.monotonic()<deadline:
    if len(list((control/'stopped').glob('node*.stopped')))==32 and all(p.poll() is not None for p in helpers):break
    if not cs:break
    time.sleep(.1)
   current=containers_for(lab)
   if current:
    subprocess.run(['docker','stop','--time','5',*[c['Id'] for c in current]],capture_output=True,check=True,timeout=60)
    after=json.loads(out(['docker','inspect',*[c['Id'] for c in current]]));ctl.dump(raw/'containers.after.json',after)
    if any(c['State'].get('OOMKilled') for c in after):raise ctl.ProcessFailure('container OOM killed')
    if result['status']=='ok' and len(list((control/'stopped').glob('node*.stopped')))!=32:raise RuntimeError('graceful stop acknowledgement missing')
   for p in helpers:
    try:p.wait(timeout=5)
    except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
   for h in handles:h.close()
  def archive():
   sources=[(control,'control'),(lab/'shared/logs','node-logs'),(lab/'shared/generated','network-scripts')]
   trace=shared/'tcp-traces' if config['method']=='tcp-direct' else lab/'shared/traces'
   sources.append((trace,'tcp-traces' if config['method']=='tcp-direct' else 'traces'))
   for p,label in sources:
    if p.exists():copy_evidence(p,raw/label)
    elif result['requests_issued']:raise RuntimeError('required evidence missing '+label)
   for name in ('lab.conf','lab.dep'):
    if (lab/name).exists():shutil.copy2(lab/name,raw/name)
  def clean():
   with (raw/'cleanup.log').open('w') as h:subprocess.run(['kathara','lclean','-d',str(lab)],stdout=h,stderr=subprocess.STDOUT,check=True,timeout=120)
   if containers_for(lab):raise RuntimeError('lab containers remain')
  result=finalize(raw,result,stop,archive,clean);result['ended_mono_ns']=time.monotonic_ns();result['total_wall_seconds']=(result['ended_mono_ns']-begin)/1e9
  ctl.dump(raw/'result.json',result)
 return result
def can_skip(config,blocked):return config['rate']>blocked.get((config['method'],config['model']),float('inf'))
def status():
 results=[];progress=[]
 for p in sorted(RAW.glob('*/attempt*/result.json')):
  r=json.loads(p.read_text());results.append(dict(config=r['config']['id'],status=r['status'],reason=r.get('reason'),timing=r.get('total_wall_seconds')))
 for p in LAB.glob('*/attempt*/shared/concurrency-pilot-v2/control/pilot-progress.json'):
  if not (RAW/p.parents[4].name/p.parents[3].name/'result.json').exists():progress.append(dict(path=str(p),data=json.loads(p.read_text())))
 return dict(evidence='EXPLORATORY_PILOT_V2',target_smoke=2,target_load=12,results=results,progress=progress,unit=subprocess.run(['systemctl','show',UNIT,'-p','ActiveState','-p','SubState'],capture_output=True,text=True).stdout)
def analyze():
 import analysis
 return analysis.analyze(RAW,DERIVED)
def queue(smoke_only=False):
 with (ROOT/'queue.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);blocked={}
  for config in matrix():
   if smoke_only and config['stage']!='smoke':continue
   paths=sorted((RAW/config['id']).glob('attempt*/result.json'));prior=[json.loads(p.read_text()) for p in paths]
   if any(r['status'] not in ('ok','load_timeout','transport_failure','infrastructure_failure') for r in prior):raise RuntimeError('prior failure requires investigation')
   if prior and prior[-1]['status']=='ok':continue
   if prior and prior[-1]['status'] in ('load_timeout','transport_failure'):
    if config['stage']=='smoke':raise RuntimeError('failed smoke')
    blocked[config['method'],config['model']]=config['rate'];continue
   if can_skip(config,blocked):ctl.dump(DERIVED/'skipped'/f"{config['id']}.json",dict(config=config,reason='same method/model failed at lower rate'));continue
   attempts=list((RAW/config['id']).glob('attempt*'))
   if len(attempts)!=len(paths) or len(attempts)>=2 or any(r.get('requests_issued') for r in prior):raise RuntimeError('retry disallowed or unfinished raw')
   preflight();r=one(config,len(attempts)+1);analyze()
   if r['status']=='infrastructure_failure' and not r['requests_issued'] and not attempts:preflight();r=one(config,2);analyze()
   if r['status'] in ('load_timeout','transport_failure') and config['stage']=='load':blocked[config['method'],config['model']]=config['rate']
   elif r['status']!='ok':raise RuntimeError('pilot stopped: '+json.dumps(r))
  return analyze()
def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=('preflight','recover','smoke','pilot','status','analyze'));p.add_argument('--execute',action='store_true');a=p.parse_args()
 if a.command in ('recover','smoke','pilot') and not a.execute:p.error('--execute required')
 if a.command=='recover':
  import prepare
  result=prepare.recover()
 elif a.command in ('smoke','pilot'):result=queue(a.command=='smoke')
 else:result={'preflight':preflight,'status':status,'analyze':analyze}[a.command]()
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
