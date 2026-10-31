#!/usr/bin/env python3
"""Worker-c-only isolated pilot CLI; result status is never FORMAL READY."""
import argparse,collections,fcntl,hashlib,json,os,re,shutil,socket,subprocess,sys,time
from pathlib import Path
import controller as ctl

ROOT=Path('/data/networkcache-concurrency-pilot-v1')
REVISION='bootstrap-v2'
RAW=ROOT/('raw-'+REVISION); LAB=ROOT/('lab-'+REVISION); DERIVED=ROOT/('derived-'+REVISION)
SOURCE=ROOT/'source'; HERE=Path(__file__).resolve().parent
UP=SOURCE/'experiments/content_discovery/delay_find_content/scripts'
WORK=SOURCE/'experiments/content_discovery/delay_find_content/workloads/latency-v4-bf1024-h3-c512'
SCENARIO='kua-dc-v4-32-latency-v4-bf1024-h3-c512-iid-zipf-b65536-seed001'
MAPPED=WORK/'mapped'/SCENARIO
IMAGE='rsync-router:concurrency-pilot-v1'; NS='concurrency-pilot-v1'
sys.path.insert(0,str(UP))

def run(args,**kw):return subprocess.run(args,check=True,text=True,**kw)
def output(args):return subprocess.check_output(args,text=True).strip()
def check_bootstrap(source=SOURCE):
 files=[source/'kathara/ruikekkk/shared'/name for name in ('start-node.sh','start-switch.sh')]
 for path in files:
  if not path.is_file() or not os.access(path,os.X_OK):raise ctl.InfrastructureError('missing/non-executable lab bootstrap: '+str(path))
  run(['sh','-n',str(path)],capture_output=True)
 return files
def install_lab_support(lab,source=SOURCE):
 files=check_bootstrap(source);shared=lab/'shared';shared.mkdir(parents=True,exist_ok=True)
 evidence={}
 for path in files:
  target=shared/path.name;shutil.copy2(path,target)
  digest=hashlib.sha256(path.read_bytes()).hexdigest()
  if hashlib.sha256(target.read_bytes()).hexdigest()!=digest or not os.access(target,os.X_OK):raise ctl.InfrastructureError('bootstrap copy failed: '+str(target))
  evidence[path.name]=dict(source=str(path),destination=str(target),sha256=digest,executable=True)
 return evidence
def matrix():
 configs=[]
 for i,(stage,model,rate,cap) in enumerate([
  ('smoke','serial-quiet',0,1),('smoke','closed',0,1),('smoke','closed',0,4),
  *[('load',m,r,32) for r in (8,32,128) for m in ('poisson','burst')],('cold','wave',0,32)]):
  for method in (('nc','tcp-direct') if i%2==0 else ('tcp-direct','nc')):
   configs.append(dict(id=f'{i:02d}-{model}-r{rate}-c{cap}-{method}',stage=stage,model=model,rate=rate,cap=cap,method=method))
 return configs
def preflight(idle=True):
 assert socket.gethostname()=='simpleifz','wrong worker'
 assert SOURCE.resolve()==Path('/data/networkcache-concurrency-pilot-v1/source')
 assert output(['git','-C',str(SOURCE),'branch','--show-current'])=='codex/single-cluster-concurrency-pilot-v1'
 containers=output(['docker','ps','--format','{{.Names}}'])
 if idle and containers:raise RuntimeError('worker not idle: '+containers)
 mem=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
 assert mem>=16*1024**3 and shutil.disk_usage(ROOT).free>=100*1024**3,'resources'
 image=output(['docker','image','inspect','--format','{{.Id}}',IMAGE])
 manifest=json.loads((ROOT/'campaign.json').read_text())
 assert manifest['execution_revision']==REVISION and manifest['raw_root']==str(RAW),'execution revision mismatch'
 assert image==manifest['image_id'],'image changed'
 for name,digest in manifest['sources'].items():
  assert hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()==digest,name
 check_bootstrap()
 return dict(status='PREFLIGHT_OK',image=image,available_memory_bytes=mem,free_disk_bytes=shutil.disk_usage(ROOT).free)
