"""Read-only validation; derived diagnostics; all-or-nothing formal inference."""
import collections,hashlib,json,math,statistics
from pathlib import Path
import analysis as a,controller as ctl,matrix

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def measured(raw):
 return [r for p in sorted(raw.glob('measurement-*/requests.csv')) for r in a.read(p)]
def destination(raw,root):return root/'derived'/raw.relative_to(root/'raw')
def validate(raw,root):
 try:
  r=json.loads((raw/'result.json').read_text());c=r['config'];campaign=json.loads((root/'campaign.json').read_text())
  assert c in matrix.slots(c['stage']),'unknown slot'
  assert json.loads((raw/'campaign_snapshot.json').read_text())==campaign,'campaign mismatch'
  assert r['status']=='ok' and not r['finalization_errors'],r.get('reason') or r['status']
  assert (raw/'runtime_image_id.before_launch').read_text().strip()==campaign['image_id'],'image mismatch'
  assert all(x['Image']==campaign['image_id'] for x in json.loads((raw/'containers.before.json').read_text())),'container image mismatch'
  assert not any(x['State'].get('OOMKilled') for x in json.loads((raw/'containers.after.json').read_text())),'OOM'
  env=json.loads((raw/'environment.json').read_text())
  for k,v in dict(RSYNC_QUERY_TIMEOUT_MS='30000',RSYNC_FALLBACK_DELAY_MS='1500',KATHARA_NODE_LOOP_SLEEP_MS='25',RSYNC_BLOOM_BITS='1024',RSYNC_BLOOM_HASH_COUNT='3',RSYNC_WIRE_VERSION='3',KATHARA_LINK_DELAY_MS='2',KATHARA_LINK_RATE_MBIT='1000',KATHARA_LINK_MTU='1500').items():assert env[k]==v,k
  expected=[x for x in a.read(raw/'schedule.csv') if x['phase']!='setup'];rows=measured(raw)
  assert len(rows)==len(expected) and len({x['request_index'] for x in rows})==len(rows),'measurement count'
  keys=('request_index','phase','bin','round','key','requester','owner','value_size_bytes','expected_fnv1a64','schedule_sha256')
  assert [[x[k] for k in keys] for x in rows]==[[x[k] for k in keys] for x in expected],'measurement schedule mismatch'
  for x in rows:
   assert x['status']=='ok','incomplete request'
   times=[int(x[k]) for k in ('planned_mono_ns','admitted_mono_ns','issued_mono_ns','node_start_mono_ns','completed_mono_ns','observed_completion_mono_ns')]
   assert times==sorted(times),'request clock order'
   assert int(x['total_latency_ns'])==times[4]-times[0] and int(x['execution_ns'])==times[4]-times[3],'latency arithmetic'
  phases=[]
  for p in sorted(raw.glob('measurement-*/result.json')):
   phase=json.loads(p.read_text());m=json.loads((p.parent/'arrival_manifest.json').read_text());rr=a.read(p.parent/'requests.csv');phases.append(phase)
   if c['stage']!='smoke' or c['family']=='federation':
    offsets,shift=ctl.offsets(len(rr),c['model'],c['rate'],c['arrival_seed'])
    assert m['planned_offsets_ns']==offsets and m['phase_seconds']==shift and m['seed']==c['arrival_seed'],'arrival mismatch'
   else:offsets=[i*1_000_000 for i in range(len(rr))]
   assert [int(x['planned_mono_ns'])-m['base_mono_ns'] for x in rr]==offsets,'arrival clock changed'
   assert m['concurrency_cap'] is None and phase['status']=='ok' and phase['completed']==len(rr),'closed/failed phase'
  events=[];counts=collections.Counter();tcp_probe_data=0
  for folder in ('traces','tcp-traces'):
   for p in (raw/folder).glob('*.jsonl'):
    for line in p.open():
     e=json.loads(line);counts[e.get('event')]+=1
     if str(e.get('app_request_id','')).startswith('900000') and e.get('packet_type')=='DATA':tcp_probe_data+=1
     if e.get('event')=='query_expired_unsatisfied':events.append(e)
  assert counts['pilot_transport_error']==0,'transport error'
  if c['method']=='nc':
   assert counts['pilot_tcp_connect']>0 and counts['pilot_tcp_reuse']>0,'reuse missing'
   files=list((raw/'traces').glob('*.transport.csv'));assert len(files)==c['hosts'],'transport observation missing'
   frames=[x for p in files for x in a.read(p)]
   assert len(frames)==counts['pilot_wire_send'],'transport frame accounting mismatch'
   assert len({(x['node'],x['frame_id']) for x in frames})==len(frames),'duplicate transport frame ID'
   for x in frames:
    assert x['lane'] in ('data','control') and int(x['error_errno'])==0
    assert int(x['wire_complete_mono_ns'])-int(x['enqueued_mono_ns'])==int(x['application_send_queue_ns'])>=0
  probes=set()
  if c['family']=='federation':
   import federation
   iso=json.loads((raw/'isolation.json').read_text());act=json.loads((raw/'activation.json').read_text())
   assert iso['status']=='ok' and len(iso['probes'])==2
   probes={p['request_index'] for p in iso['probes']};assert probes=={'900000001','900000002'}
   assert int(phases[0]['last_completion_mono_ns'])<iso['lower_mono_ns']<iso['upper_mono_ns']<=act['issued_mono_ns']
   assert len(act['ack_mono_ns'])==2 and min(act['ack_mono_ns'])>=act['issued_mono_ns']
   assert act['post_ready_mono_ns']-max(act['ack_mono_ns'])>=500_000_000 and phases[1]['base_mono_ns']>=act['post_ready_mono_ns']
   if c['method']=='nc':
    assert len(federation.expiry_evidence(events,iso['probes'],{int(k):v for k,v in iso['starts'].items()},iso['lower_mono_ns'],iso['upper_mono_ns']))==2
    assert len(list((raw/'control/probe-retired').glob('*.tsv')))==2
    assert not any((raw/'control/completions'/f'{i}.tsv').exists() for i in probes),'late probe DATA'
   else:
    assert not events and tcp_probe_data==0
    for p in iso['probes']:
     f=(raw/'control/completions'/f"{p['request_index']}.tsv").read_text().strip().split('\t')
     assert f==p['failed_completion'] and f[3]=='io_error' and f[4]=='0' and f[11]=='0'
  else:assert not events,'formal unsatisfied expiry'
  allrows=rows+(a.read(raw/'setup/requests.csv') if (raw/'setup/requests.csv').exists() else [])
  ctl.validate_audit(raw/'control',allrows,probes)
  assert not list((raw/'control/fatal').glob('*')),'runtime fatal'
  import pilot
  assert pilot.peak(rows).get('all',0)>=2,'no actual overlap'
  return dict(qualified=True,reason='ok',completed=len(rows),formal_expiry=0,isolation_expiry=len(events),campaign=campaign['fingerprint'])
 except Exception as e:return dict(qualified=False,reason=f'{type(e).__name__}: {e}')

