import asyncio,collections,json,tempfile,threading,time,unittest
from pathlib import Path
import controller as c
import pilot,analysis,tcp_node
import compare_cutoff
def requests(nodes=(1,1,2),keys=None):
 return [dict(request_index=str(i+1),requester=str(n),owner='32',key=(keys or ['same']*len(nodes))[i],value_size_bytes='3',expected_fnv1a64='hash',schedule_sha256='schedule',phase='measurement') for i,n in enumerate(nodes)]
def completion(r,start,end):return [r['request_index'],str(r['requester']),r['key'],'ok','3','hash',str(start),str(end),'999','data','schedule','','777']
class Tests(unittest.TestCase):
 def test_matrix(self):
  m=pilot.matrix();self.assertEqual(len(m),64);self.assertEqual({r['model'] for r in m},{'poisson','burst'})
  self.assertEqual(len({r['condition'] for r in m}),64)
  self.assertTrue(all(r['reuse_connections']==(r['method']=='nc') for r in m))
  self.assertTrue(all(r['query_lifetime_seconds']==30 for r in m))
  self.assertTrue(all(r['loop_sleep_ms']==25 and r['request_timeout_abort'] is False for r in m))
 def test_cutoff_off_keeps_started_request_and_accepts_late_completion(self):
  e=c.Engine(requests((1,2)),100,[0,9_000_000_000],request_timeout_abort=False);e.admit(100);r=e.dispatch(101)[0];e.started('1',1,102)
  e.check_timeout(8_000_000_103);self.assertEqual(r['status'],'issued');self.assertIn('1',e.active)
  e.admit(9_000_000_101);self.assertEqual(len(e.dispatch(9_000_000_102)),1)
  e.complete(completion(r,102,9_000_000_103),9_000_000_104);self.assertEqual(r['execution_ns'],9_000_000_001)
 def test_cutoff_off_preserves_dispatch_timeout(self):
  e=c.Engine(requests((1,)),100,[0],request_timeout_abort=False);e.admit(100);e.dispatch(101)
  with self.assertRaisesRegex(c.LoadTimeout,'dispatch_timeout'):e.check_timeout(8_000_000_102)
 def test_cutoff_off_preserves_global_watchdog_and_censoring(self):
  e=c.Engine(requests((1,)),100,[0],request_timeout_abort=False);e.admit(100);e.dispatch(101);e.started('1',1,102)
  with self.assertRaisesRegex(c.LoadTimeout,'global watchdog'):e.check_timeout(60_000_000_101)
  e.finish('global watchdog',60_000_000_101);self.assertEqual(e.rows[0]['status'],'incomplete');self.assertEqual(e.rows[0]['total_latency_ns'],'')
  self.assertEqual(e.rows[0]['observation_end_mono_ns'],60_000_000_101)
 def test_ablation_common_success_excludes_uncompleted_not_fake_latency(self):
  old=[dict(request_index='1',status='ok',total_latency_ns='1000000'),dict(request_index='2',status='request_timeout',total_latency_ns='')]
  new=[dict(request_index='1',status='ok',total_latency_ns='2000000'),dict(request_index='2',status='ok',total_latency_ns='9000000000')]
  result=compare_cutoff.paired_common_success(old,new)
  self.assertEqual(result['n'],1);self.assertEqual(result['new_minus_old_mean_user_ms'],1)
 def test_arrival_reproducible_and_frozen(self):
  for model in ('poisson','burst'):
   a,p=c.offsets(4096,model,32);b,q=c.offsets(4096,model,32);self.assertEqual((a,p),(b,q));self.assertTrue(all(x<y for x,y in zip(a,a[1:])))
 def test_serial_releases_on_completion_without_quiescence(self):
  e=c.Engine(requests(),100,[0,0,0],serial=True);e.admit(100);r=e.dispatch(101)[0]
  e.admit(999);self.assertEqual(e.dispatch(999),[])
  e.complete(completion(r,102,1000),1001);e.admit(1002);second=e.dispatch(1003)
  self.assertEqual(len(second),1);self.assertEqual(second[0]['planned_mono_ns'],1000)
  self.assertEqual(len(e.active),1);e.finish('stop',1004)
  self.assertEqual(e.rows[-1]['status'],'not_released_at_abort');self.assertEqual(e.rows[-1]['planned_mono_ns'],'')
 def test_serial_per_request_timeout_stays_eight_seconds(self):
  e=c.Engine(requests(),100,[0,0,0],serial=True);e.admit(100);e.dispatch(101)
  with self.assertRaises(c.LoadTimeout):e.check_timeout(8_000_000_102)
 def test_timing_only_complete_matched_versions(self):
  s=dict(config=dict(model='serial',method='nc',id='serial-nc'),status='ok',completed=4096,campaign='v3',schedule_sha256='same',raw='serial',measurement_seconds=400,total_wall_seconds=450)
  r=dict(s,config=dict(model='poisson',method='nc',id='poisson-r32-nc'),raw='open',measurement_seconds=130,total_wall_seconds=180)
  self.assertAlmostEqual(analysis.time_comparison([s,r])[0]['wall_ratio'],2.5)
  self.assertEqual(analysis.time_comparison([s,dict(r,status='load_timeout')]),[])
  with self.assertRaises(ValueError):analysis.time_comparison([s,dict(r,campaign='v2')])
 def test_poisson_and_burst_rate(self):
  for model in ('poisson','burst'):
   a,p=c.offsets(100000,model,32);self.assertAlmostEqual(len(a)/(a[-1]/1e9),32,delta=.5)
   if model=='burst':self.assertAlmostEqual(sum((t/1e9+p)%5>=4 for t in a)/len(a),.8,delta=.02)
 def test_same_node_all_dispatch_without_completion(self):
  e=c.Engine(requests((1,)*128),100,[0]*128);e.admit(100);self.assertEqual(len(e.dispatch(101)),128);self.assertEqual(len(e.active),128)
 def test_arrival_independent_of_completion(self):
  e=c.Engine(requests(),100,[0,10,20]);e.admit(100);e.dispatch(101);e.admit(120);self.assertEqual(len(e.dispatch(121)),2);self.assertEqual(len(e.active),3)
 def test_out_of_order_same_key_association(self):
  e=c.Engine(requests(),100,[0,0,0]);e.admit(100);a,b,d=e.dispatch(101)
  e.complete(completion(b,102,105),106);self.assertIn('1',e.active);self.assertIn('3',e.active);self.assertEqual(set(e.done),{'2'})
 def test_queue_included(self):
  e=c.Engine(requests((1,)),100,[10]);e.admit(150);r=e.dispatch(200)[0];e.complete(completion(r,250,400),401)
  self.assertEqual((r['scheduler_delay_ns'],r['node_queue_ns'],r['execution_ns'],r['total_latency_ns']),(40,100,150,290))
 def test_duplicate_unknown_completion_and_start(self):
  e=c.Engine(requests((1,)),100,[0]);e.admit(100);r=e.dispatch(101)[0];f=completion(r,102,103);e.complete(f,104)
  with self.assertRaises(c.CorrectnessError):e.complete(f,105)
  with self.assertRaises(c.CorrectnessError):e.started('2',1,106)
 def test_corruption(self):
  for i in (1,2,4,5,10):
   e=c.Engine(requests((1,)),100,[0]);e.admit(100);r=e.dispatch(101)[0];f=completion(r,102,103);f[i]='bad'
   with self.assertRaises(c.CorrectnessError):e.complete(f,104)
 def test_transport_failure_not_integrity_failure(self):
  e=c.Engine(requests((1,)),100,[0]);e.admit(100);r=e.dispatch(101)[0];f=completion(r,102,103);f[3]='io_error';f[4]='0';f[5]='empty'
  with self.assertRaises(c.TransportFailure):e.complete(f,104)
  self.assertEqual(r['status'],'transport_error');self.assertEqual(r['total_latency_ns'],'')
 def test_timeout_denominators_distinguish_future(self):
  e=c.Engine(requests((1,1,1)),100,[0,0,20_000_000_000]);e.admit(100);e.dispatch(101)
  with self.assertRaises(c.LoadTimeout):e.check_timeout(8_000_000_102)
  e.finish('timeout',8_000_000_102);self.assertEqual([r['status'] for r in e.rows],['dispatch_timeout','incomplete','not_arrived_at_abort'])
 def test_watchdog(self):
  e=c.Engine(requests(),100,[0,1,2])
  with self.assertRaises(c.LoadTimeout):e.check_timeout(60_000_000_103)
 def test_rate_skip_is_per_method_model(self):
  b={('nc','poisson'):8}
  self.assertTrue(pilot.can_skip(dict(method='nc',model='poisson',rate=32),b))
  self.assertFalse(pilot.can_skip(dict(method='tcp-direct',model='poisson',rate=32),b))
  self.assertFalse(pilot.can_skip(dict(method='nc',model='burst',rate=32),b))
 def test_archive_temporary_files_excluded_and_formal_checked(self):
  with tempfile.TemporaryDirectory() as directory:
   r=Path(directory);s=r/'s';s.mkdir();(s/'.node.stopped.tmp.42').write_text('transient');(s/'node.stopped').write_text('done')
   pilot.copy_evidence(s,r/'d');self.assertEqual(list(p.name for p in (r/'d').iterdir()),['node.stopped'])
 def test_finalize_errors_preserve_outcome_and_always_cleanup(self):
  for fail in ('stop','archive','cleanup'):
   with tempfile.TemporaryDirectory() as directory:
    r=Path(directory);called=[]
    def action(name):
     def f():
      self.assertTrue((r/'measurement_outcome.json').exists());called.append(name)
      if name==fail:raise RuntimeError(name)
     return f
    x=pilot.finalize(r,dict(status='ok'),action('stop'),action('archive'),action('cleanup'))
    self.assertEqual(x['measurement_status'],'ok');self.assertEqual(x['status'],'finalization_failure');self.assertIn('cleanup',called)
    if fail=='stop':self.assertNotIn('archive',called)
 def test_delayed_stop_precedes_archive(self):
  with tempfile.TemporaryDirectory() as directory:
   done=[]
   def stop():time.sleep(.01);done.append('stopped')
   def archive():self.assertEqual(done,['stopped'])
   r=pilot.finalize(Path(directory),dict(status='ok'),stop,archive,lambda:None);self.assertEqual(r['status'],'ok')
 def test_audit_duplicates_and_unknown_files(self):
  with tempfile.TemporaryDirectory() as directory:
   r=Path(directory);(r/'delivery-audit').mkdir();(r/'completions').mkdir();(r/'completions/1.tsv').write_text('test')
   a=r/'delivery-audit/node1.tsv';a.write_text('1\t1\tkey\n');rows=[dict(request_index='1',status='ok')];c.validate_audit(r,rows)
   a.write_text('1\t1\tkey\n1\t1\tkey\n')
   with self.assertRaises(c.CorrectnessError):c.validate_audit(r,rows)
 def test_analysis_warmup_queue_and_flat(self):
  for pattern in ('flat','warmup','queue'):
   rows=[]
   for i in range(4096):
    ex=100 if pattern!='warmup' else 100+200*(1-i/4096);q=100*i/4096 if pattern=='queue' else 0
    rows.append(dict(status='ok',total_latency_ns=int((ex+q)*1e6),execution_ns=int(ex*1e6),node_queue_ns=int(q*1e6),scheduler_delay_ns=0,via='local_cache'))
   batches,windows=analysis.batch_analysis(rows);last=windows[-1]
   if pattern=='flat':self.assertEqual(last['execution_ms_slope_per_batch'],0)
   if pattern=='warmup':self.assertLess(last['execution_ms_slope_per_batch'],0)
   if pattern=='queue':self.assertGreater(last['node_queue_ms_slope_per_batch'],0);self.assertEqual(last['execution_ms_slope_per_batch'],0)
   self.assertNotIn('converged',last)
 def test_burst_only_complete_cycles(self):
  rows=[dict(status='not_issued',planned_mono_ns=i*1_000_000_000) for i in range(21)]
  cycles=analysis.complete_burst_cycles(rows,dict(base_mono_ns=0,phase_seconds=2),dict(ended_mono_ns=20_000_000_000,last_planned_mono_ns=20_000_000_000))
  self.assertEqual([r['start_seconds'] for r in cycles],[3,8,13]);self.assertTrue(all(r['planned']==5 for r in cycles))
 def test_failure_rows_keep_denominator(self):
  x=analysis.summary([dict(status='not_issued') for _ in range(4096)]);self.assertEqual(x['planned'],4096);self.assertEqual(x['completed'],0);self.assertIsNone(x['total_latency_ms']['mean'])
 def test_real_controller_same_node_file_overlap(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);control=root/'control';control.mkdir();stop=threading.Event();seen=set();live=[]
   def helpers():
    while not stop.is_set():
     for p in control.glob('commands/node*/*.tsv'):
      if p in seen:continue
      seen.add(p);f=p.read_text().strip().split('\t');node=p.parent.name.removeprefix('node');start=time.monotonic_ns();c.atomic(control/'started'/f'{f[0]}.tsv',f'{f[0]}\t{node}\t{start}\n');live.append((f,node,start))
     for f,node,start in list(live):
      if time.monotonic_ns()-start<25_000_000:continue
      end=time.monotonic_ns();c.atomic(control/'completions'/f'{f[0]}.tsv','\t'.join([f[0],node,f[2],'ok',f[4],f[5],str(start),str(end),'999','data','schedule'])+'\n');live.remove((f,node,start))
     for p in control.glob('quiesce/*.marker'):
      for n in range(1,33):
       ack=control/'quiesced'/f'{p.stem}-node{n}.ack'
       if not ack.exists():c.atomic(ack,'quiet\n')
     time.sleep(.001)
   t=threading.Thread(target=helpers);t.start()
   try:
    result,rows=c.execute(requests((1,1,1)),control,root/'out','nc','diagnostic',arrival=[0,1_000_000,2_000_000]);self.assertEqual(result['status'],'ok',result)
    self.assertEqual(pilot.peak(rows,True)['1'],3)
   finally:stop.set();t.join()