def environment(lab,raw,method,schedule):
 return dict(os.environ,LAB_DIR=str(lab),IMAGE_TAG=IMAGE,SKIP_BUILD='1',SCENARIOS_DIR=str(WORK/'scenarios'),
  KATHARA_NODE_LOOP_SLEEP_MS='25',KATHARA_LINK_DELAY_MS='2',KATHARA_LINK_RATE_MBIT='1000',
  KATHARA_LINK_QUEUE_PACKETS='1000',KATHARA_LINK_MTU='1500',RSYNC_WIRE_VERSION='3',RSYNC_BLOOM_BITS='1024',
  RSYNC_BLOOM_HASH_COUNT='3',RSYNC_STAGED_FALLBACK='1',RSYNC_FALLBACK_DELAY_MS='1500',RSYNC_QUERY_TIMEOUT_MS='7000',
  RSYNC_REDIS_TIMEOUT_MS='1000',RSYNC_REDIS_MAXMEMORY='0',RSYNC_REDIS_MAXMEMORY_POLICY='noeviction',
  RSYNC_REDIS_REQUIRE_EMPTY='1',RSYNC_ORACLE_UNICAST='0',RSYNC_LOOP_ITERATIONS='100000000',
  RSYNC_SKIP_APP='1' if method=='tcp-direct' else '0',RSYNC_EVENT_TRACE='1',RSYNC_EVENT_TRACE_FLUSH_EVERY='1',
  RSYNC_EVENT_TRACE_RUN_ID=raw.parent.name+'-'+raw.name,
  RSYNC_EXPERIMENT_CONTROL_DIR=f'/shared/{NS}/control',RSYNC_EXPERIMENT_DATA_DIR=f'/shared/{NS}/data',
  RSYNC_EXPERIMENT_SCHEDULE_HASH=schedule,RUNTIME_PROVENANCE_FILE=str(raw/'runtime_image_id.before_launch'))
def input_rows(config):
 rows=ctl.read_tsv(MAPPED/'schedule.tsv');setup=[r for r in rows if r['phase']=='setup']
 measurement=[r for r in rows if r['phase']=='measurement']
 assert len(setup)==32 and len(measurement)==4096
 groups=[]
 if config['stage']=='cold':
  keys=[r for r in ctl.read_tsv(MAPPED/'catalog.tsv') if r['catalog_class']=='formal'][:4]
  for group,(width,key) in enumerate(zip((2,4,8,16),keys),1):
   selected=[n for n in range(1,33) if n!=int(key['owner'])][:width]
   groups.append([dict(key,request_index=str(500000+group*100+n),requester=str(n),phase='measurement',
                   bin=str(group),group_width=width,round='0') for n in selected])
 else:groups=[measurement[:256] if config['stage']=='smoke' else measurement]
 digest=hashlib.sha256(json.dumps([setup,groups],sort_keys=True).encode()).hexdigest()
 for r in setup+[r for g in groups for r in g]:r['schedule_sha256']=digest
 return setup,groups,digest
def wait_ready(control,processes):
 deadline=time.monotonic()+180
 while time.monotonic()<deadline:
  if any(p.poll() is not None for p in processes):raise ctl.InfrastructureError('helper exited before ready')
  if all((control/'ready'/f'node{n}.ready').exists() for n in range(1,33)):return
  time.sleep(.2)
 raise ctl.InfrastructureError('readiness timeout')
def container_snapshot(lab,raw,label):
 # Namespace equality, MTU, qdisc and route evidence; no assumed RTT threshold.
 lines=[]
 names=output(['docker','ps','--format','{{.Names}}']).splitlines()
 for name in names:
  text=output(['docker','exec',name,'sh','-c','cat /proc/self/timens_offsets; ip -j link; ip -j route; tc -j qdisc'])
  if text.splitlines()[:2]!=Path('/proc/self/timens_offsets').read_text().strip().splitlines():
   raise ctl.InfrastructureError('container monotonic namespace differs from controller')
  lines.append(dict(container=name,state=text))
 ctl.dump(raw/f'network-{label}.json',lines)
 if len(names)!=44:raise ctl.InfrastructureError(f'expected 44 containers, got {len(names)}')
 return names
