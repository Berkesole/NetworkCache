"""Open-arrival, one-inflight-per-node pilot. No protocol changes or global current request."""
import collections,csv,json,math,os,random,time
from pathlib import Path

ARRIVAL_SEED=9173
class CorrectnessError(RuntimeError):pass
class LoadTimeout(RuntimeError):pass
class InfrastructureError(RuntimeError):pass

def dump(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name('.'+path.name+'.tmp');tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');tmp.replace(path)
def atomic(path,text):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name('.'+path.name+'.tmp');tmp.write_text(text);tmp.replace(path)
def table(path,rows):
 if not rows:return
 with Path(path).open('w',newline='') as f:
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
    pos=(elapsed+phase)%5
    high=pos>=4; span=(5 if high else 4)-pos
    intensity=rate*(4 if high else .25)
    if hazard<=span*intensity:elapsed+=hazard/intensity;break
    elapsed+=span+1e-12;hazard-=span*intensity
  else:raise ValueError(model)
  out.append(round(elapsed*1e9))
 return out,phase

class Engine:
 def __init__(self,requests,mode,base,arrival=None,cap=32):
  self.mode=mode;self.base=base;self.cap=cap;self.active={};self.done={};self.next=0
  self.rows=[dict(r,status='not_arrived',planned_mono_ns=(base+arrival[i] if arrival is not None else ''),
                  admitted_mono_ns='',issued_mono_ns='',node_start_mono_ns='',completed_mono_ns='') for i,r in enumerate(requests)]
  ids=[r['request_index'] for r in self.rows]
  if len(ids)!=len(set(ids)):raise CorrectnessError('duplicate application ID')
  self.byid={r['request_index']:r for r in self.rows}
  self.queues=collections.defaultdict(collections.deque)
  self.watchdog=(base+arrival[-1]+60_000_000_000) if arrival else None
 def admit(self,now):
  if self.mode=='open':
   while self.next<len(self.rows) and int(self.rows[self.next]['planned_mono_ns'])<=now:
    r=self.rows[self.next];r.update(status='queued',admitted_mono_ns=now)
    self.queues[int(r['requester'])].append(r);self.next+=1
  else:
   pending=len(self.active)+sum(len(v) for v in self.queues.values())
   while self.next<len(self.rows) and pending<self.cap:
    r=self.rows[self.next];r.update(status='queued',planned_mono_ns=now,admitted_mono_ns=now)
    self.queues[int(r['requester'])].append(r);self.next+=1;pending+=1
 def dispatch(self,now):
  out=[]
  for node,q in sorted(self.queues.items(),key=lambda pair:int(pair[1][0]['request_index']) if pair[1] else math.inf):
   if node not in self.active and q and len(self.active)<self.cap:
    r=q.popleft();r.update(status='issued',issued_mono_ns=now);self.active[node]=r;out.append(r)
  return out
 def started(self,index,node,stamp):
  r=self.byid.get(index)
  if not r or int(r['requester'])!=node or self.active.get(node) is not r:raise CorrectnessError('unexpected start')
  if stamp<int(r['issued_mono_ns']):raise CorrectnessError('start before issue')
  r['node_start_mono_ns']=stamp
 def complete(self,fields,now):
  if len(fields)<11:raise CorrectnessError('malformed completion')
  index,node,key,status,size,digest,start,end,packet,via,schedule=fields[:11]
  if index in self.done:raise CorrectnessError('duplicate completion '+index)
  r=self.byid.get(index)
  if not r or self.active.get(int(node)) is not r:raise CorrectnessError('unknown completion')
  if (node,key,size,digest,schedule)!=(str(r['requester']),r['key'],str(r['value_size_bytes']),r['expected_fnv1a64'],r['schedule_sha256']):
   raise CorrectnessError('identity or value mismatch '+index)
  if status!='ok':
   if status=='io_error':raise LoadTimeout('TCP io_error '+index)
   raise CorrectnessError('completion status '+status)
  start,end=int(start),int(end)
  if not int(r['issued_mono_ns'])<=start<=end<=now:raise CorrectnessError('invalid completion clock')
  if r['node_start_mono_ns'] and r['node_start_mono_ns']!=start:raise CorrectnessError('start marker mismatch')
  r.update(status='ok',node_start_mono_ns=start,completed_mono_ns=end,via=via,packet_request_id=packet,
           scheduler_delay_ns=int(r['admitted_mono_ns'])-int(r['planned_mono_ns']),
           node_queue_ns=start-int(r['admitted_mono_ns']),execution_ns=end-start,
           total_latency_ns=end-int(r['planned_mono_ns']))
  self.done[index]=r;del self.active[int(node)]
 def check_timeout(self,now):
  for r in self.active.values():
   start=r['node_start_mono_ns'] or r['issued_mono_ns']
   if now-int(start)>8_000_000_000:
    r['status']='timeout' if r['node_start_mono_ns'] else 'dispatch_timeout'
    raise LoadTimeout(str(r['status'])+' '+r['request_index'])
  if self.watchdog and now>self.watchdog and len(self.done)<len(self.rows):raise LoadTimeout('global watchdog')
 def finish_incomplete(self,reason):
  for r in self.rows:
   if r['status']=='issued':r['status']='incomplete'
   elif r['status'] in ('not_arrived','queued'):r['status']='not_issued'
   if r['status']!='ok':r['termination_reason']=reason

def quiesce(control,ident,timeout=20):
 marker=control/'quiesce'/f'{ident}.marker';atomic(marker,f'{ident}\n')
 paths=[control/'quiesced'/f'{ident}-node{n}.ack' for n in range(1,33)]
 deadline=time.monotonic()+timeout
 try:
  while time.monotonic()<deadline:
   try:
    before=[(p.stat().st_mtime_ns,p.read_bytes()) for p in paths]
    time.sleep(.5)
    if before==[(p.stat().st_mtime_ns,p.read_bytes()) for p in paths]:return
   except FileNotFoundError:pass
   time.sleep(.01)
  raise LoadTimeout('phase quiescence timeout')
 finally:
  marker.unlink(missing_ok=True)
  for p in paths:p.unlink(missing_ok=True)

def execute(requests,control,output,method,model,rate=0,cap=32,serial_quiet=False,group='measurement'):
 arrival,phase=offsets(len(requests),model,rate) if model in ('poisson','burst') else (None,0)
 if model=='wave':arrival=[0]*len(requests)
 base=time.monotonic_ns()+100_000_000
 engine=Engine(requests,'open' if arrival is not None else 'closed',base,arrival,cap)
 output.mkdir(parents=True,exist_ok=True)
 dump(output/'arrival_manifest.json',dict(model=model,rate=rate,seed=ARRIVAL_SEED,phase_seconds=phase,
      planned_offsets_ns=arrival,request_ids=[r['request_index'] for r in requests],clock='monotonic',base_mono_ns=base))
 status='ok';reason='';last_sample=0;start_seen=set()
 with (output/'timeline.jsonl').open('w') as timeline:
  try:
   while len(engine.done)<len(engine.rows):
    now=time.monotonic_ns()
    if now<base:time.sleep(.001);continue
    engine.admit(now)
    for node,r in list(engine.active.items()):
     index=r['request_index'];p=control/'started'/f'{index}.tsv'
     if p.exists() and index not in start_seen:
      fields=p.read_text().strip().split('\t');engine.started(index,int(fields[1]),int(fields[2]));start_seen.add(index)
     p=control/'completions'/f'{index}.tsv'
     if p.exists():
      engine.complete(p.read_text().strip().split('\t'),time.monotonic_ns())
      if serial_quiet:
       quiesce(control,index);time.sleep(.1)
    engine.check_timeout(time.monotonic_ns())
    for r in engine.dispatch(time.monotonic_ns()):
     text='\t'.join([r['request_index'],method,r['key'],str(r['owner']),str(r['value_size_bytes']),r['expected_fnv1a64'],r['schedule_sha256']])+'\n'
     atomic(control/'commands'/f"node{r['requester']}"/f"{int(r['request_index']):06d}.tsv",text)
    if now-last_sample>=100_000_000:
     timeline.write(json.dumps(dict(mono_ns=now,admitted=engine.next,completed=len(engine.done),
       inflight=len(engine.active),queued=sum(map(len,engine.queues.values())),loadavg=os.getloadavg(),
       meminfo=Path('/proc/meminfo').read_text(),cpu=Path('/proc/stat').read_text().splitlines()[0]))+'\n');timeline.flush()
     dump(control/'pilot-progress.json',dict(group=group,model=model,planned=len(requests),admitted=engine.next,completed=len(engine.done),inflight=len(engine.active)))
     last_sample=now
    time.sleep(.002)
   quiesce(control,'800000'+str(requests[-1]['request_index']))
  except CorrectnessError as e:status='correctness_failure';reason=str(e)
  except LoadTimeout as e:status='load_timeout';reason=str(e)
  except Exception as e:status='controller_failure';reason=repr(e)
  finally:
   engine.finish_incomplete(reason);table(output/'requests.csv',engine.rows)
   record=dict(status=status,reason=reason,planned=len(requests),admitted=engine.next,completed=len(engine.done),
       base_mono_ns=base,ended_mono_ns=time.monotonic_ns(),model=model,rate=rate,group=group)
   dump(output/'result.json',record)
 return record,engine.rows

def validate_audit(control,rows):
 counts=collections.Counter()
 for p in (control/'delivery-audit').glob('*.tsv'):
  for line in p.read_text().splitlines():
   f=line.split('\t');counts[f[0]]+=1
 expected={r['request_index'] for r in rows if r['status']=='ok'}
 if set(counts)!=expected or any(v!=1 for v in counts.values()):raise CorrectnessError('delivery audit differs from completed request IDs')
