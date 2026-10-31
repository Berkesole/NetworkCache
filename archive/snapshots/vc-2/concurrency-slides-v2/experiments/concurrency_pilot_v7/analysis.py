"""Descriptive pilot analysis: no convergence gate, no cross-seed inference."""
import collections,csv,hashlib,html,json,math
from pathlib import Path
import controller as ctl
def read(path):
 with path.open() as h:return list(csv.DictReader(h))
def num(row,key):return int(row[key]) if row.get(key) not in (None,'') else None
def percentile(a,q):
 if not a:return None
 a=sorted(a);x=(len(a)-1)*q;i=int(x);return a[i]+(a[min(i+1,len(a)-1)]-a[i])*(x-i)
def average(a):return sum(a)/len(a) if a else None
def summary(rows):
 ok=[r for r in rows if r['status']=='ok'];d=dict(planned=len(rows),completed=len(ok),statuses=dict(collections.Counter(r['status'] for r in rows)),
  completion_fraction=len(ok)/len(rows) if rows else None,local_cache_fraction=sum(r.get('via')=='local_cache' for r in ok)/len(ok) if ok else None)
 for key in ('total_latency_ns','execution_ns','node_queue_ns','scheduler_delay_ns'):
  a=[num(r,key)/1e6 for r in ok if num(r,key) is not None];d[key[:-3]+'_ms']=dict(mean=average(a),p50=percentile(a,.5),p95=percentile(a,.95),p99=percentile(a,.99),n=len(a))
 d['quantiles']='successful requests only; all planned/failed requests retained; exploratory p99'
 return d
def flattened(rows):
 d=summary(rows)
 return {k:v for k,v in d.items() if not isinstance(v,dict)}|{k:v['mean'] for k,v in d.items() if isinstance(v,dict) and 'mean' in v}
def slope(points):
 if len(points)<2:return None
 x=average([p[0] for p in points]);y=average([p[1] for p in points]);den=sum((a-x)**2 for a,b in points)
 return sum((a-x)*(b-y) for a,b in points)/den if den else None