def one(config,attempt):
 started=time.time_ns();started_mono=time.monotonic_ns();timing={}
 raw=RAW/config['id']/f'attempt{attempt:03d}';raw.mkdir(parents=True,exist_ok=False)
 lab=LAB/config['id']/f'attempt{attempt:03d}'
 shared=lab/'shared'/NS;control=shared/'control'
 control.mkdir(parents=True);(raw/'logs').mkdir()
 for n in ('commands','completions','ready','started','delivery-audit','quiesce','quiesced','stopped'): (control/n).mkdir()
 shutil.copytree(MAPPED/'data',shared/'data')
 for source,name in [(UP/'latency_v3_tcp_node.py','tcp_node.py'),(UP/'latency_v4_cache_probe.py','cache_probe.py')]:shutil.copy2(source,shared/name)
 setup,groups,digest=input_rows(config)
 ctl.table(raw/'schedule.csv',setup+[r for g in groups for r in g]);ctl.dump(raw/'config.json',config)
 shutil.copy2(ROOT/'campaign.json',raw/'campaign_snapshot.json')
 env=environment(lab,raw,config['method'],digest)
 processes=[];handles=[];ledger=[];result=dict(status='infrastructure_failure',reason='startup',requests_issued=False)
 names=[]
 try:
  ctl.dump(raw/'bootstrap_manifest.json',install_lab_support(lab))
  with (raw/'driver.stdout.log').open('w') as out,(raw/'driver.stderr.log').open('w') as err:
   run([str(SOURCE/'scripts/kathara/run_ruikekkk_scenario.sh'),SCENARIO],env=env,stdout=out,stderr=err,timeout=600)
  names=container_snapshot(lab,raw,'before')
  if config['method']=='tcp-direct':
   for n in range(1,33):
    log=(raw/'logs'/f'node{n}.helper.log').open('w');handles.append(log)
    processes.append(subprocess.Popen(['kathara','exec','-d',str(lab),'--wait',f'node{n}','--','python3',f'/shared/{NS}/tcp_node.py',str(n),
        f'/shared/{NS}/data/node{n}.tsv',f'/shared/{NS}/control',digest,'--trace',f'/shared/{NS}/tcp-traces/node{n}.jsonl'],stdout=log,stderr=subprocess.STDOUT))
  wait_ready(control,processes)
  timing['startup_seconds']=(time.monotonic_ns()-started_mono)/1e9
  setup_start=time.monotonic_ns()
  result,rows=ctl.execute(setup,control,raw/'setup',config['method'],'closed',cap=1,group='setup');ledger+=rows
  timing['setup_and_drain_seconds']=(time.monotonic_ns()-setup_start)/1e9
  if result['status']!='ok':return result
  for i,group in enumerate(groups):
   if config['stage']=='cold':
    pre=[]
    for request in group:
     node=int(request['requester']);matches=[n for n in names if re.search(rf'(?:^|_)node{node}(?:_|$)',n)]
     if len(matches)!=1:raise ctl.CorrectnessError('ambiguous requester container')
     cached=output(['docker','exec',matches[0],'redis-cli','--raw','-s','/run/redis/redis.sock','EXISTS',request['key']])
     if cached not in ('0','1'):raise ctl.CorrectnessError('invalid cold-cache probe')
     pre.append(dict(node=node,key=request['key'],cached=cached=='1'))
    ctl.dump(raw/f'cold-prestate-{i+1}.json',pre)
   mode='closed' if config['model']=='serial-quiet' else config['model']
   result,rows=ctl.execute(group,control,raw/f'measurement-{i+1}',config['method'],mode,
    config['rate'],config['cap'],config['model']=='serial-quiet',f'measurement-{i+1}');ledger+=rows
   if result['status']!='ok':break
  if result['status']=='ok':ctl.validate_audit(control,ledger)
  if result['status']=='ok' and config['stage']=='smoke' and config['cap']==4:
   measured=[r for r in ledger if r.get('phase')=='measurement' and r['status']=='ok']
   events=sorted([(int(r['node_start_mono_ns']),1) for r in measured]+[(int(r['completed_mono_ns']),-1) for r in measured])
   active=peak=0
   for _,delta in events:active+=delta;peak=max(peak,active)
   if peak<2:raise ctl.CorrectnessError('C4 smoke did not exercise actual concurrency')
 except ctl.CorrectnessError as e:result=dict(status='correctness_failure',reason=str(e))
 except Exception as e:
  issued=any((control/'commands').glob('node*/*.tsv'))
  result=dict(status='controller_failure' if issued else 'infrastructure_failure',reason=repr(e))
 finally:
  cleanup_start=time.monotonic_ns()
  result.update(config=config,started_epoch_ns=started,measurement_finished_epoch_ns=time.time_ns(),
    requests_issued=any((control/'commands').glob('node*/*.tsv')))
  ctl.atomic(control/'stop','stop\n')
  deadline=time.monotonic()+15
  for p in processes:
   try:p.wait(timeout=max(.1,deadline-time.monotonic()))
   except subprocess.TimeoutExpired:p.terminate()
  for h in handles:h.close()
  # Preserve scratch before cleanup. Never modify the attempt after returning.
  for path,label in [(control,'control'),(lab/'shared/traces','traces'),(lab/'shared/logs','node-logs'),(shared/'tcp-traces','tcp-traces'),(lab/'shared/generated','network-scripts')]:
   if path.exists():shutil.copytree(path,raw/label)
  for name in ('lab.conf','lab.dep'):
   if (lab/name).exists():shutil.copy2(lab/name,raw/name)
  with (raw/'cleanup.log').open('w') as out:
   subprocess.run(['kathara','lclean','-d',str(lab)],stdout=out,stderr=subprocess.STDOUT,timeout=120)
  timing['preserve_and_cleanup_seconds']=(time.monotonic_ns()-cleanup_start)/1e9
  timing['total_wall_seconds']=(time.monotonic_ns()-started_mono)/1e9
  result.update(ended_epoch_ns=time.time_ns(),timing=timing,execution_revision=REVISION)
  ctl.dump(raw/'result.json',result)
 return result

