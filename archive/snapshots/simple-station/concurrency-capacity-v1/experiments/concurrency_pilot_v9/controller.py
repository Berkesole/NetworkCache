"""Open arrival with no concurrency cap. All application IDs remain independent."""
import collections,csv,json,math,os,random,time
from pathlib import Path
ARRIVAL_SEED=9173
class CorrectnessError(RuntimeError):pass
class LoadTimeout(RuntimeError):pass
class TransportFailure(RuntimeError):pass
class ProcessFailure(RuntimeError):pass
class InfrastructureError(RuntimeError):pass
def atomic(path,text):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name('.'+path.name+'.tmp');tmp.write_text(text);tmp.replace(path)
def dump(path,value):atomic(path,json.dumps(value,indent=2,sort_keys=True)+'\n')
def table(path,rows):
 if not rows:return
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader();w.writerows(rows)
def read_tsv(path):
 with Path(path).open() as f:return list(csv.DictReader(f,delimiter='\t'))
def offsets(count,model,rate,seed=ARRIVAL_SEED):
 rng=random.Random(seed);phase=rng.random()*5 if model=='burst' else 0.0
 elapsed=0.0;out=[]
 for _ in range(count):
  hazard=rng.expovariate(1)
  if model=='poisson':elapsed+=hazard/rate
  elif model=='burst':
   while hazard>0:
    pos=(elapsed+phase)%5;high=pos>=4;span=(5 if high else 4)-pos;intensity=rate*(4 if high else .25)
    if hazard<=span*intensity:elapsed+=hazard/intensity;break
    elapsed+=span+1e-12;hazard-=span*intensity
  else:raise ValueError(model)
  out.append(round(elapsed*1e9))
 return out,phase
class Engine:
 def __init__(self,requests,base,arrival,serial=False,request_timeout_abort=True,per_request_quiescence=False):
  if len(requests)!=len(arrival) or any(a>b for a,b in zip(arrival,arrival[1:])):raise CorrectnessError('arrival shape/order')
  self.request_timeout_abort=request_timeout_abort
  self.serial=serial;self.per_request_quiescence=per_request_quiescence;self.release_after_quiet=None
  if per_request_quiescence and not serial:raise CorrectnessError('quiescence requires serial release')
  self.base=base;self.next=0;self.active={};self.done={};self.queued=collections.deque()
  self.rows=[dict(r,status='not_arrived',planned_mono_ns=base+arrival[i],admitted_mono_ns='',issued_mono_ns='',node_start_mono_ns='',completed_mono_ns='',observed_completion_mono_ns='',terminal_mono_ns='',scheduler_delay_ns='',node_queue_ns='',execution_ns='',total_latency_ns='') for i,r in enumerate(requests)]
  self.byid={r['request_index']:r for r in self.rows}
  if len(self.byid)!=len(self.rows):raise CorrectnessError('duplicate application ID')
  self.watchdog=base+arrival[-1]+60_000_000_000
  if serial:
   for r in self.rows[1:]:r['planned_mono_ns']=''
 def admit(self,now):
  if self.serial:
   if self.active or self.queued or self.next>=len(self.rows):return
   r=self.rows[self.next]
   eligible=self.base if self.next==0 else int(self.rows[self.next-1]['completed_mono_ns'])
   if self.per_request_quiescence and self.next:
    if self.release_after_quiet is None:return
    eligible=self.release_after_quiet
   if now<eligible:return
   r.update(planned_mono_ns=eligible,status='queued',admitted_mono_ns=now)
   self.queued.append(r);self.next+=1;self.release_after_quiet=None;return
  while self.next<len(self.rows) and self.rows[self.next]['planned_mono_ns']<=now:
   r=self.rows[self.next];r.update(status='queued',admitted_mono_ns=now);self.queued.append(r);self.next+=1
 def dispatch(self,now):
  result=[]
  while self.queued:
   r=self.queued.popleft();r.update(status='issued',issued_mono_ns=now);self.active[r['request_index']]=r;result.append(r)
  return result
 def started(self,index,node,stamp):
  r=self.active.get(index)
  if not r or int(r['requester'])!=node or stamp<int(r['issued_mono_ns']):raise CorrectnessError('unknown/mismatched start')
  if r['node_start_mono_ns'] and r['node_start_mono_ns']!=stamp:raise CorrectnessError('changed start')
  r['node_start_mono_ns']=stamp
 def complete(self,f,now):
  if len(f)<11:raise CorrectnessError('malformed completion')
  index,node,key,status,size,digest,start,end,wire,via,schedule=f[:11]
  r=self.active.get(index)
  if index in self.done or not r:raise CorrectnessError('duplicate/unknown completion '+index)
  if (node,key,schedule)!=(str(r['requester']),r['key'],r['schedule_sha256']):raise CorrectnessError('identity mismatch '+index)
  start,end=int(start),int(end)
  if not int(r['issued_mono_ns'])<=start<=end<=now:raise CorrectnessError('completion clock order')
  if r['node_start_mono_ns'] and r['node_start_mono_ns']!=start:raise CorrectnessError('start marker mismatch')
  r.update(node_start_mono_ns=start,observed_completion_mono_ns=now,terminal_mono_ns=end,via=via,packet_request_id=wire,origin_query_uid=f[12] if len(f)>12 else '')
  if status in ('io_error','transport_error'):
   r['status']='transport_error';raise TransportFailure(index)
  if status!='ok' or (size,digest)!=(str(r['value_size_bytes']),r['expected_fnv1a64']):
   r['status']='integrity_error';raise CorrectnessError('completion value/status '+index)
  r.update(status='ok',completed_mono_ns=end,scheduler_delay_ns=int(r['admitted_mono_ns'])-r['planned_mono_ns'],
   node_queue_ns=start-int(r['admitted_mono_ns']),execution_ns=end-start,total_latency_ns=end-r['planned_mono_ns'])
  self.done[index]=r;del self.active[index]
 def check_timeout(self,now):
  for r in self.active.values():
   if now-int(r['node_start_mono_ns'] or r['issued_mono_ns'])>8_000_000_000:
    if r['node_start_mono_ns'] and not self.request_timeout_abort:
     r.setdefault('over_8s_observed_mono_ns',now)
     continue
    r.update(status='request_timeout' if r['node_start_mono_ns'] else 'dispatch_timeout',terminal_mono_ns=now)
    raise LoadTimeout(r['status']+' '+r['request_index'])
  if self.serial and self.active and now-max(int(r['planned_mono_ns']) for r in self.active.values())>60_000_000_000:raise LoadTimeout('serial last-release watchdog')
  if not self.serial and now>self.watchdog and len(self.done)<len(self.rows):raise LoadTimeout('global watchdog')
 def finish(self,reason,now):
  for r in self.rows:
   r['observation_end_mono_ns']=now
   if r['status']=='issued':r['status']='incomplete'
   elif r['status']=='queued':r['status']='not_issued'
   elif r['status']=='not_arrived':r['status']='not_released_at_abort' if self.serial else ('not_arrived_at_abort' if r['planned_mono_ns']>now else 'not_issued')
   if r['status']!='ok':r['termination_reason']=reason
 def snapshot(self,now):
  per=collections.defaultdict(lambda:dict(inflight=0,pending_start=0))
  for r in self.active.values():per[str(r['requester'])]['inflight' if r['node_start_mono_ns'] else 'pending_start']+=1
  return dict(mono_ns=now,admitted=self.next,completed=len(self.done),outstanding=len(self.active),per_node=dict(per),
   queued=len(self.queued),cpu=Path('/proc/stat').read_text().splitlines()[0],meminfo=Path('/proc/meminfo').read_text(),loadavg=os.getloadavg())