def batch_analysis(rows):
 # Cohorts stay in original input order, never completion order.
 batches=[dict(batch=i//128+1,**flattened(rows[i:i+128])) for i in range(0,len(rows),128)]
 windows=[]
 for end in range(4,len(batches)+1):
  subset=rows[(end-4)*128:end*128];row=dict(batch_end=end,**flattened(subset))
  for metric in ('total_latency_ms','execution_ms','node_queue_ms','local_cache_fraction'):
   points=[(x['batch'],x[metric]) for x in batches[end-4:end] if x.get(metric) is not None]
   row[metric+'_slope_per_batch']=slope(points)
   row[metric+'_change_from_previous']=row[metric]-windows[-1][metric] if windows and row.get(metric) is not None and windows[-1].get(metric) is not None else None
  windows.append(row)
 return batches,windows
def timeline_analysis(rows,base,end):
 duration=max(0,(end-base)/1e9);bins=collections.defaultdict(lambda:collections.Counter())
 for r in rows:
  for field,label in [('planned_mono_ns','scheduled'),('admitted_mono_ns','admitted'),('issued_mono_ns','issued'),('node_start_mono_ns','started'),('completed_mono_ns','completed')]:
   t=num(r,field)
   if t is not None and t<=end:bins[max(0,int((t-base)/1e9))][label]+=1
 cumulative=collections.Counter();out=[]
 for second in range(math.ceil(duration)+1):
  counts=bins[second];cumulative.update(counts)
  cohort=[r for r in rows if num(r,'planned_mono_ns') is not None and second<=(num(r,'planned_mono_ns')-base)/1e9<second+1 and num(r,'planned_mono_ns')<=end]
  out.append(dict(second=second,**{k:counts[k] for k in ('scheduled','admitted','issued','started','completed')},
    issued_minus_completed=cumulative['issued']-cumulative['completed'],scheduled_minus_completed=cumulative['scheduled']-cumulative['completed'],
    **{'arrival_cohort_'+k:v for k,v in flattened(cohort).items()}))
 return out
def complete_burst_cycles(rows,manifest,result):
 base=manifest['base_mono_ns'];phase=manifest['phase_seconds'];end=min(result['ended_mono_ns'],result['last_planned_mono_ns']);cycles=[]
 max_cycle=math.floor(((end-base)/1e9+phase)/5)
 for c in range(1,max_cycle):
  a=base+round((5*c-phase)*1e9);b=a+5_000_000_000
  if a<base or b>end:continue
  selected=[r for r in rows if a<=num(r,'planned_mono_ns')<b]
  cycles.append(dict(cycle=c,start_seconds=(a-base)/1e9,end_seconds=(b-base)/1e9,**flattened(selected)))
 return cycles
def plot(path,series,xkey,title,metrics):
 colors=['#0072B2','#D55E00','#009E73','#CC79A7'];parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="540" viewBox="0 0 1000 540">', '<rect width="1000" height="540" fill="white"/>',f'<text x="60" y="30" font-family="sans-serif" font-size="18">{html.escape(title)}</text>']
 xmax=max((r[xkey] for r in series),default=1) or 1;ymax=max((r[k] for r in series for k,_ in metrics if r.get(k) is not None),default=1)*1.1 or 1
 for i in range(6):
  y=450-i*70;v=ymax*i/5;parts.append(f'<path d="M70 {y}H950" stroke="#ddd"/><text x="62" y="{y+4}" text-anchor="end" font-family="sans-serif" font-size="12">{v:.2f}</text>')
 for i,(metric,label) in enumerate(metrics):
  points=' '.join(f"{70+r[xkey]/xmax*880:.2f},{450-r[metric]/ymax*350:.2f}" for r in series if r.get(metric) is not None)
  parts.append(f'<polyline points="{points}" fill="none" stroke="{colors[i]}" stroke-width="2" stroke-dasharray="{["none","7 3","2 3","9 3 2 3"][i]}"/>')
  parts.append(f'<text x="{80+i*220}" y="65" fill="{colors[i]}" font-family="sans-serif" font-size="13">{html.escape(label)}</text>')
 for i in range(6):
  x=70+i*176;parts.append(f'<text x="{x}" y="470" text-anchor="middle" font-family="sans-serif" font-size="12">{xmax*i/5:.1f}</text>')
 parts.append(f'<text x="500" y="502" text-anchor="middle" font-family="sans-serif" font-size="14">{html.escape(xkey)} · single seed · descriptive only</text></svg>');path.write_text('\n'.join(parts))
def one(raw,dest):
 result=json.loads((raw/'result.json').read_text());dest.mkdir(parents=True,exist_ok=True)
 rows=[];phases=[];cohorts=[];times=[];cycles=[];samples=[]
 for p in sorted(raw.glob('measurement-*/requests.csv')):
  rr=read(p);res=json.loads((p.parent/'result.json').read_text());manifest=json.loads((p.parent/'arrival_manifest.json').read_text());rows+=rr;phases.append(res)
  times+=timeline_analysis(rr,res['base_mono_ns'],res['ended_mono_ns'])
  if manifest['model']=='burst':cycles+=complete_burst_cycles(rr,manifest,res)
  for line in (p.parent/'timeline.jsonl').read_text().splitlines():samples.append(json.loads(line))
 if not rows:
  # A setup/infrastructure failure still preserves the unissued measurement denominator.
  rows=[dict(x,status='not_issued') for x in read(raw/'schedule.csv') if x.get('phase')=='measurement']
 batches,windows=batch_analysis(rows);record=summary(rows)
 record.update(config=result['config'],status=result['status'],measurement_status=result['measurement_status'],reason=result.get('reason'),raw=str(raw),
  total_wall_seconds=result['total_wall_seconds'],startup_seconds=result['timing'].get('startup_seconds'),setup_seconds=result['timing'].get('setup_seconds'),
  finalization_seconds=result['finalization_seconds'],finalization_errors=result['finalization_errors'],
  measurement_seconds=sum((p['ended_mono_ns']-p['base_mono_ns'])/1e9 for p in phases),
  nominal_arrival_window_seconds=4096/result['config']['rate'] if result['config']['rate'] else None,
  actual_arrival_window_seconds=sum((p['last_planned_mono_ns']-p['base_mono_ns'])/1e9 for p in phases),
  after_last_arrival_seconds=sum(max(0,p['ended_mono_ns']-p['last_planned_mono_ns'])/1e9 for p in phases),
  final_quiescence_seconds=sum(p.get('final_quiescence_seconds') or 0 for p in phases),
  early=summary(rows[:512]),late=summary(rows[-512:]),stability=dict(mode='DESCRIPTIVE_NO_GATE',automatic_convergence_claim=False,
   interpretations=['cache warmup may remain','cache and queue may stabilize','cache may stabilize while queue grows','insufficient observation'],
   decision='inspect execution/cache AND queue/total latency; no fixed latency target; no automatic horizon extension'),
  tail_exploratory=True,comparison='Only matched complete v4 serial/open runs; never compare speedup across versions',
  campaign=json.loads((raw/'campaign_snapshot.json').read_text())['fingerprint'],schedule_sha256=rows[0].get('schedule_sha256') if rows else None)
 events=collections.defaultdict(list)
 for r in rows:
  if num(r,'node_start_mono_ns') is not None and num(r,'completed_mono_ns') is not None:events[str(r['requester'])]+=[(num(r,'node_start_mono_ns'),1),(num(r,'completed_mono_ns'),-1)]
 def peak(e):
  active=maximum=0
  for _,d in sorted(e):active+=d;maximum=max(maximum,active)
  return maximum
 record['per_node_peak_completed_intervals']={n:peak(e) for n,e in events.items()};record['global_peak_completed_intervals']=peak([e for v in events.values() for e in v])
 record['peak_scope']='completed intervals; outstanding/failed requests also retained in raw timeline'
 cpus=[];previous=None
 for s in samples:
  values=list(map(int,s['cpu'].split()[1:9]));total=sum(values);idle=values[3]+values[4]
  if previous and total>previous[0]:cpus.append(100*(1-(idle-previous[1])/(total-previous[0])))
  previous=(total,idle)
 record['resources']=dict(host_cpu_mean_percent=average(cpus),host_cpu_peak_percent=max(cpus,default=None),
  minimum_available_memory_bytes=min((int(next(l.split()[1] for l in s['meminfo'].splitlines() if l.startswith('MemAvailable:')))*1024 for s in samples),default=None),
  peak_outstanding=max((s['outstanding'] for s in samples),default=0))
 traffic=collections.Counter();mechanisms=collections.Counter();traffic_bins=collections.defaultdict(collections.Counter)
 bounds=[(p['base_mono_ns'],p['ended_mono_ns']) for p in phases]
 for folder in ('traces','tcp-traces'):
  for p in (raw/folder).glob('*.jsonl'):
   for line in p.open():
    e=json.loads(line);t=int(e.get('ts_mono_ns',0));bound=next(((a,b) for a,b in bounds if a<=t<=b),None)
    if bound is None:continue
    event=e.get('event','');mechanisms[event]+=1;bucket=traffic_bins[int((t-bound[0])/1e9)];bucket[event]+=1
    if event=='pilot_wire_send':
     kind=e['packet_type'];size=int(e.get('frame_bytes') or e.get('wire_bytes'))
     traffic[kind+'_frames']+=1;traffic[kind+'_bytes']+=size;bucket[kind+'_frames']+=1;bucket[kind+'_bytes']+=size
 record['protocol_traffic']=dict(traffic);record['mechanism_events']=dict(mechanisms);record['traffic_scope']='application framing, excludes IP/TCP overhead; no provider decomposition'
 health=raw/'containers.after.json'
 record['health']=dict(container_oom_killed=[c['Name'] for c in json.loads(health.read_text()) if c['State'].get('OOMKilled')] if health.exists() else None,
  process_failure=json.loads((raw/'process-failure.json').read_text()) if (raw/'process-failure.json').exists() else None)
 ctl.table(dest/'batch_timeline.csv',batches);ctl.table(dest/'rolling_4batch.csv',windows);ctl.table(dest/'time_timeline.csv',times);ctl.table(dest/'complete_burst_cycles.csv',cycles)
 ctl.table(dest/'mechanism_time_timeline.csv',[dict(second=s,**v) for s,v in sorted(traffic_bins.items())]);ctl.dump(dest/'summary.json',record)
 plot(dest/'latency.svg',batches,'batch','Arrival-order cohorts: mean milliseconds',[('total_latency_ms','User latency'),('execution_ms','Execution'),('node_queue_ms','Ingress wait')])
 plot(dest/'cache.svg',batches,'batch','Local-cache service fraction among completed requests',[('local_cache_fraction','Local cache')])
 plot(dest/'load.svg',times,'second','Per-second arrivals and completions',[('scheduled','Scheduled'),('issued','Issued'),('completed','Completed')])
 plot(dest/'backlog.svg',times,'second','Outstanding counts, including pending dispatch',[('scheduled_minus_completed','Due minus completed'),('issued_minus_completed','Issued minus completed')])
 record['input_sha256']={str(p.relative_to(raw)):hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.rglob('*') if p.is_file()}
 ctl.dump(dest/'analysis_manifest.json',dict(input_sha256=record.pop('input_sha256'),evidence='EXPLORATORY_SINGLE_SEED',outputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file() and p.name!='analysis_manifest.json'}))
 return record
def time_comparison(rows):
 comparisons=[]
 for r in rows:
  if r['config']['model'] not in ('poisson','burst') or r['status']!='ok' or r['completed']!=4096:continue
  candidates=[s for s in rows if s['config']['model']=='serial' and s['config']['method']==r['config']['method'] and s['status']=='ok' and s['completed']==4096]
  for s in candidates:
   if (s['campaign'],s['schedule_sha256'])!=(r['campaign'],r['schedule_sha256']):raise ValueError('unmatched timing comparison')
   comparisons.append(dict(method=r['config']['method'],open_config=r['config']['id'],serial_raw=s['raw'],open_raw=r['raw'],
    serial_measurement_seconds=s['measurement_seconds'],open_measurement_seconds=r['measurement_seconds'],measurement_ratio=s['measurement_seconds']/r['measurement_seconds'],
    serial_wall_seconds=s['total_wall_seconds'],open_wall_seconds=r['total_wall_seconds'],wall_ratio=s['total_wall_seconds']/r['total_wall_seconds']))
 return comparisons
def analyze(raw_root,derived):
 derived.mkdir(parents=True,exist_ok=True);rows=[]
 for p in sorted(raw_root.glob('*/attempt*/result.json')):rows.append(one(p.parent,derived/p.parent.parent.name/p.parent.name))
 ctl.dump(derived/'report.json',dict(evidence='EXPLORATORY_PILOT_V7',runs=rows))
 comparisons=time_comparison(rows);ctl.dump(derived/'time_comparison.json',dict(comparisons=comparisons,note='Same version, workload and method; complete runs only. Arrival pacing is not maximum throughput.'))
 lines=['# 同版本串行 / 开放到达耗时比较','','仅完整同方法 4096 请求比较；比值为串行耗时 / 开放到达耗时。到达速率是输入，不代表最大吞吐量。','',
  '| 方法/开放条件 | 串行测量 s | 开放测量 s | 测量比值 | 串行总 s | 开放总 s | 总比值 |','|---|---:|---:|---:|---:|---:|---:|']
 for r in comparisons:lines.append(f"| {r['open_config']} | {r['serial_measurement_seconds']:.2f} | {r['open_measurement_seconds']:.2f} | {r['measurement_ratio']:.2f} | {r['serial_wall_seconds']:.2f} | {r['open_wall_seconds']:.2f} | {r['wall_ratio']:.2f} |")
 if not comparisons:lines+=['','尚无完整的同版本匹配对；不发布提前估算的加速比。']
 (derived/'time_comparison.md').write_text('\n'.join(lines)+'\n')
 lines=['# 轮询间隔敏感性 Pilot v7','','单 seed 描述性结果；不设收敛通过阈值，不与旧镜像计算加速比。','',
 '| 条件 | 状态 | 成功/计划 | 测量秒 | 总秒 | 平均用户 ms | 平均执行 ms | 同节点峰值 |', '|---|---|---:|---:|---:|---:|---:|---:|']
 def fmt(v):return '—' if v is None else f'{v:.2f}'
 for r in rows:
  lines.append(f"| {r['config']['id']} | {r['status']} | {r['completed']}/{r['planned']} | {fmt(r['measurement_seconds'])} | {fmt(r['total_wall_seconds'])} | {fmt(r['total_latency_ms']['mean'])} | {fmt(r['execution_ms']['mean'])} | {max(r['per_node_peak_completed_intervals'].values(),default=0)} |")
 lines+=['','每项目录提供 latency.svg、cache.svg、load.svg、backlog.svg、逐 batch、4-batch 窗口和完整突发周期 CSV。',
 '分位数仅描述成功请求；失败、未完成、未发出仍保留在分母。缓存行为稳定不代表排队稳定。',
 '未自动判定收敛：同时检查执行/缓存趋势、积压趋势和用户时延；观察不足时如实报告。']
 (derived/'report.md').write_text('\n'.join(lines)+'\n');return dict(completed_attempts=len(rows),report=str(derived/'report.md'))