def percentile(values,p):
 if not values:return None
 s=sorted(values);i=(len(s)-1)*p;a=int(i);b=min(a+1,len(s)-1);return s[a]+(s[b]-s[a])*(i-a)
def summarize(raw):
 result=json.loads((raw/'result.json').read_text());rows=[];bounds=[]
 for f in sorted(raw.glob('measurement-*/requests.csv')):
  with f.open() as h:rows+=list(__import__('csv').DictReader(h))
 for f in sorted(raw.glob('measurement-*/result.json')):
  r=json.loads(f.read_text());bounds.append((r['base_mono_ns'],r['ended_mono_ns']))
 ok=[r for r in rows if r['status']=='ok'];out=dict(result=result,raw=str(raw),planned=len(rows),completed=len(ok),
  statuses=dict(collections.Counter(r['status'] for r in rows)),quantile_scope='completed only; failures retained in denominator; exploratory single seed',
  total_wall_seconds=result.get('timing',{}).get('total_wall_seconds',(result['ended_epoch_ns']-result['started_epoch_ns'])/1e9),timing=result.get('timing',{}))
 duration=sum(b-a for a,b in bounds)/1e9
 out['measurement_seconds']=duration
 out['configured_offered_rate']=result['config']['rate'] or None
 planned=[int(r['planned_mono_ns']) for r in rows if r.get('planned_mono_ns')]
 out['realized_schedule_rate']=len(planned)/((max(planned)-min(planned))/1e9) if planned and max(planned)>min(planned) else None
 cpu=[];memory=[];previous=None;max_queue=0
 for path in sorted(raw.glob('measurement-*/timeline.jsonl')):
  previous=None
  for line in path.open():
   sample=json.loads(line);values=list(map(int,sample['cpu'].split()[1:9]));total=sum(values);idle=values[3]+values[4]
   if previous and total>previous[0]:cpu.append(100*(1-(idle-previous[1])/(total-previous[0])))
   previous=(total,idle)
   memory.append(int(next(s.split()[1] for s in sample['meminfo'].splitlines() if s.startswith('MemAvailable:')))*1024)
   max_queue=max(max_queue,sample['queued'])
 out['host_resources']=dict(cpu_peak_percent=max(cpu,default=None),cpu_mean_percent=sum(cpu)/len(cpu) if cpu else None,
   minimum_available_memory_bytes=min(memory,default=None),max_ingress_queue=max_queue)
 for label,key in [('offered','admitted_mono_ns'),('issued','issued_mono_ns'),('completed','completed_mono_ns')]:
  out[label+'_rate']=sum(bool(r.get(key)) for r in rows)/duration if duration else None
 for key in ('total_latency_ns','execution_ns','node_queue_ns','scheduler_delay_ns'):
  values=[int(r[key])/1e6 for r in ok];out[key.replace('_ns','_ms')]=dict(mean=sum(values)/len(values) if values else None,
    p50=percentile(values,.5),p95=percentile(values,.95),p99=percentile(values,.99),count=len(values),tail_exploratory=len(values)<10000)
 events=sorted([(int(r['node_start_mono_ns']),1) for r in ok]+[(int(r['completed_mono_ns']),-1) for r in ok])
 active=peak=0
 for _,delta in events:active+=delta;peak=max(peak,active)
 out['peak_actual_inflight_completed_requests']=peak
 out['via']=dict(collections.Counter(r.get('via') for r in ok))
 if result['config']['stage']=='cold':
  waves=[]
  for group in sorted({r['bin'] for r in rows}):
   selected=[r for r in ok if r['bin']==group];starts=[int(r['node_start_mono_ns']) for r in selected];ends=[int(r['completed_mono_ns']) for r in selected]
   cold_file=raw/f'cold-prestate-{group}.json'
   cold=cold_file.exists() and all(not p['cached'] for p in json.loads(cold_file.read_text()))
   waves.append(dict(group=group,width=len([r for r in rows if r['bin']==group]),completed=len(selected),requesters_cold=cold,
    start_spread_ms=(max(starts)-min(starts))/1e6 if starts else None,
    all_overlap=bool(starts) and max(starts)<min(ends),valid=cold and len(selected)==len([r for r in rows if r['bin']==group]) and max(starts,default=0)<min(ends,default=0)))
  out['cold_waves']=waves
 traffic=collections.Counter();mechanisms=collections.Counter()
 for folder in ('traces','tcp-traces'):
  for f in (raw/folder).glob('*.jsonl'):
   for line in f.open():
    e=json.loads(line);ts=int(e.get('ts_mono_ns',0))
    if not any(a<=ts<=b for a,b in bounds):continue
    event=e.get('event','');mechanisms[event]+=1
    if event=='pilot_wire_send':
     traffic[e['packet_type']+'_frames']+=1;traffic[e['packet_type']+'_bytes']+=int(e.get('frame_bytes') or e.get('wire_bytes'))
 out['protocol_traffic']=dict(traffic);out['mechanism_event_counts']=dict(mechanisms)
 out['traffic_scope']='application protocol framing bytes, not IP bytes or TCP retransmission bytes'
 return out