def grouped(rows):
 groups=collections.OrderedDict()
 for r in rows:
  phase=r.get('phase','measurement')
  if phase in ('pre','post'):phase+=':'+r['direction']+':'+r['request_class']
  groups.setdefault((phase,int(r.get('bin',1))),[]).append(r)
 return [dict(phase=phase,batch=group,**a.flattened(rs)) for (phase,group),rs in groups.items()]

def diagnose(raw,root):
 dest=destination(raw,root);dest.mkdir(parents=True,exist_ok=True)
 result=json.loads((raw/'result.json').read_text());c=result['config'];actual={r['request_index']:r for r in measured(raw)}
 rows=[actual.get(r['request_index'],dict(r,status='not_issued')) for r in a.read(raw/'schedule.csv') if r['phase']!='setup']
 groups=grouped(rows);phases=[json.loads(p.read_text()) for p in sorted(raw.glob('measurement-*/result.json'))]
 duration=sum((p['ended_mono_ns']-p['base_mono_ns'])/1e9 for p in phases)
 summary=dict(a.summary(rows),config=c,status=result['status'],timing=result['timing'],measurement_seconds=duration,total_wall_seconds=result['total_wall_seconds'],
  finalization_seconds=result['finalization_seconds'],finalization_errors=result['finalization_errors'],
  drain_after_last_arrival_seconds=sum(max(0,p['ended_mono_ns']-p['last_planned_mono_ns'])/1e9 for p in phases),
  gate=validate(raw,root),evidence='FORMAL_PENDING_ALL_CONDITIONS' if c['stage']=='formal' else 'DIAGNOSTIC_NO_CI',
  mechanism_policy='local_cache is verified application-local service only; peer-cache/provider-distance proxies unavailable; no same-key attribution')
 ctl.dump(dest/'summary.json',summary);ctl.dump(dest/'validation.json',summary['gate']);ctl.table(dest/'batch_timeline.csv',groups)
 times=[];cycles=[];samples=[]
 for p in sorted(raw.glob('measurement-*/result.json')):
  res=json.loads(p.read_text());rr=a.read(p.parent/'requests.csv');m=json.loads((p.parent/'arrival_manifest.json').read_text())
  for x in a.timeline_analysis(rr,res['base_mono_ns'],res['ended_mono_ns']):times.append(dict(x,phase=rr[0]['phase']))
  if m['model']=='burst':cycles+=a.complete_burst_cycles(rr,m,res)
  samples.extend(json.loads(line) for line in (p.parent/'timeline.jsonl').read_text().splitlines())
 ctl.table(dest/'time_timeline.csv',times);ctl.table(dest/'burst_cycles.csv',cycles)
 windows=[]
 for phase in dict.fromkeys(r['phase'] for r in groups):
  gs=[r for r in groups if r['phase']==phase]
  for i in range(3,len(gs)):
   subset=gs[i-3:i+1];w=dict(phase=phase,batch_end=gs[i]['batch'])
   for key in ('total_latency_ms','execution_ms','node_queue_ms','local_cache_fraction'):
    pts=[(r['batch'],r[key]) for r in subset if r.get(key) is not None];w[key+'_mean']=a.average([y for _,y in pts]);w[key+'_slope']=a.slope(pts)
   windows.append(w)
  a.plot(dest/f'{phase}-latency.svg',gs,'batch','Mean ms; original arrival-order groups, no convergence gate',[('total_latency_ms','User'),('execution_ms','Execution'),('node_queue_ms','Ingress')])
 for phase in dict.fromkeys(r['phase'] for r in times):
  ts=[r for r in times if r['phase']==phase]
  a.plot(dest/f'{phase}-backlog.svg',ts,'second','Backlog includes all due requests',[('scheduled_minus_completed','Due minus completed'),('issued_minus_completed','Issued minus completed')])
 ctl.table(dest/'rolling_4batch.csv',windows)
 counts=collections.Counter();traffic=collections.Counter()
 for folder in ('traces','tcp-traces'):
  for p in (raw/folder).glob('*.jsonl'):
   for line in p.open():
    e=json.loads(line);t=int(e.get('ts_mono_ns',0))
    if not any(q['base_mono_ns']<=t<=q['ended_mono_ns'] for q in phases):continue
    counts[e.get('event')]+=1
    if e.get('event')=='pilot_wire_send':traffic[e['packet_type']+'_frames']+=1;traffic[e['packet_type']+'_bytes']+=int(e.get('frame_bytes') or e.get('wire_bytes'))
 cpus=[];previous=None
 for s in samples:
  values=list(map(int,s['cpu'].split()[1:9]));total=sum(values);idle=values[3]+values[4]
  if previous and total>previous[0]:cpus.append(100*(1-(idle-previous[1])/(total-previous[0])))
  previous=(total,idle)
 resources=dict(host_cpu_mean_percent=a.average(cpus),host_cpu_peak_percent=max(cpus,default=None),minimum_available_memory_bytes=min((int(next(l.split()[1] for l in s['meminfo'].splitlines() if l.startswith('MemAvailable:')))*1024 for s in samples),default=None))
 ctl.dump(dest/'health_and_mechanisms.json',dict(process_failure=(raw/'process-failure.json').exists(),oom=[x['Name'] for x in json.loads((raw/'containers.after.json').read_text()) if x['State'].get('OOMKilled')] if (raw/'containers.after.json').exists() else None,resources=resources,events=dict(counts),traffic=dict(traffic),peak_outstanding=max((s['outstanding'] for s in samples),default=0),raw_resource_samples='measurement-*/timeline.jsonl; host CPU/memory and per-node in-flight'))
 transport=[]
 for p in (raw/'traces').glob('*.transport.csv'):
  transport.extend(x for x in a.read(p) if any(q['base_mono_ns']<=int(x['wire_complete_mono_ns'])<=q['ended_mono_ns'] for q in phases))
 lanes=[]
 for lane in ('control','data'):
  frames=[x for x in transport if x['lane']==lane];values=[int(x['application_send_queue_ns'])/1e6 for x in frames]
  lanes.append(dict(lane=lane,frames=len(frames),mean_queue_ms=a.average(values),p50_queue_ms=a.percentile(values,.5),p95_queue_ms=a.percentile(values,.95),p99_queue_ms=a.percentile(values,.99),max_queue_ms=max(values,default=None),would_block_calls=sum(int(x['would_block_calls']) for x in frames),max_admitted_queue_depth=max((int(x['admitted_queue_depth']) for x in frames),default=0)))
 ctl.dump(dest/'transport_summary.json',dict(lanes=lanes,scope='per-frame user-space send queue; not end-to-end packet delay; kernel TCP snapshots in targeted diagnostics only'))
 ctl.dump(dest/'analysis_manifest.json',dict(inputs={str(p.relative_to(raw)):sha(p) for p in raw.rglob('*') if p.is_file()},outputs={p.name:sha(p) for p in dest.iterdir() if p.is_file() and p.name!='analysis_manifest.json'}))
 return summary

