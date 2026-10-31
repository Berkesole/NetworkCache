"""Phase barrier and strictly isolated probe evidence; no protocol tuning."""
import json,time
import controller as ctl

def expiry_evidence(events,probes,starts,lower,upper):
 expected={int(p['requester']):p for p in probes};seen={}
 for e in events:
  if e.get('event')!='query_expired_unsatisfied':continue
  node=int(e.get('node',0));t=int(e.get('ts_mono_ns',0))
  if node not in expected or node in seen:raise ctl.CorrectnessError('extra/duplicate expiry requester')
  if not lower<t<=upper or t-starts[node]<30_000_000_000:raise ctl.CorrectnessError('expiry outside isolated lifetime window')
  if any(str(e.get(k,''))!=v for k,v in dict(src=str(node),phase='fallback',reason='query-lifetime-expired',value_bytes='1',fallback='1').items()):raise ctl.CorrectnessError('bad expiry fields/count')
  if any(e.get(k) not in (None,'') for k in ('request_id','key')):raise ctl.CorrectnessError('unexpected expiry identity schema')
  seen[node]=e
 return seen

def read_events(traces):
 events=[]
 for p in traces.glob('node*.jsonl'):
  # A writer may still hold the final newline; only inspect complete records.
  text=p.read_text();lines=text.splitlines()
  if text and not text.endswith('\n'):lines=lines[:-1]
  for line in lines:
   if 'query_expired_unsatisfied' in line:events.append(json.loads(line))
 return events

def wait_files(paths,health,deadline):
 while time.monotonic()<deadline:
  health()
  if all(p.exists() for p in paths):return
  time.sleep(.025)
 raise ctl.LoadTimeout('federation control timeout')

def boundary(control,raw,mapped,method,schedule,health,traces):
 probes=ctl.read_tsv(mapped/'isolation_probes.tsv')
 assert [(p['requester'],p['owner'],p['key']) for p in probes]==[('2','40','federation_v4h3_probe_B'),('34','8','federation_v4h3_probe_A')]
 lower=time.monotonic_ns();deadline=time.monotonic()+40;starts={};events=[]
 for i,p in enumerate(probes,1):
  p['request_index']=str(900000000+i);p['schedule_sha256']=schedule
  ident=p['request_index'];node=int(p['requester'])
  ctl.atomic(control/'probe-allowlist'/f'node{node}.tsv',f"{ident}\t{p['key']}\t{schedule}\n")
  ctl.atomic(control/'commands'/f'node{node}'/f'{ident}.tsv','\t'.join([ident,method,p['key'],p['owner'],p['value_size_bytes'],p['expected_fnv1a64'],schedule])+'\n')
 ctl.dump(raw/'probe-plan.json',dict(probes=probes,lower_mono_ns=lower,formal_active=0))
 wait_files([control/'started'/f"{p['request_index']}.tsv" for p in probes],health,deadline)
 for p in probes:
  f=(control/'started'/f"{p['request_index']}.tsv").read_text().strip().split('\t')
  assert f[:2]==[p['request_index'],p['requester']];starts[int(p['requester'])]=int(f[2])
 if method=='nc':
  while time.monotonic()<deadline:
   health()
   if list((control/'fatal').glob('*')):raise ctl.CorrectnessError('probe runtime fatal')
   if any((control/'completions'/f"{p['request_index']}.tsv").exists() for p in probes):raise ctl.CorrectnessError('successful NC isolation DATA')
   events=read_events(traces);seen=expiry_evidence(events,probes,starts,lower,time.monotonic_ns())
   if len(seen)==2:break
   time.sleep(.15)
  else:raise ctl.LoadTimeout('probe natural expiry missing by 40s')
  for p in probes:
   node=int(p['requester']);ident=p['request_index'];e=seen[node]
   ctl.atomic(control/'probe-retire'/f'{ident}-node{node}.tsv',f"{ident}\t{p['key']}\t{e['ts_mono_ns']}\t{schedule}\n")
  wait_files([control/'probe-retired'/f"{p['request_index']}-node{p['requester']}.tsv" for p in probes],health,deadline)
 else:
  wait_files([control/'completions'/f"{p['request_index']}.tsv" for p in probes],health,deadline)
  for p in probes:
   f=(control/'completions'/f"{p['request_index']}.tsv").read_text().strip().split('\t')
   if len(f)<12 or f[:3]!=[p['request_index'],p['requester'],p['key']] or f[3]!='io_error' or f[4]!='0' or f[10]!=schedule or f[11]!='0':raise ctl.CorrectnessError('TCP probe is not an empty failed completion')
   p['failed_completion']=f
 ctl.quiesce(control,'899999990',health,nodes=64)
 if list((control/'fatal').glob('*')):raise ctl.CorrectnessError('probe fatal before activation')
 before=time.monotonic_ns();ctl.dump(raw/'isolation.json',dict(status='ok',probes=probes,starts=starts,events=events,lower_mono_ns=lower,upper_mono_ns=before))
 for node,peer in ((1,33),(33,1)):
  link='172.31.16.2' if node==1 else '172.31.16.1'
  ctl.atomic(control/'activation'/f'node{node}.tsv',f'{peer}\t10.90.0.{peer}\t{44000+peer}\truntime-local-bootstrap\t{link}\t{schedule}\n')
 wait_files([control/'activated'/f'node{n}.tsv' for n in (1,33)],health,time.monotonic()+30)
 acks=[]
 for node,peer in ((1,33),(33,1)):
  f=(control/'activated'/f'node{node}.tsv').read_text().split('\t')
  if f[:3]!=[str(node),str(peer),'ok']:raise ctl.CorrectnessError('activation rejected')
  acks.append(int(f[3]))
 time.sleep(.5);health();ready=time.monotonic_ns()
 ctl.atomic(control/'federation-post.enabled',str(ready)+'\n')
 ctl.dump(raw/'activation.json',dict(issued_mono_ns=before,ack_mono_ns=acks,post_ready_mono_ns=ready))
 return {p['request_index'] for p in probes}