def analyze():
 rows=[]
 for p in sorted(RAW.glob('*/attempt*/result.json')):rows.append(summarize(p.parent))
 ctl.dump(DERIVED/'report.json',dict(evidence='EXPLORATORY_PILOT',execution_revision=REVISION,runs=rows))
 lines=['# Single-cluster concurrency pilot','', 'Exploratory, one seed, no confidence intervals. Queue-inclusive latency is primary.','',
  '| Run | Status | Completed | Mean total ms | p95 ms | Peak concurrent | Wall s |','|---|---|---:|---:|---:|---:|---:|']
 for r in rows:
  m=r['total_latency_ms'];fmt=lambda x:'—' if x is None else f'{x:.2f}'
  lines.append(f"| {r['result']['config']['id']} | {r['result']['status']} | {r['completed']}/{r['planned']} | {fmt(m['mean'])} | {fmt(m['p95'])} | {r['peak_actual_inflight_completed_requests']} | {r['total_wall_seconds']:.1f} |")
 lines+=['','Failure denominators are retained; quantiles describe completed requests only.',
 'Actual next workload rates must be chosen from queue, generator lag, failure and host-resource evidence; no automated formal launch.']
 (DERIVED/'report.md').write_text('\n'.join(lines)+'\n')
 return dict(completed_attempts=len(rows),report=str(DERIVED/'report.md'))