class AsyncTCPTests(unittest.IsolatedAsyncioTestCase):
 async def test_client_and_owner_overlap_without_cache(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);value=b'abc';server=tcp_node.Runtime(32,{'same':value},root/'server','schedule',root/'servertrace')
   live=peak=0;original=server.serve
   async def delayed(reader,writer):
    nonlocal live,peak
    live+=1;peak=max(peak,live);await asyncio.sleep(.03)
    try:await original(reader,writer)
    finally:live-=1
   listener=await asyncio.start_server(delayed,'127.0.0.1',0);port=listener.sockets[0].getsockname()[1]
   client=tcp_node.Runtime(1,{},root/'client','schedule',root/'clienttrace',lambda _n:('127.0.0.1',port))
   try:
    for i in range(4):client.submit([str(i),'tcp-direct','same','32','3',tcp_node.fnv1a64(value),'schedule'])
    await asyncio.gather(*list(client.clients));self.assertGreaterEqual(peak,2)
    files=list((root/'client/completions').glob('*.tsv'));self.assertEqual(len(files),4);self.assertEqual(client.data,{})
    self.assertEqual(len((root/'clienttrace').read_text().splitlines()),4)
    starts=[int(p.read_text().split('\t')[6]) for p in files];ends=[int(p.read_text().split('\t')[7]) for p in files];self.assertLess(max(starts),min(ends))
    with self.assertRaises(ValueError):client.submit(['0','tcp-direct','same','32','3',tcp_node.fnv1a64(value),'schedule'])
   finally:listener.close();await listener.wait_closed()
if __name__=='__main__':unittest.main()