def ci(values):
 assert len(values)==10
 mean=statistics.mean(values);half=2.2621571627409915*statistics.stdev(values)/math.sqrt(10)
 return dict(seed_count=10,mean=mean,ci95_low=mean-half,ci95_high=mean+half)
def aggregate(seed_reps):
 assert set(seed_reps)==set(range(1,11))
 means=[]
 for seed in range(1,11):
  assert set(seed_reps[seed])=={1,2};means.append(statistics.mean(seed_reps[seed].values()))
 return means,ci(means)

def formal(root,selection):
 assert len(selection)==1280 and {x['slot'] for x in selection}=={c['id'] for c in matrix.slots('formal')}
 table=collections.defaultdict(lambda:collections.defaultdict(dict));periods=collections.defaultdict(lambda:collections.defaultdict(dict))
 configs={c['condition']:c for c in matrix.conditions()}
 metrics=('total_latency_ms','execution_ms','node_queue_ms','scheduler_delay_ms','local_cache_fraction')
 for item in selection:
  c=item['config'];groups=grouped(measured(Path(item['raw'])))
  byperiod=collections.defaultdict(list)
  for g in groups:
   phase=g['phase'];b=g['batch']
   if c['family']=='federation':period=('early' if b<=2 else 'late' if b>=(7 if phase.startswith('pre:') else 15) else 'middle')
   elif c['distribution']=='controlled':period='early' if b<=2 else 'late' if b>=7 else 'middle'
   else:period='early' if b<=4 else 'late' if b>=29 else 'middle'
   for key in metrics:
    if g.get(key) is None:continue
    table[(c['condition'],phase,b,key)][c['seed']][c['repetition']]=g[key]
    byperiod[(phase,'all',key)].append(g[key]);byperiod[(phase,period,key)].append(g[key])
  for (phase,period,key),values in byperiod.items():periods[(c['condition'],phase,period,key)][c['seed']][c['repetition']]=statistics.mean(values)
 out=root/'derived/formal_complete';out.mkdir(exist_ok=True)
 rows=[];seed_values={}
 for (cond,phase,period,key),reps in periods.items():
  means,stat=aggregate(reps);seed_values[(cond,phase,period,key)]=means;rows.append(dict(condition=cond,phase=phase,period=period,metric=key,**stat))
 ctl.table(out/'period_ci95.csv',rows)
 timeline=[]
 for (cond,phase,b,key),reps in table.items():timeline.append(dict(condition=cond,phase=phase,batch=b,metric=key,**aggregate(reps)[1]))
 ctl.table(out/'batch_ci95.csv',timeline)
 seedrows=[]
 for (cond,phase,period,key),values in seed_values.items():
  seedrows.extend(dict(condition=cond,phase=phase,period=period,metric=key,seed=i+1,value=v) for i,v in enumerate(values))
 ctl.table(out/'seed_period_means.csv',seedrows)
 effects=[]
 for (cond,phase,period,key),values in seed_values.items():
  c=configs[cond];base=None;comparison=None
  if c['method']=='nc':base=next(x['condition'] for x in configs.values() if all(x[k]==c[k] for k in ('family','hosts','size','catalog','distribution','model','rate')) and x['method']=='tcp-direct');comparison='NC_minus_TCP'
  candidates=[(base,comparison)] if base else []
  if c['hosts']==32 and c['family'] in ('controlled','zipf','uniform'):
   other=next(x['condition'] for x in configs.values() if x['hosts']==16 and all(x[k]==c[k] for k in ('family','size','distribution','model','rate','method')));candidates.append((other,'32_minus_16'))
  if c['family']=='uniform':
   other=next(x['condition'] for x in configs.values() if x['family']=='zipf' and all(x[k]==c[k] for k in ('hosts','size','model','rate','method')));candidates.append((other,'Uniform_minus_Zipf'))
  if c['family']=='size':
   other=next(x['condition'] for x in configs.values() if x['family']=='controlled' and x['hosts']==32 and all(x[k]==c[k] for k in ('model','rate','method')));candidates.append((other,'size_minus_64KiB'))
  if c['rate']==128:
   other=next(x['condition'] for x in configs.values() if x['rate']==32 and all(x[k]==c[k] for k in ('family','hosts','size','catalog','distribution','model','method')));candidates.append((other,'rate128_minus_32'))
  if c['family']=='federation':
   if c['catalog']==512:
    other=next(x['condition'] for x in configs.values() if x['family']=='federation' and x['catalog']==1000 and all(x[k]==c[k] for k in ('distribution','model','rate','method')));candidates.append((other,'catalog512_minus_1000'))
   if c['distribution']=='uniform':
    other=next(x['condition'] for x in configs.values() if x['family']=='federation' and x['distribution']=='zipf' and all(x[k]==c[k] for k in ('catalog','model','rate','method')));candidates.append((other,'Uniform_minus_Zipf'))
  for other,label in candidates:
   paired=seed_values[(other,phase,period,key)];effects.append(dict(left=cond,right=other,comparison=label,phase=phase,period=period,metric=key,**ci([x-y for x,y in zip(values,paired)])))
   if all(v>0 for v in paired):effects.append(dict(left=cond,right=other,comparison=label.replace('_minus_','_over_'),phase=phase,period=period,metric=key,**ci([x/y for x,y in zip(values,paired)])))
 ctl.table(out/'paired_effects.csv',effects)
 ctl.dump(out/'results.json',dict(status='READY',single_cluster_runs=960,federation_runs=320,periods=rows,timeline=timeline,paired_effects=effects,scope='No mixing old serial data; no automatic convergence claim'))

