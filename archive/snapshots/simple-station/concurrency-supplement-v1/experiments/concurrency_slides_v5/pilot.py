#!/usr/bin/env python3
"""Worker-c isolated Slides-matrix open-arrival campaign."""
import argparse,collections,csv,fcntl,hashlib,json,os,re,shutil,socket,subprocess,sys,time
from pathlib import Path
import controller as ctl
import matrix as mat
ROOT=Path(os.environ.get('NC_ROOT',str(Path(__file__).resolve().parents[3])));SOURCE=ROOT/'source';HERE=Path(__file__).resolve().parent
RAW=ROOT/'raw';LAB=ROOT/'lab';DERIVED=ROOT/'derived';IMAGE='rsync-router:concurrency-slides-v5';NS='concurrency-slides-v5'
WORK=None;SCENARIO=None;MAPPED=None;NODES=[]
UNIT=ROOT.name
def out(a):return subprocess.check_output(a,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def matrix():
 return mat.conditions()

def context(config):
 global WORK,SCENARIO,MAPPED,NODES,RAW
 fed=config['family']=='federation';WORK=ROOT/'inputs'/('federation' if fed else 'single')
 original=f"kua-dc-v4-federation-v4-bf1024-h3-formal-catalog{config['catalog']}-{config['distribution']}-seed{config['seed']:03d}" if fed else f"kua-dc-v4-{config['hosts']}-latency-v4-bf1024-h3-c512-{config['distribution']}-b{config['size']}-seed{config['seed']:03d}"
 MAPPED=WORK/'mapped'/original
 SCENARIO=original.replace('kua-dc-v4-federation-v4-bf1024-h3-formal','kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot') if fed else original
 text=(WORK/'scenarios'/f'{SCENARIO}.yaml').read_text()
 NODES=sorted(map(int,re.findall(r'^\s*-\s*id:\s*(\d+)\s*$',text,re.M)))
 assert NODES==list(range(1,config['hosts']+1))
 RAW=ROOT/'raw'/config['stage']
 return original

def timeout_policy():
 return dict(started_request_abort_seconds=None,slow_request_marker_seconds=8,unstarted_dispatch_abort_seconds=8,protocol_query_lifetime_seconds=30,protocol_query_lifetime_overrides=[7],origin_fallback_seconds=1.5,watchdog_seconds_after_last_arrival=60,serial_watchdog_seconds_after_current_release=60,tcp_direct_exchange_timeout_seconds=5,quiescence_timeout_seconds=20,quiescence_stable_seconds=.5)
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
 assert socket.gethostname() in set(os.environ.get('NC_ALLOWED_HOSTS','simpleifz,simpleai').split(','))
 assert out(['git','-C',str(SOURCE),'branch','--show-current'])==os.environ.get('NC_BRANCH','simple-station/concurrency-slides-v5')
 for revision in ('v1','v2','v3','v4'):
  assert out(['systemctl','show','networkcache-concurrency-slides-'+revision,'-p','ActiveState','--value']) in ('failed','inactive')
 import network_backend
 network_backend.preflight(ROOT)
 assert not out(['docker','ps','-q']),'worker not idle'
 assert out(['systemctl','show','networkcache-concurrency-pilot-v9','-p','ActiveState','--value']) in ('failed','inactive')
 assert out(['systemctl','show','networkcache-concurrency-pilot-v8','-p','ActiveState','--value']) in ('failed','inactive')
 assert out(['systemctl','show','networkcache-concurrency-pilot-v7','-p','ActiveState','--value']) in ('failed','inactive')
 assert out(['systemctl','show','networkcache-concurrency-pilot-v6','-p','ActiveState','--value']) in ('failed','inactive')
 assert out(['systemctl','show','networkcache-concurrency-pilot-v5','-p','ActiveState','--value']) in ('failed','inactive')
 assert out(['systemctl','show','networkcache-concurrency-pilot-v4','-p','ActiveState','--value']) in ('failed','inactive')
 assert out(['systemctl','show','networkcache-concurrency-pilot-v1','-p','ActiveState','--value']) in ('failed','inactive')
 assert out(['systemctl','show','networkcache-concurrency-pilot-v2','-p','ActiveState','--value']) in ('failed','inactive')
 assert out(['systemctl','show','networkcache-concurrency-pilot-v3-gate','-p','ActiveState','--value']) in ('failed','inactive')
 m=json.loads((ROOT/'campaign.json').read_text());digest=m.pop('fingerprint')
 assert hashlib.sha256(json.dumps(m,sort_keys=True).encode()).hexdigest()==digest,'campaign hash'
 assert sha(ROOT/'backend.json')==m['backend_sha256'],'backend fingerprint mismatch'
 assert m['matrix']==matrix() and m['per_node_inflight'] is None and m['concurrency_cap'] is None
 assert m['timeout_policy']==timeout_policy()
 assert m['loop_sleep_ms']==25
 assert out(['docker','image','inspect','--format','{{.Id}}',IMAGE])==m['image_id']
 for name,d in m['sources'].items():assert sha(SOURCE/name)==d,name
 for name,d in m['inputs'].items():assert sha(ROOT/'inputs'/name)==d,name
 bootstrap();mem=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
 assert mem>=32*1024**3 and shutil.disk_usage(ROOT).free>=200*1024**3
 assert json.loads((ROOT/'recovery/complete.json').read_text())['cleanup_complete']
 return dict(status='PREFLIGHT_OK',campaign=digest,image=m['image_id'],available_memory_bytes=mem,free_disk_bytes=shutil.disk_usage(ROOT).free)
def input_rows(config):
 context(config)
 original=ctl.read_tsv(MAPPED/'schedule.tsv');setup=[r for r in original if r['phase']=='setup'];measurement=[r for r in original if r['phase']!='setup']
 fed=config['family']=='federation'
 assert len(measurement)==mat.request_count(config)
 if fed:
  groups=[[r for r in measurement if r['phase']==phase] for phase in ('pre','post')]
  assert list(map(len,groups))==[4096,7680]
 elif config['stage']!='smoke':groups=[measurement]
 else:
  keys=[r for r in ctl.read_tsv(MAPPED/'catalog.tsv') if r['catalog_class']=='formal' and int(r['owner'])>=config['hosts']//2+1][:6]
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
def environment(lab,raw,method,digest,query_lifetime_seconds,loop_sleep_ms):
 assert query_lifetime_seconds in (30,7) and loop_sleep_ms==25
 return dict(os.environ,LAB_DIR=str(lab),IMAGE_TAG=IMAGE,SKIP_BUILD='1',SCENARIOS_DIR=str(WORK/'scenarios'),
  KATHARA_NODE_LOOP_SLEEP_MS=str(loop_sleep_ms),KATHARA_LINK_DELAY_MS='2',KATHARA_LINK_RATE_MBIT='1000',KATHARA_LINK_QUEUE_PACKETS='1000',KATHARA_LINK_MTU='1500',
  RSYNC_WIRE_VERSION='3',RSYNC_BLOOM_BITS='1024',RSYNC_BLOOM_HASH_COUNT='3',RSYNC_STAGED_FALLBACK='1',RSYNC_FALLBACK_DELAY_MS='1500',RSYNC_QUERY_TIMEOUT_MS=str(query_lifetime_seconds*1000),
  RSYNC_REDIS_TIMEOUT_MS='1000',RSYNC_REDIS_MAXMEMORY='0',RSYNC_REDIS_MAXMEMORY_POLICY='noeviction',RSYNC_REDIS_REQUIRE_EMPTY='1',RSYNC_ORACLE_UNICAST='0',
  RSYNC_LOOP_ITERATIONS='100000000',RSYNC_SKIP_APP='1' if method=='tcp-direct' else '0',RSYNC_EVENT_TRACE='1',RSYNC_EVENT_TRACE_FLUSH_EVERY='1',
  RSYNC_EVENT_TRACE_RUN_ID=raw.parent.name+'-'+raw.name,RSYNC_EXPERIMENT_CONTROL_DIR=f'/shared/{NS}/control',RSYNC_EXPERIMENT_DATA_DIR=f'/shared/{NS}/data',
  RSYNC_EXPERIMENT_SCHEDULE_HASH=digest,RUNTIME_PROVENANCE_FILE=str(raw/'runtime_image_id.before_launch'))
def containers_for(lab,expected=None):
 ids=out(['docker','ps','-aq','--filter','label=app=kathara']).splitlines()
 allc=[]
 for cid in ids:
  probe=subprocess.run(['docker','inspect',cid],capture_output=True,text=True)
  if probe.returncode==0:allc+=json.loads(probe.stdout)
 cs=[c for c in allc if any(m['Source']==str(lab/'shared') for m in c['Mounts'])]
 if expected is not None and len(cs)!=expected:raise ctl.InfrastructureError(f'expected {expected} lab containers, got {len(cs)}')
 return cs
class Health:
 def __init__(self,cs,method,helpers,raw):
  self.helpers=helpers;self.last=0;self.pids={};self.raw=raw
  if method=='nc':
   for c in cs:
    if int(c['Config']['Labels']['name'].removeprefix('node')) not in NODES:continue
    text=out(['docker','top',c['Id'],'-eo','pid,args'])
    candidates=[line.split(None,1)[0] for line in text.splitlines()[1:] if len(line.split(None,1))==2 and line.split(None,1)[1].startswith('/app/build/rsync-node-dev')]
    if len(candidates)!=1:raise ctl.InfrastructureError('cannot locate node host PID: '+c['Name'])
    pid=candidates[0];self.pids[pid]=Path('/proc',pid,'stat').read_text().split()[21]
   if len(self.pids)!=len(NODES):raise ctl.InfrastructureError('NC PID inventory mismatch')
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
  if {int(p.stem.removeprefix('node')) for p in (control/'ready').glob('node*.ready')}==set(NODES):return
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
_T95_DF2=4.302652729911275
def _ols_slope_ci(xs,ys):
 n=len(xs)
 if n<3:return None
 mx=sum(xs)/n;my=sum(ys)/n
 sxx=sum((x-mx)**2 for x in xs);sxy=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
 if sxx==0:return None
 slope=sxy/sxx;intercept=my-slope*mx
 resid=[y-(intercept+slope*x) for x,y in zip(xs,ys)]
 dof=n-2
 if dof<=0:return None
 s2=sum(r*r for r in resid)/dof
 se=(s2/sxx)**0.5
 return dict(slope=slope,se=se,ci_low=slope-_T95_DF2*se,ci_high=slope+_T95_DF2*se,dof=dof,intercept=intercept)
def warmup_gate(ledger,bins=4):
 """Warm-up gate for federation: both domains local latency over the last pre
 bins must have an OLS slope whose 95% t CI contains 0. Returns the evidence."""
 pre=[r for r in ledger if r.get('phase')=='pre']
 evidence={};reasons=[]
 all_bins=sorted({int(r['bin']) for r in pre})
 use=all_bins[-bins:]
 for domain,(lo,hi) in (('A',(1,32)),('B',(33,64))):
  rows=[r for r in pre if lo<=int(r['requester'])<=hi]
  per=[]
  for b in use:
   sel=[int(r['total_latency_ns']) for r in rows if int(r['bin'])==b and r.get('status')=='ok' and r.get('total_latency_ns')]
   if sel:per.append((b,sum(sel)/len(sel)))
  if len(per)<3:
   reasons.append(f'domain{domain}:insufficient_pre_bins={len(per)}');continue
  fit=_ols_slope_ci([q[0] for q in per],[q[1] for q in per])
  if fit is None:
   reasons.append(f'domain{domain}:degenerate');continue
  passed=fit['ci_low']<=0<=fit['ci_high']
  evidence[domain]=dict(bins=[q[0] for q in per],bin_mean_ns={str(q[0]):q[1] for q in per},
   plateau_ns=sum(q[1] for q in per)/len(per),slope_ns_per_bin=fit['slope'],se_ns_per_bin=fit['se'],
   ci_low_ns_per_bin=fit['ci_low'],ci_high_ns_per_bin=fit['ci_high'],dof=fit['dof'],contains_zero=passed)
  if not passed:reasons.append(f'domain{domain}:slope_ci_excludes_zero')
 return dict(passed=not reasons,reasons=reasons,bins_used=use,domains=evidence)
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
def validate_runtime_logs(logs,ttl,poll,reuse):
 for n in NODES:
  text=(logs/f'node{n}.log').read_text()
  for key,value in (('query_timeout_ms',ttl*1000),('fallback_delay_ms',1500),('loop_sleep_ms',poll),('pilot_connection_reuse',int(reuse))):
   assert re.search(r'\b'+key+r'\s*[:=]\s*'+str(value)+r'\b',text),f'node{n}: {key} mismatch'
def one(config,attempt):
 context(config)
 begin=time.monotonic_ns();raw=RAW/config['id']/f'attempt{attempt:03d}';raw.mkdir(parents=True,exist_ok=False)
 lab=LAB/config['stage']/config['id']/raw.name;shared=lab/'shared'/NS;control=shared/'control';control.mkdir(parents=True)
 helpers=[];handles=[];cs=[];ledger=[];timing={};probe_ids=set();result=dict(status='infrastructure_failure',reason='startup')
 setup,groups,digest=input_rows(config);ctl.table(raw/'schedule.csv',setup+[r for g in groups for r in g]);ctl.dump(raw/'config.json',config)
 shutil.copy2(ROOT/'campaign.json',raw/'campaign_snapshot.json')
 try:
  for name in ('commands','started','completions','ready','stopped','delivery-audit','quiesce','quiesced','fatal'):(control/name).mkdir()
  if config['reuse_connections']:ctl.atomic(control/'tcp-connection-reuse.enabled','enabled\n')
  ctl.dump(raw/'transport_policy.json',dict(reuse_connections=config['reuse_connections'],nodelay_changed=False,per_peer_frames_per_io_tick=1,ambiguous_frame_replay=False))
  ctl.atomic(control/'async-v2.enabled','enabled\n');shutil.copytree(MAPPED/'data',shared/'data');shutil.copy2(HERE/'tcp_node.py',shared/'tcp_node.py')
  ctl.dump(raw/'bootstrap_manifest.json',bootstrap(lab))
  shutil.copy2(MAPPED/'schedule.tsv',raw/'original_schedule.tsv');shutil.copy2(WORK/'scenarios'/f'{SCENARIO}.yaml',raw/'scenario.yaml')
  with (raw/'driver.stdout.log').open('w') as o,(raw/'driver.stderr.log').open('w') as e:
   env=environment(lab,raw,config['method'],digest,config['query_lifetime_seconds'],config['loop_sleep_ms'])
   if config['family']=='federation':env['RSYNC_FEDERATION_METHOD']=config['method']
   ctl.dump(raw/'environment.json',{k:v for k,v in env.items() if k.startswith(('RSYNC_','KATHARA_')) or k in ('IMAGE_TAG','SKIP_BUILD')})
   subprocess.run([str(SOURCE/'scripts/kathara/run_ruikekkk_scenario.sh'),SCENARIO],env=env,stdout=o,stderr=e,check=True,timeout=600)
  device_ids=set(re.findall(r'^(node\d+)\[image\]',(lab/'lab.conf').read_text(),re.M))
  if not device_ids:raise ctl.InfrastructureError('missing generated device inventory')
  cs=containers_for(lab,len(device_ids));ctl.dump(raw/'containers.before.json',cs)
  if {c['Config']['Labels']['name'] for c in cs}!=device_ids:raise ctl.InfrastructureError('device inventory mismatch')
  image=json.loads((ROOT/'campaign.json').read_text())['image_id'];assert all(c['Image']==image for c in cs)
  network=[]
  for c in cs:
   text=out(['docker','exec',c['Id'],'sh','-c','cat /proc/self/timens_offsets; ip -j link; ip -j route; tc -j qdisc'])
   if text.splitlines()[:2]!=Path('/proc/self/timens_offsets').read_text().strip().splitlines():raise ctl.InfrastructureError('monotonic namespace mismatch')
   network.append(dict(container=c['Id'],state=text))
  ctl.dump(raw/'network.before.json',network)
  if config['method']=='tcp-direct':
   for n in NODES:
    log=(raw/f'node{n}.helper.log').open('w');handles.append(log)
    helpers.append(subprocess.Popen(['kathara','exec','-d',str(lab),'--wait',f'node{n}','--','python3',f'/shared/{NS}/tcp_node.py',str(n),f'/shared/{NS}/data/node{n}.tsv',f'/shared/{NS}/control',digest,'--trace',f'/shared/{NS}/tcp-traces/node{n}.jsonl'],stdout=log,stderr=subprocess.STDOUT))
  wait_ready(control,helpers)
  if config['method']=='nc':validate_runtime_logs(lab/'shared/logs',config['query_lifetime_seconds'],config['loop_sleep_ms'],config['reuse_connections'])
  import network_backend
  network_backend.prepare_lab(raw,cs)
  network_backend.verify_lab(ROOT,lab,raw,cs)
  health=Health(cs,config['method'],helpers,raw);timing['startup_seconds']=(time.monotonic_ns()-begin)/1e9
  st=time.monotonic_ns()
  if setup:
   result,rows=ctl.execute(setup,control,raw/'setup',config['method'],'setup',arrival=[i*25_000_000 for i in range(len(setup))],group='setup',health=health,nodes=len(NODES));ledger+=rows
  else:
   ctl.quiesce(control,'800000000',health,nodes=len(NODES));result=dict(status='ok',reason='')
  timing['setup_seconds']=(time.monotonic_ns()-st)/1e9
  if result['status']=='ok':
   for i,group in enumerate(groups,1):
    if config['family']=='federation' and i==2:
     gstart=time.monotonic_ns();gate=warmup_gate(ledger);ctl.dump(raw/'warmup_gate.json',gate);timing['warmup_gate_seconds']=(time.monotonic_ns()-gstart)/1e9;timing['warmup_gate_passed']=gate['passed']
     if not gate['passed']:
      result=dict(status='warmup_gate_failed',reason=';'.join(gate['reasons']));break
     import federation
     bstart=time.monotonic_ns();probe_ids=federation.boundary(control,raw,MAPPED,config['method'],digest,health,lab/'shared/traces');timing['boundary_seconds']=(time.monotonic_ns()-bstart)/1e9;timing['activation_ok']=True
    result,rows=ctl.execute(group,control,raw/f'measurement-{i}',config['method'],config['model'],config['rate'],arrival=[j*1_000_000 for j in range(len(group))] if config['stage']=='smoke' and config['family']!='federation' else None,group=f'measurement-{i}',health=health,request_timeout_abort=config.get('request_timeout_abort',True),nodes=len(NODES),arrival_seed=config['arrival_seed']);ledger+=rows
    if result['status']!='ok':break
  if result['status']=='ok':
   ctl.validate_audit(control,ledger,probe_ids)
   if config['model'] in ('serial','serial-quiescent'):
    measured=[r for r in ledger if r.get('phase')=='measurement']
    if peak(measured).get('all',0)!=1:raise ctl.CorrectnessError('serial baseline overlap')
   elif config['stage']!='smoke' or config['family']=='federation':
    measured=[r for r in ledger if r.get('phase')!='setup']
    if peak(measured).get('all',0)<2:raise ctl.CorrectnessError('open arrival has no actual execution overlap')
   if config['stage']=='smoke' and config['family']!='federation':
    first=[r for r in ledger if r.get('phase')=='measurement' and r.get('bin')=='1'];peaks=peak(first,True)
    if max(peaks.values(),default=0)<2:raise ctl.CorrectnessError('no verified same-node overlap')
    last=[r for r in ledger if r.get('phase')=='measurement' and r.get('bin')=='4']
    if config['method']=='nc' and any(r['via']!='local_cache' for r in last):raise ctl.CorrectnessError('warm-cache smoke not served locally')
    ctl.dump(raw/'smoke_evidence.json',dict(same_node_peak=peaks,completed=len(ledger),audit='exactly_once',warm_key_via=[r['via'] for r in last]))
 except ctl.CorrectnessError as e:result=dict(status='correctness_failure',reason=str(e))
 except Exception as e:result=dict(status='controller_failure' if any((control/'commands').glob('node*/*.tsv')) else 'infrastructure_failure',reason=repr(e))
 finally:
  result.update(config=config,started_mono_ns=begin,measurement_finished_mono_ns=time.monotonic_ns(),timing=timing,requests_issued=any((control/'commands').glob('node*/*.tsv')))
  result['slots_in_parallel']=int(os.environ.get('RSYNC_SLOTS_IN_PARALLEL','0')) or None
  def stop():
   ctl.atomic(control/'stop','stop\n');deadline=time.monotonic()+15
   while time.monotonic()<deadline:
    if len(list((control/'stopped').glob('node*.stopped')))==len(NODES) and all(p.poll() is not None for p in helpers):break
    if not cs:break
    time.sleep(.1)
   current=containers_for(lab)
   if current:
    subprocess.run(['docker','stop','--time','5',*[c['Id'] for c in current]],capture_output=True,check=False,timeout=600)
   remaining=containers_for(lab)
   after=json.loads(out(['docker','inspect',*[c['Id'] for c in remaining]])) if remaining else []
   ctl.dump(raw/'containers.after.json',after)
   if any(c['State'].get('OOMKilled') for c in after):raise ctl.ProcessFailure('container OOM killed')
   if result['status']=='ok' and len(list((control/'stopped').glob('node*.stopped')))!=len(NODES):raise RuntimeError('graceful stop acknowledgement missing')
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
 report={};progress=[]
 for stage,target in (('smoke',6),('pilot',64),('formal',1280)):
  results=[]
  for c in mat.slots(stage):
   files=sorted((ROOT/'raw'/stage/c['id']).glob('attempt*/result.json'))
   if files:
    r=json.loads(files[-1].read_text());v=DERIVED/stage/c['id']/files[-1].parent.name/'validation.json'
    gate=json.loads(v.read_text()) if v.exists() else {}
    results.append(dict(slot=c['id'],status=r['status'],validated=gate.get('qualified',False),reason=gate.get('reason',r.get('reason')),wall_seconds=r.get('total_wall_seconds')))
  report[stage]=dict(target=target,qualified=sum(r['validated'] for r in results),finished=len(results),failed=[r for r in results if r['status']!='ok' or not r['validated']])
 for p in LAB.glob('*/*/attempt*/shared/concurrency-slides-v5/control/pilot-progress.json'):
  if not (ROOT/'raw'/p.parents[5].name/p.parents[4].name/p.parents[3].name/'result.json').exists():progress.append(dict(path=str(p),data=json.loads(p.read_text())))
 return dict(stages=report,progress=progress,unit=subprocess.run(['systemctl','show',UNIT,'-p','ActiveState','-p','SubState'],capture_output=True,text=True).stdout)
def analyze():
 import reports
 return reports.build(ROOT)
def complete_stage(stage):
 import reports
 for c in mat.slots(stage):
  paths=sorted((ROOT/'raw'/stage/c['id']).glob('attempt*/result.json'))
  if not paths or not reports.validate(paths[-1].parent,ROOT)['qualified']:return False
 return True
def capacity_budget():
 wall=bytes_needed=0
 for c in mat.slots('pilot'):
  p=sorted((ROOT/'raw/pilot'/c['id']).glob('attempt*/result.json'))[-1]
  r=json.loads(p.read_text());wall+=r['total_wall_seconds']*20
  trees=[p.parent,LAB/'pilot'/c['id']/p.parent.name]
  bytes_needed+=sum(f.stat().st_size for tree in trees for f in tree.rglob('*') if f.is_file())*20
 report=dict(remaining_formal_runs=1280,estimated_formal_seconds=wall,estimated_formal_bytes=bytes_needed,required_free_bytes=int(bytes_needed*1.3)+100*1024**3,free_bytes=shutil.disk_usage(ROOT).free)
 ctl.dump(DERIVED/'formal_budget.json',report)
 if report['free_bytes']<report['required_free_bytes']:raise RuntimeError('formal disk budget insufficient; no data deleted')
 return report
def queue(stage):
 import reports
 with (ROOT/'queue.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if stage in ('pilot','formal') and not complete_stage('smoke'):raise RuntimeError('six smoke gate incomplete')
  if stage=='formal':
   if not complete_stage('pilot'):raise RuntimeError('64 pilot gate incomplete')
   capacity_budget()
  for config in mat.slots(stage):
   context(config);paths=sorted((RAW/config['id']).glob('attempt*/result.json'));attempts=list((RAW/config['id']).glob('attempt*'))
   if len(attempts)!=len(paths):raise RuntimeError('unfinished attempt; manual investigation required')
   prior=[json.loads(p.read_text()) for p in paths]
   if paths and reports.validate(paths[-1].parent,ROOT)['qualified']:continue
   if len(attempts)>=2 or any(r.get('requests_issued') or r['status']!='infrastructure_failure' for r in prior):raise RuntimeError('failed measurement cannot automatically rerun')
   preflight();r=one(config,len(attempts)+1)
   if r['status']=='infrastructure_failure' and not r['requests_issued'] and not attempts:
    reports.diagnose(RAW/config['id']/'attempt001',ROOT);preflight();r=one(config,2)
   path=RAW/config['id']/f"attempt{len(list((RAW/config['id']).glob('attempt*'))):03d}"
   reports.diagnose(path,ROOT);gate=reports.validate(path,ROOT)
   ctl.dump(DERIVED/'checkpoint.json',dict(stage=stage,slot=config['id'],validation=gate,epoch_ns=time.time_ns()))
   if not gate['qualified']:
    analyze()
    raise RuntimeError('queue stopped: '+json.dumps(gate))
   print(json.dumps(dict(stage=stage,slot=config['id'],qualified=True,wall_seconds=r['total_wall_seconds'])),flush=True)
  return analyze()
def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=('preflight','smoke','pilot','run','campaign','status','analyze'));p.add_argument('--execute',action='store_true');a=p.parse_args()
 if a.command in ('smoke','pilot','run','campaign') and not a.execute:p.error('--execute required')
 if a.command=='campaign':
  for stage in ('smoke','pilot','formal'):result=queue(stage)
 elif a.command in ('smoke','pilot','run'):result=queue('formal' if a.command=='run' else a.command)
 else:result={'preflight':preflight,'status':status,'analyze':analyze}[a.command]()
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