def quiesce(control,ident,health=None,timeout=20):
 marker=control/'quiesce'/f'{ident}.marker';atomic(marker,ident+'\n');paths=[control/'quiesced'/f'{ident}-node{n}.ack' for n in range(1,33)]
 deadline=time.monotonic()+timeout
 try:
  while time.monotonic()<deadline:
   if health:health()
   try:
    before=[(p.stat().st_mtime_ns,p.read_bytes()) for p in paths];time.sleep(.5)
    if before==[(p.stat().st_mtime_ns,p.read_bytes()) for p in paths]:return
   except FileNotFoundError:pass
   time.sleep(.01)
  raise LoadTimeout('phase quiescence timeout')
 finally:
  marker.unlink(missing_ok=True)
  for p in paths:p.unlink(missing_ok=True)
def execute(requests,control,output,method,model,rate=0,arrival=None,group='measurement',health=None,request_timeout_abort=True):
 phase=0
 serial=model in ('serial','serial-quiescent');strict=model=='serial-quiescent'
 if arrival is None:
  if serial:arrival=[0]*len(requests)
  else:arrival,phase=offsets(len(requests),model,rate)
 base=time.monotonic_ns()+100_000_000;engine=Engine(requests,base,arrival,serial=serial,request_timeout_abort=request_timeout_abort,per_request_quiescence=strict);output.mkdir(parents=True,exist_ok=True)
 dump(output/'timeout_policy.json',dict(started_request_abort_seconds=8 if request_timeout_abort else None,slow_request_marker_seconds=8,unstarted_dispatch_abort_seconds=8,watchdog_seconds_after_last_arrival=60,protocol_query_lifetime_source='../config.json and ../environment.json; checked against all 32 runtime logs'))
 dump(output/'arrival_manifest.json',dict(model=model,rate=rate,seed=ARRIVAL_SEED,phase_seconds=phase,planned_offsets_ns=None if serial else arrival,release_rule='previous_completion_then_global_quiescence_500ms' if strict else ('previous_application_completion_no_quiescence' if serial else 'frozen_open_arrival'),request_ids=[r['request_index'] for r in requests],base_mono_ns=base,clock='monotonic',concurrency_cap=1 if serial else None))
 status='ok';reason='';last_sample=0;started_seen=set();loop_finished=None;quiet_events=[]
 with (output/'timeline.jsonl').open('w') as timeline:
  try:
   while len(engine.done)<len(engine.rows):
    now=time.monotonic_ns()
    if now<base:time.sleep(.001);continue
    if health:health()
    if list((control/'fatal').glob('*')):raise CorrectnessError('runtime fatal diagnostic')
    engine.admit(now)
    for index,r in list(engine.active.items()):
     p=control/'started'/f'{index}.tsv'
     if p.exists() and index not in started_seen:
      f=p.read_text().strip().split('\t')
      if f[0]!=index:raise CorrectnessError('start file ID mismatch')
      engine.started(index,int(f[1]),int(f[2]));started_seen.add(index)
     p=control/'completions'/f'{index}.tsv'
     if p.exists():
      engine.complete(p.read_text().strip().split('\t'),time.monotonic_ns())
      if strict and len(engine.done)<len(engine.rows):
       qstart=time.monotonic_ns()
       try:quiesce(control,'700000'+index,health)
       finally:
        qend=time.monotonic_ns();event=dict(after_request_id=index,start_mono_ns=qstart,end_mono_ns=qend,seconds=(qend-qstart)/1e9)
        quiet_events.append(event)
        with (output/'inter_request_quiescence.jsonl').open('a') as qlog:qlog.write(json.dumps(event)+'\n')
       engine.release_after_quiet=qend
    engine.check_timeout(time.monotonic_ns())
    for r in engine.dispatch(time.monotonic_ns()):
     # Timestamp each actual filesystem dispatch, not the start of a large loop.
     r['issued_mono_ns']=time.monotonic_ns()
     text='\t'.join([r['request_index'],method,r['key'],str(r['owner']),str(r['value_size_bytes']),r['expected_fnv1a64'],r['schedule_sha256']])+'\n'
     atomic(control/'commands'/f"node{r['requester']}"/f"{int(r['request_index']):06d}.tsv",text)
    now=time.monotonic_ns()
    if now-last_sample>=100_000_000:
     sample=engine.snapshot(now);timeline.write(json.dumps(sample)+'\n');timeline.flush()
     dump(control/'pilot-progress.json',dict(group=group,model=model,planned=len(requests),admitted=engine.next,completed=len(engine.done),outstanding=len(engine.active),per_node=sample['per_node']));last_sample=now
    time.sleep(.002)
   loop_finished=time.monotonic_ns();quiesce(control,'800000'+requests[-1]['request_index'],health)
  except CorrectnessError as e:status='correctness_failure';reason=str(e)
  except LoadTimeout as e:status='load_timeout';reason=str(e)
  except TransportFailure as e:status='transport_failure';reason=str(e)
  except ProcessFailure as e:status='process_failure';reason=str(e)
  except Exception as e:status='controller_failure';reason=repr(e)
  finally:
   end=time.monotonic_ns();engine.finish(reason,end);table(output/'requests.csv',engine.rows)
   record=dict(status=status,reason=reason,planned=len(requests),admitted=engine.next,completed=len(engine.done),base_mono_ns=base,ended_mono_ns=end,
    request_timeout_abort=request_timeout_abort,
    inter_request_quiescence_count=len(quiet_events),inter_request_quiescence_seconds=sum(e['seconds'] for e in quiet_events),
    observed_started_over_8s=sum(bool(r.get('over_8s_observed_mono_ns')) for r in engine.rows),
    completed_execution_over_8s=sum(r['status']=='ok' and int(r['execution_ns'])>8_000_000_000 for r in engine.rows),
    model=model,rate=rate,group=group,last_planned_mono_ns=max((r['planned_mono_ns'] for r in engine.rows if r['planned_mono_ns']!=''),default=base),last_completion_mono_ns=max((r['completed_mono_ns'] for r in engine.rows if r['completed_mono_ns']),default=None),
    all_completions_observed_mono_ns=loop_finished,final_quiescence_seconds=(end-loop_finished)/1e9 if loop_finished else None)
   dump(output/'result.json',record)
 return record,engine.rows
def validate_audit(control,rows):
 counts=collections.Counter()
 for p in (control/'delivery-audit').glob('*.tsv'):
  for line in p.read_text().splitlines():counts[line.split('\t')[0]]+=1
 expected={r['request_index'] for r in rows if r['status']=='ok'}
 if set(counts)!=expected or any(v!=1 for v in counts.values()):raise CorrectnessError('delivery audit differs from successful application IDs')
 actual={p.stem for p in (control/'completions').glob('*.tsv')}
 if actual!=expected:raise CorrectnessError('completion files differ from successful IDs')