def status():
 results=[]
 for p in sorted(RAW.glob('*/attempt*/result.json')):
  r=json.loads(p.read_text());results.append(dict(config=r['config']['id'],status=r['status'],reason=r.get('reason','')))
 progress=[]
 for p in LAB.glob('*/attempt*/shared/concurrency-pilot-v1/control/pilot-progress.json'):
  if not (RAW/p.parents[4].name/p.parents[3].name/'result.json').exists():progress.append(dict(path=str(p),data=json.loads(p.read_text())))
 return dict(evidence='EXPLORATORY_PILOT',execution_revision=REVISION,raw_root=str(RAW),prior_failed_raw_root=str(ROOT/'raw'),results=results,progress=progress,
   unit=subprocess.run(['systemctl','show','networkcache-concurrency-pilot-v1','-p','ActiveState','-p','SubState'],capture_output=True,text=True).stdout)
def queue(smoke_only=False):
 ROOT.mkdir(exist_ok=True)
 with (ROOT/'queue.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  blocked={}
  for config in matrix():
   if smoke_only and config['stage']!='smoke':continue
   existing=sorted((RAW/config['id']).glob('attempt*/result.json'))
   prior=[json.loads(p.read_text()) for p in existing]
   if any(r['status'] in ('correctness_failure','controller_failure') for r in prior):raise RuntimeError('prior correctness/controller failure requires investigation')
   if config['model'] in blocked and config['rate']>blocked[config['model']]:
    ctl.dump(DERIVED/'skipped'/f"{config['id']}.json",dict(reason='lower-rate timeout',config=config));continue
   if prior:
    r=prior[-1]
    if r['status']=='ok':continue
    if r['status']=='load_timeout':
     if config['stage']=='smoke':raise RuntimeError('smoke timeout')
     blocked[config['model']]=min(blocked.get(config['model'],config['rate']),config['rate']);continue
    if r.get('requests_issued') or len(prior)>=2:raise RuntimeError('retry disallowed')
   attempts=sorted((RAW/config['id']).glob('attempt*'))
   if len(attempts)!=len(prior):raise RuntimeError('unfinished raw attempt requires investigation')
   preflight()
   r=one(config,len(attempts)+1);analyze()
   if r['status']=='infrastructure_failure' and not r['requests_issued'] and not attempts:
    preflight();r=one(config,2);analyze()
   if r['status']=='load_timeout' and config['stage']!='smoke':blocked[config['model']]=min(blocked.get(config['model'],config['rate']),config['rate'])
   elif r['status']!='ok':raise RuntimeError(f"pilot halted: {config['id']} {r}")
  return analyze()
def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=('preflight','smoke','pilot','status','analyze'));p.add_argument('--execute',action='store_true');a=p.parse_args()
 if a.command in ('smoke','pilot'):
  if not a.execute:p.error('execution requires --execute')
  result=queue(a.command=='smoke')
 else:result={'preflight':preflight,'status':status,'analyze':analyze}[a.command]()
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
