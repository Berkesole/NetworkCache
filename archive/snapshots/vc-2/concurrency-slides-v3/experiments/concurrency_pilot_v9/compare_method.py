"""Same-method elapsed-time comparison. No latency superiority or capacity claim."""
import hashlib,json
import analysis,controller as ctl

IDENTITY=('request_index','requester','owner','key','value_size_bytes','expected_fnv1a64','schedule_sha256')
def identities(rows):return [[r[k] for k in IDENTITY] for r in rows]
def matched(a,b):
 if identities(a)!=identities(b):raise ValueError('request identity/order mismatch')
def pairs(runs):
 result=[]
 for method in ('nc','tcp-direct'):
  ready={r['config']['model']:r for r in runs if r['config']['method']==method and r['status']=='ok' and r['completed']==4096}
  for old,new in (('serial-quiescent','serial'),('serial','poisson'),('serial','burst'),('serial-quiescent','poisson'),('serial-quiescent','burst')):
   if old not in ready or new not in ready:continue
   a,b=ready[old],ready[new]
   if (a['campaign'],a['schedule_sha256'])!=(b['campaign'],b['schedule_sha256']):raise ValueError('unmatched provenance')
   result.append(dict(method=method,baseline=old,treatment=new,
    measurement_seconds_saved=a['measurement_seconds']-b['measurement_seconds'],measurement_speedup=a['measurement_seconds']/b['measurement_seconds'],
    wall_seconds_saved=a['total_wall_seconds']-b['total_wall_seconds'],wall_speedup=a['total_wall_seconds']/b['total_wall_seconds']))
 return result

