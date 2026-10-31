import json,random,tempfile,unittest,threading,time
from pathlib import Path
import controller as c
import pilot

def requests(nodes=(1,2,1)):
 return [dict(request_index=str(i+1),requester=str(n),owner='32',key='shared',value_size_bytes='65536',expected_fnv1a64='hash',schedule_sha256='schedule') for i,n in enumerate(nodes)]
def completion(row,start,end):
 return [row['request_index'],row['requester'],row['key'],'ok','65536','hash',str(start),str(end),'packet','data','schedule']

class Tests(unittest.TestCase):
 def test_bootstrap_install_at_shared_root(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);source=root/'source';shared=source/'kathara/ruikekkk/shared';shared.mkdir(parents=True)
   for name in ('start-node.sh','start-switch.sh'):
    p=shared/name;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o755)
   evidence=pilot.install_lab_support(root/'lab',source)
   self.assertEqual(len(evidence),2)
   for name in evidence:
    self.assertEqual((root/'lab/shared'/name).read_bytes(),(shared/name).read_bytes())
    self.assertTrue((root/'lab/shared'/name).stat().st_mode&0o111)
 def test_bootstrap_missing_or_unexecutable_rejected_before_copy(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);shared=root/'source/kathara/ruikekkk/shared';shared.mkdir(parents=True)
   for name in ('start-node.sh','start-switch.sh'):
    p=shared/name;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o755)
   (shared/'start-switch.sh').chmod(0o644)
   with self.assertRaises(c.InfrastructureError):pilot.install_lab_support(root/'lab',root/'source')
   self.assertFalse((root/'lab').exists())
   (shared/'start-switch.sh').unlink()
   with self.assertRaises(c.InfrastructureError):pilot.check_bootstrap(root/'source')
 def test_bootstrap_invalid_shell_rejected(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);shared=root/'kathara/ruikekkk/shared';shared.mkdir(parents=True)
   for name in ('start-node.sh','start-switch.sh'):
    p=shared/name;p.write_text('#!/bin/sh\nif\n');p.chmod(0o755)
   with self.assertRaises(pilot.subprocess.CalledProcessError):pilot.check_bootstrap(root)
 def test_execution_revision_isolates_old_attempts(self):
  self.assertNotEqual(pilot.RAW,pilot.ROOT/'raw');self.assertNotEqual(pilot.LAB,pilot.ROOT/'lab')
  self.assertEqual(pilot.REVISION,'bootstrap-v2')
 def test_matrix(self):
  m=pilot.matrix();self.assertEqual(len(m),20);self.assertEqual(len({r['id'] for r in m}),20)
  self.assertEqual(sum(r['stage']=='smoke' for r in m),6)
  for a,b in zip(m[::2],m[1::2]):self.assertNotEqual(a['method'],b['method'])
 def test_arrival_reproducible_and_method_independent(self):
  for model in ('poisson','burst'):
   a,p=c.offsets(4096,model,32);b,q=c.offsets(4096,model,32)
   self.assertEqual((a,p),(b,q));self.assertTrue(all(x<y for x,y in zip(a,a[1:])))
 def test_poisson_rate(self):
  a,_=c.offsets(100000,'poisson',32)
  self.assertAlmostEqual(len(a)/(a[-1]/1e9),32,delta=.5)
 def test_burst_integrated_rate(self):
  a,p=c.offsets(100000,'burst',32)
  self.assertAlmostEqual(len(a)/(a[-1]/1e9),32,delta=.5)
  high=sum(((t/1e9+p)%5)>=4 for t in a)
  self.assertAlmostEqual(high/len(a),.8,delta=.02)
 def test_open_admits_independent_of_completion(self):
  e=c.Engine(requests(),'open',100,[0,10,20]);e.admit(120)
  self.assertEqual(e.next,3);self.assertEqual(len(e.dispatch(120)),2)
  self.assertEqual(sum(map(len,e.queues.values())),1)
  self.assertEqual(e.rows[2]['planned_mono_ns'],120)
 def test_fifo_and_out_of_order_completion(self):
  e=c.Engine(requests(),'open',100,[0,0,0]);e.admit(100);a,b=e.dispatch(101)
  e.complete(completion(b,102,105),106);self.assertEqual(e.dispatch(107),[])
  e.complete(completion(a,102,110),111);self.assertEqual(e.dispatch(112)[0]['request_index'],'3')
 def test_same_key_different_nodes_are_separate(self):
  e=c.Engine(requests((1,2)),'open',100,[0,0]);e.admit(100);a,b=e.dispatch(101)
  e.complete(completion(a,102,110),111);self.assertEqual(len(e.done),1);self.assertIn(2,e.active)
 def test_late_admission_latency_includes_queue(self):
  e=c.Engine(requests((1,)),'open',100,[10]);e.admit(150);r=e.dispatch(200)[0]
  e.complete(completion(r,250,400),401)
  self.assertEqual(r['total_latency_ns'],290);self.assertEqual(r['scheduler_delay_ns'],40);self.assertEqual(r['node_queue_ns'],100)
 def test_duplicate_completion_rejected(self):
  e=c.Engine(requests((1,)),'open',100,[0]);e.admit(100);r=e.dispatch(101)[0];f=completion(r,102,103)
  e.complete(f,104)
  with self.assertRaises(c.CorrectnessError):e.complete(f,105)
 def test_corrupt_identity_size_hash_and_schedule(self):
  for index in (1,2,4,5,10):
   e=c.Engine(requests((1,)),'open',100,[0]);e.admit(100);r=e.dispatch(101)[0];f=completion(r,102,103);f[index]='999'
   with self.assertRaises(c.CorrectnessError):e.complete(f,104)
 def test_timeout_retains_all_rows(self):
  e=c.Engine(requests(),'open',100,[0,0,0]);e.admit(100);e.dispatch(101)
  with self.assertRaises(c.LoadTimeout):e.check_timeout(8_000_000_102)
  e.finish_incomplete('timeout');self.assertEqual(len(e.rows),3);self.assertEqual(e.rows[2]['status'],'not_issued')
 def test_watchdog(self):
  e=c.Engine(requests(),'open',0,[0,10,20])
  with self.assertRaises(c.LoadTimeout):e.check_timeout(60_000_000_021)
 def test_closed_cap(self):
  e=c.Engine(requests(),'closed',100,cap=1);e.admit(100);self.assertEqual(e.next,1);self.assertEqual(len(e.dispatch(101)),1)
 def test_start_validation(self):
  e=c.Engine(requests((1,)),'open',100,[0]);e.admit(100);e.dispatch(101)
  with self.assertRaises(c.CorrectnessError):e.started('1',1,99)
  e.started('1',1,102)
  with self.assertRaises(c.CorrectnessError):e.complete(completion(e.rows[0],103,104),105)
 def test_audit_checks_duplicates_and_missing(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'delivery-audit').mkdir();p=root/'delivery-audit/node1.tsv'
   p.write_text('1\t1\tkey\n');rows=[dict(request_index='1',status='ok')];c.validate_audit(root,rows)
   p.write_text('1\t1\tkey\n1\t1\tkey\n')
   with self.assertRaises(c.CorrectnessError):c.validate_audit(root,rows)
 def test_no_global_marker_or_phase_decomposition(self):
  text=Path(c.__file__).read_text();self.assertNotIn('monitor/current.tsv',text)
 def test_real_controller_file_boundary(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);control=root/'control';control.mkdir();stop=threading.Event();seen=set();live=[]
   def helpers():
    while not stop.is_set():
     for p in control.glob('commands/node*/*.tsv'):
      if p in seen:continue
      seen.add(p);f=p.read_text().strip().split('\t');node=p.parent.name.removeprefix('node');start=time.monotonic_ns()
      c.atomic(control/'started'/f'{f[0]}.tsv',f'{f[0]}\t{node}\t{start}\n');live.append((f,node,start))
     for f,node,start in list(live):
      if time.monotonic_ns()-start<20_000_000:continue
      end=time.monotonic_ns();text='\t'.join([f[0],node,f[2],'ok',f[4],f[5],str(start),str(end),'packet','data',f[6]])+'\n'
      c.atomic(control/'completions'/f'{f[0]}.tsv',text);live.remove((f,node,start))
     for p in control.glob('quiesce/*.marker'):
      for n in range(1,33):
       ack=control/'quiesced'/f'{p.stem}-node{n}.ack'
       if not ack.exists():c.atomic(ack,'quiet\n')
     time.sleep(.001)
   thread=threading.Thread(target=helpers);thread.start()
   try:
    result,rows=c.execute(requests(),control,root/'out','nc','poisson',10000,cap=32)
    self.assertEqual(result['status'],'ok',result);self.assertEqual(len(rows),3)
    self.assertTrue(all(r['total_latency_ns']>=r['execution_ns'] for r in rows))
    self.assertLess(rows[1]['node_start_mono_ns'],rows[0]['completed_mono_ns'])
   finally:stop.set();thread.join()
if __name__=='__main__':unittest.main()