def build(root):
 selections={};states={}
 for stage in ('smoke','pilot','formal'):
  selected=[];states[stage]=[]
  for c in matrix.slots(stage):
   files=sorted((root/'raw'/stage/c['id']).glob('attempt*/result.json'))
   gate=validate(files[-1].parent,root) if files else dict(qualified=False,reason='not_started')
   states[stage].append(dict(slot=c['id'],qualified=gate['qualified'],reason=gate['reason']))
   if gate['qualified']:selected.append(dict(slot=c['id'],config=c,raw=str(files[-1].parent),result_sha256=sha(files[-1]),gate=gate))
  selections[stage]=selected
 ctl.dump(root/'derived/selection.json',selections);ctl.dump(root/'derived/run_status.json',states)
 ctl.table(root/'derived/selection.csv',[dict(slot=x['slot'],raw=x['raw'],result_sha256=x['result_sha256'],**x['config']) for items in selections.values() for x in items])
 ctl.table(root/'derived/run_status.csv',[dict(stage=stage,**x) for stage,items in states.items() for x in items])
 ready=len(selections['formal'])==1280
 if ready:formal(root,selections['formal'])
 ctl.dump(root/'derived/collection_manifest.json',dict(status='READY' if ready else 'COLLECTING_NO_PARTIAL_STATS',counts={k:len(v) for k,v in selections.items()},campaign=json.loads((root/'campaign.json').read_text())['fingerprint'],selection_sha256=sha(root/'derived/selection.json'),status_sha256=sha(root/'derived/run_status.json')))
 lines=['# 开放到达实验重跑','','Status: '+('READY' if ready else 'COLLECTING_NO_PARTIAL_STATS'),'','| Stage | Qualified | Target |','|---|---:|---:|']
 for stage,n in (('smoke',6),('pilot',64),('formal',1280)):lines.append(f'| {stage} | {len(selections[stage])} | {n} |')
 lines+=['','每个 run 的诊断包含到达顺序曲线、实际时间积压、资源和分阶段耗时；小试不进入正式统计。','不预设收敛时延，不用成功样本替代失败分母，不沿用同 key provider-hit 路径猜测。']
 (root/'derived/report.md').write_text('\n'.join(lines)+'\n')
 return dict(ready=ready,counts={k:len(v) for k,v in selections.items()})
