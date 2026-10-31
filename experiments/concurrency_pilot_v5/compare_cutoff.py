"""Descriptive cutoff ablation; censored requests never receive fake latency."""
import csv,hashlib,json,pathlib
import analysis,controller as ctl
PARENT=pathlib.Path('/data/networkcache-concurrency-pilot-v4')
def read(p):
 with p.open() as h:return list(csv.DictReader(h))
def describe(raw):
 rows=read(raw/'measurement-1/requests.csv');res=json.loads((raw/'measurement-1/result.json').read_text());outcome=json.loads((raw/'result.json').read_text())
 success=[r for r in rows if r['status']=='ok'];started=[r for r in rows if r.get('node_start_mono_ns')]
 end=res['ended_mono_ns'];counts=analysis.summary(rows)
 return dict(raw=str(raw),status=outcome['status'],reason=res['reason'],planned=len(rows),completed=len(success),statuses=counts['statuses'],
  completion_fraction=len(success)/len(rows),measurement_seconds=(end-res['base_mono_ns'])/1e9,total_wall_seconds=outcome['total_wall_seconds'],
  user_latency_ms_success_only=counts['total_latency_ms'],execution_ms_success_only=counts['execution_ms'],
  started_not_completed=sum(r['status']!='ok' for r in started),
  started_uncompleted_past_8s=sum(r['status']!='ok' and end-int(r['node_start_mono_ns'])>8_000_000_000 for r in started),
  completed_execution_over_8s=sum(int(r['execution_ns'])>8_000_000_000 for r in success),
  final_quiescence_seconds=res.get('final_quiescence_seconds'),censoring='No completion time or latency imputed for incomplete/unissued requests'),rows
def paired_common_success(old,new):
 left={r['request_index']:r for r in old if r['status']=='ok'};right={r['request_index']:r for r in new if r['status']=='ok'};ids=sorted(left.keys()&right.keys(),key=int)
 return dict(n=len(ids),new_minus_old_mean_user_ms=sum((int(right[i]['total_latency_ns'])-int(left[i]['total_latency_ns']))/1e6 for i in ids)/len(ids) if ids else None,
  scope='Conditional on success in both independent runs; selection-biased, descriptive only, no causal or capacity claim')
def build(root):
 campaign=json.loads((root/'campaign.json').read_text());comparisons=[]
 for new in sorted((root/'raw').glob('*/attempt*')):
  if not (new/'result.json').exists() or not (new/'measurement-1/requests.csv').exists():continue
  old=PARENT/'raw'/new.parent.name/'attempt001';reference=campaign['baseline_inputs'][new.parent.name]
  for name,digest in reference['sha256'].items():
   assert hashlib.sha256((old/name).read_bytes()).hexdigest()==digest,'baseline changed: '+name
  baseline=json.loads((old/'campaign_snapshot.json').read_text());snapshot=json.loads((new/'campaign_snapshot.json').read_text())
  assert baseline['image_id']==snapshot['image_id']==campaign['image_id']
  left,lrows=describe(old);right,rrows=describe(new)
  fields=('request_index','key','requester','owner','value_size_bytes','expected_fnv1a64','schedule_sha256')
  assert [[r[k] for k in fields] for r in lrows]==[[r[k] for k in fields] for r in rrows]
  a=json.loads((old/'measurement-1/arrival_manifest.json').read_text());b=json.loads((new/'measurement-1/arrival_manifest.json').read_text())
  assert all(a[k]==b[k] for k in ('model','rate','seed','phase_seconds','planned_offsets_ns','request_ids'))
  comparisons.append(dict(config=new.parent.name,cutoff_on=left,cutoff_off=right,common_success=paired_common_success(lrows,rrows)))
 report=dict(dimension='started_request_abort_seconds: 8 -> disabled',unchanged_protocol_query_lifetime_seconds=7,
  unchanged_dispatch_abort_seconds=8,unchanged_watchdog_seconds_after_last_arrival=60,runtime_image=campaign['image_id'],comparisons=comparisons,
  interpretation='Changing the cutoff changes exposure and sample composition. Compare completion/censoring and latency together; single seed, no CI, no proof of convergence.')
 ctl.dump(root/'derived/cutoff_comparison.json',report)
 lines=['# 单请求8秒终止策略对照','','只取消测量阶段已开始请求的8秒全局终止；同 v4 镜像，协议7秒寿命不变。',
  '单 seed 描述性对照。未完成请求没有被赋予8秒、60秒或0秒的伪造时延。','',
  '| 条件 | 策略 | 成功/计划 | 已开始未完成 | 测量秒 | 成功样本平均用户ms |','|---|---|---:|---:|---:|---:|']
 for pair in comparisons:
  for key in ('cutoff_on','cutoff_off'):
   x=pair[key];mean=x['user_latency_ms_success_only']['mean'];value=f'{mean:.2f}' if mean is not None else '—'
   lines.append(f"| {pair['config']} | {key} | {x['completed']}/{x['planned']} | {x['started_not_completed']} | {x['measurement_seconds']:.2f} | {value} |")
 lines+=['','成功样本时延不能单独解释为改善：对照组提前终止，实际暴露负载与样本范围不同。',
  '完整 JSON 同时提供状态分母、超过8秒仍未完成数、双侧共同成功请求的条件性差值；后者仍有选择偏差。',
  'batch/time 曲线沿用原请求顺序；不得从缺失尾部或未完成请求推断收敛。']
 (root/'derived/cutoff_comparison.md').write_text('\n'.join(lines)+'\n')
