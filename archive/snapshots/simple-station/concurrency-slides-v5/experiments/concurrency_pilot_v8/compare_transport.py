"""Same-new-image connection reuse sensitivity; old runs verify inputs only."""
import collections, hashlib, json
import analysis, controller as ctl

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def matched(a,b):
 fields=('request_index','key','requester','owner','value_size_bytes','expected_fnv1a64','schedule_sha256')
 assert [[r[k] for k in fields] for r in a]==[[r[k] for k in fields] for r in b], 'request identity mismatch'

def build(root):
 campaign=json.loads((root/'campaign.json').read_text());runs=[];references={}
 for config in campaign['matrix']:
  if config['stage']!='load':continue
  model=config['model'];ref=campaign['baseline_inputs'][model]
  from pathlib import Path
  old=Path(ref['raw'])
  for name,d in ref['sha256'].items(): assert sha(old/name)==d,'old evidence changed: '+name
  references[model]=(analysis.read(old/'measurement-1/requests.csv'),json.loads((old/'measurement-1/arrival_manifest.json').read_text()))
 for path in sorted((root/'raw').glob('*/attempt*/result.json')):
  raw=path.parent;result=json.loads(path.read_text());config=result['config']
  if config['stage']!='load':continue
  if not (raw/'measurement-1/requests.csv').exists(): continue
  rows=analysis.read(raw/'measurement-1/requests.csv');manifest=json.loads((raw/'measurement-1/arrival_manifest.json').read_text())
  phase=json.loads((raw/'measurement-1/result.json').read_text());oldrows,oldmanifest=references[config['model']]
  matched(oldrows,rows)
  for k in ('model','rate','seed','phase_seconds','planned_offsets_ns','request_ids'): assert manifest[k]==oldmanifest[k],k
  assert json.loads((raw/'campaign_snapshot.json').read_text())==campaign
  assert (raw/'runtime_image_id.before_launch').read_text().strip()==campaign['image_id']
  env=json.loads((raw/'environment.json').read_text());assert env['RSYNC_QUERY_TIMEOUT_MS']=='30000'
  assert config['query_lifetime_seconds']==30 and env['KATHARA_NODE_LOOP_SLEEP_MS']==str(config['loop_sleep_ms'])
  ref=campaign['baseline_inputs'][config['model']]
  oldenv=json.loads((Path(ref['raw'])/'environment.json').read_text())
  allowed={'IMAGE_TAG','RSYNC_EVENT_TRACE_RUN_ID','RSYNC_EXPERIMENT_CONTROL_DIR','RSYNC_EXPERIMENT_DATA_DIR'}
  assert {k:v for k,v in env.items() if k not in allowed}=={k:v for k,v in oldenv.items() if k not in allowed},'unintended runtime parameter change'
  summary=json.loads((root/'derived'/raw.parent.name/raw.name/'summary.json').read_text())
  success=[r for r in rows if r['status']=='ok']
  # Global queued-frame count is a transport proxy, not QueryTable occupancy.
  queues=collections.defaultdict(collections.deque);maximum=0;waits=[]
  for p in (raw/'traces').glob('*.jsonl'):
   for line in p.open():
    e=json.loads(line);event=e.get('event');t=int(e.get('ts_mono_ns',0));key=(e.get('node'),e.get('dest'))
    if event=='pilot_transport_enqueue':
     queues[key].append((t,e.get('packet_type'),e.get('frame_bytes')));maximum=max(maximum,len(queues[key]))
    elif event in ('pilot_wire_send','pilot_transport_error'):
     assert queues[key], 'unmatched transport completion'
     start,kind,size=queues[key].popleft();assert (kind,size)==(e.get('packet_type'),e.get('frame_bytes')),'transport FIFO mismatch'
     if phase['base_mono_ns']<=start<=phase['ended_mono_ns']:waits.append((t-start)/1e6)
  counts=summary['mechanism_events']
  runs.append(dict(config=config,raw=str(raw),status=result['status'],reason=phase['reason'],**analysis.summary(rows),
   completed_execution_over_8s=sum(int(r['execution_ns'])>8_000_000_000 for r in success),
   completion_by_user_deadline={str(s):sum(int(r['total_latency_ns'])<=s*1e9 for r in success)/len(rows) for s in (1,7,8,15,30,60)},
   measurement_seconds=summary['measurement_seconds'],total_wall_seconds=result['total_wall_seconds'],timing=result['timing'],
   finalization_seconds=result['finalization_seconds'],finalization_errors=result['finalization_errors'],
   last_arrival_to_observation_end_seconds=(phase['ended_mono_ns']-phase['last_planned_mono_ns'])/1e9,
   last_arrival_to_last_completion_seconds=(phase['last_completion_mono_ns']-phase['last_planned_mono_ns'])/1e9 if phase['last_completion_mono_ns'] else None,
   query_expired_unsatisfied_events=counts.get('query_expired_unsatisfied',0),
   query_table_occupancy='not directly observed by frozen runtime; application outstanding and transport queues are separate proxies',
   late_data_attribution='No same-key guessing: aggregate expiry and over-8s application completions only; unique late-DATA causal attribution requires separate trace audit',
   transport_per_peer_peak_queued_frames_including_setup=maximum,transport_enqueue_to_send_ms=dict(mean=analysis.average(waits),p95=analysis.percentile(waits,.95),max=max(waits,default=None)),
   early=summary['early'],late=summary['late'],mechanism_events=counts,
   resources=summary['resources'],health=summary['health'],protocol_traffic=summary['protocol_traffic']))
 report=dict(evidence='EXPLORATORY_CONNECTION_REUSE_ABLATION',campaign=campaign['fingerprint'],runtime_image=campaign['image_id'],query_lifetime_seconds=30,runs=runs,
  limitations='One seed and one repetition per condition. Same NEW image fresh/reuse controls only. V7 verifies frozen identities/arrivals, not a performance control. CPU means include drain/wait; successful latencies are selected if incomplete. Connection reuse inherently retains TCP congestion state/Nagle interactions. No separate NODELAY, batching, or forwarding changes. No formal CI or auto-tuning.')
 ctl.dump(root/'derived/transport_comparison.json',report)
 lines=['# 逐消息建连 / 同邻居连接复用','','固定 25ms 轮询、30秒查询寿命；同一新镜像开关对照，独立冷启动。旧镜像不计入性能比较。',
  '时延仅对成功样本计算，未完成不赋予伪造时延。完成率、积压、失效与耗时必须一起看。','',
  '| 到达模型 | 复用 | 完成/4096 | 失效事件 | 成功均值 ms | 成功 p99 ms | 测量 s | 总 s | 状态 |',
  '|---|---:|---:|---:|---:|---:|---:|---:|---|']
 for r in sorted(runs,key=lambda r:(r['config']['model'],r['config']['reuse_connections'])):
  def fmt(x):return '—' if x is None else f'{x:.2f}'
  lines.append(f"| {r['config']['model']} | {r['config']['reuse_connections']} | {r['completed']}/4096 | {r['query_expired_unsatisfied_events']} | {fmt(r['total_latency_ms']['mean'])} | {fmt(r['total_latency_ms']['p99'])} | {fmt(r['measurement_seconds'])} | {fmt(r['total_wall_seconds'])} | {r['status']} |")
 lines+=['','JSON 另含全分母 deadline 完成比例、>8 秒成功数、资源、发送队列和分阶段耗时。',
  '冻结镜像未直接记录 QueryTable 实时容量；不把应用在途数当作查询表大小，不按相同 key 猜测迟到 DATA 归属。',
  '连接复用也保留 TCP 连接状态；结合 CPU、握手次数、发送队列、聚合和 fallback 帧数解读。',
  'CPU 均值包含排空/等待，不作为相同持续时间负载下的 CPU 对照。全成功但排空超时与请求未完成分开解释。']
 (root/'derived/transport_comparison.md').write_text('\n'.join(lines)+'\n')
 return report