def build(root):
 campaign=json.loads((root/'campaign.json').read_text());configs={c['id']:c for c in campaign['matrix']}
 runs=[];reference=None;open_arrivals={}
 for p in sorted((root/'raw').glob('*/attempt*/result.json')):
  raw=p.parent;result=json.loads(p.read_text());config=result['config']
  assert configs[config['id']]==config
  assert json.loads((raw/'campaign_snapshot.json').read_text())==campaign
  schedule=analysis.read(raw/'schedule.csv');measurement=[r for r in schedule if r['phase']=='measurement']
  assert len(measurement)==4096 and len({r['request_index'] for r in measurement})==4096
  if reference is None:reference=measurement
  else:matched(reference,measurement)
  if (raw/'runtime_image_id.before_launch').exists():assert (raw/'runtime_image_id.before_launch').read_text().strip()==campaign['image_id']
  ledger=raw/'measurement-1/requests.csv'
  if ledger.exists():
   rows=analysis.read(ledger);matched(measurement,rows)
   manifest=json.loads((ledger.parent/'arrival_manifest.json').read_text())
   phase=json.loads((ledger.parent/'result.json').read_text())
   if config['model'] in ('poisson','burst'):
    expected,shift=ctl.offsets(4096,config['model'],128)
    assert manifest['planned_offsets_ns']==expected and manifest['phase_seconds']==shift
    assert manifest['concurrency_cap'] is None
    key=config['model'];shape={k:manifest[k] for k in ('model','rate','seed','planned_offsets_ns','phase_seconds','request_ids')}
    if key in open_arrivals:assert open_arrivals[key]==shape
    open_arrivals[key]=shape
   else:
    assert manifest['planned_offsets_ns'] is None and manifest['concurrency_cap']==1
    strict=config['model']=='serial-quiescent'
    if result['status']=='ok':
     assert phase.get('inter_request_quiescence_count',0)==(4095 if strict else 0)
     assert phase['completed']==4096
    for prev,nxt in zip(rows,rows[1:]):
     if nxt['issued_mono_ns'] and prev['completed_mono_ns']:assert int(nxt['issued_mono_ns'])>=int(prev['completed_mono_ns'])
    if strict and result['status']=='ok':
     events=[json.loads(line) for line in (ledger.parent/'inter_request_quiescence.jsonl').read_text().splitlines()]
     assert len(events)==4095
     for prev,nxt,event in zip(rows,rows[1:],events):
      assert event['after_request_id']==prev['request_index'] and event['seconds']>=.5
      assert int(prev['completed_mono_ns'])<=event['start_mono_ns']<=event['end_mono_ns']==int(nxt['planned_mono_ns'])<=int(nxt['issued_mono_ns'])
  summary=json.loads((root/'derived'/raw.parent.name/raw.name/'summary.json').read_text())
  runs.append(summary)
 comparisons=pairs(runs)
 report=dict(campaign=campaign['fingerprint'],image=campaign['image_id'],evidence='EXPLORATORY_SINGLE_SEED_METHOD_VALIDATION',runs=runs,comparisons=comparisons,
  limits='Seed001 x1, 4096 identical request identities, cold initialization. Fixed 128/s is offered load, not concurrency cap or capacity. Serial user latency starts at endogenous release; not a latency comparison against the same exogenous demand. NC transport reuse enabled; TCP Direct unchanged per-request uncached owner fetch with 5s exchange timeout. Speedup only within method and this campaign. Legacy-style means quiescence pacing on new runtime, not reproduction of old raw measurements.',
  timing='measurement_seconds includes inter-request quiescence and final drain. inter_request_quiescence_seconds is a SUBSET, do not add twice. startup+setup+measurement+finalization approximates run wall (phase start gaps separate). Queue wall also includes preflight and analysis, retained in systemd journal.')
 ctl.dump(root/'derived/method_comparison.json',report)
 def fmt(x):return '—' if x is None else f'{x:.2f}'
 lines=['# 串行 / 开放到达：实验方法与耗时对照','','同版本、同方法比较；单 seed 探索性验证，不追求最大吞吐或预设收敛时延。',
  '旧式对照保留逐请求静默节奏，但使用新版本；不能把它当作旧镜像的复现。','',
  '| 条件 | 状态 | 完成/计划 | 启动 s | 初始化 s | 测量 s | 其中逐请求静默 s | 收尾 s | 完整墙钟 s |',
  '|---|---|---:|---:|---:|---:|---:|---:|---:|']
 for r in runs:lines.append('| '+ ' | '.join([r['config']['id'],r['status'],f"{r['completed']}/{r['planned']}",*[fmt(r[k]) for k in ('startup_seconds','setup_seconds','measurement_seconds','inter_request_quiescence_seconds','finalization_seconds','total_wall_seconds')]])+' |')
 lines+=['','## 同方法节省的实验时间','','| 方法 | 基线 → 新方式 | 测量节省 s | 测量倍数 | 完整节省 s | 完整倍数 |','|---|---|---:|---:|---:|---:|']
 for r in comparisons:lines.append(f"| {r['method']} | {r['baseline']} → {r['treatment']} | {r['measurement_seconds_saved']:.2f} | {r['measurement_speedup']:.2f} | {r['wall_seconds_saved']:.2f} | {r['wall_speedup']:.2f} |")
 if not comparisons:lines+=['','匹配运行尚未全部完成，不提前估算加速比。']
 lines+=['','## 解读边界','','- 开放到达不等待完成；128/s 是到达速率，不是固定并发数。',
  '- 串行的下一请求在完成/静默之后才释放，两种方式的用户时延不是同一外生需求下的性能对比。',
  '- 延迟分位数仅统计成功请求，所有未完成和失败仍保留在分母；不将超时赋予伪造延迟。',
  '- 原顺序 128 条一组及实际时间曲线同时保留，观察缓存与排队，不设自动收敛门槛。',
  '- NC 固定复用连接；TCP Direct 保持每请求独立连接、无缓存和原 5 秒交换超时，不用跨方法耗时归因于调度。',
  '- 逐请求静默已包含在测量秒数内，不能重复相加。队列完整时间另以 unit 开始/结束记录为准。']
 (root/'derived/method_comparison.md').write_text('\n'.join(lines)+'\n')
 return dict(runs=len(runs),pairs=len(comparisons))
