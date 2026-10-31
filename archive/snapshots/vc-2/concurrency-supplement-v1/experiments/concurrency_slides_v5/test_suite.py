import collections,json,math,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import matrix,controller as c,federation,reports,pilot

class SuiteTests(unittest.TestCase):
 def test_matrix_counts_and_dedup(self):
  self.assertEqual(collections.Counter(x['family'] for x in matrix.conditions()),dict(controlled=8,zipf=16,uniform=16,size=8,federation=16))
  self.assertEqual(len(matrix.slots('smoke')),6);self.assertEqual(len(matrix.slots('pilot')),64)
  ss=matrix.slots('formal');self.assertEqual(len(ss),1280);self.assertEqual(len({s['id'] for s in ss}),1280)
  self.assertEqual(sum(matrix.request_count(s) for s in ss),7700480)  # supplement schedules: controlled 4096 (was 256), federation 11776 (was 3584)
  for k in {s['condition'] for s in ss}:self.assertEqual({(s['seed'],s['repetition']) for s in ss if s['condition']==k},{(seed,rep) for seed in range(1,11) for rep in (1,2)})
  for a,b in zip(ss[::2],ss[1::2]):
   self.assertEqual((a['seed'],a['repetition']),(b['seed'],b['repetition']));self.assertNotEqual(a['method'],b['method'])
   self.assertTrue(all(a[k]==b[k] for k in ('family','hosts','size','catalog','distribution','model','rate')))
 def test_arrival_seed_and_method_matching(self):
  for seed in range(1,11):
   for model in ('poisson','burst'):
    self.assertEqual(c.offsets(4096,model,128,9172+seed),c.offsets(4096,model,128,9172+seed))
  self.assertNotEqual(c.offsets(16,'poisson',32,9173),c.offsets(16,'poisson',32,9174))
 def test_expiry_classifier_rejects_wrong_extra_late_counts(self):
  probes=[dict(requester='2'),dict(requester='34')];starts={2:100,34:100}
  def event(n):return dict(event='query_expired_unsatisfied',node=n,src=str(n),phase='fallback',reason='query-lifetime-expired',value_bytes='1',fallback='1',ts_mono_ns=30_000_000_101)
  good=[event(2),event(34)];self.assertEqual(len(federation.expiry_evidence(good,probes,starts,99,31_000_000_000)),2)
  for bad in (good+[event(2)],[event(3)], [dict(event(2),ts_mono_ns=1_000)], [dict(event(2),value_bytes='2')], [dict(event(2),key='formal')], [dict(event(2),reason='different')]):
   with self.assertRaises(c.CorrectnessError):federation.expiry_evidence(bad,probes,starts,99,31_000_000_000)
 def test_aggregate_two_reps_then_ten_seed_means(self):
  reps={s:{1:s-1,2:s+1} for s in range(1,11)};means,stat=reports.aggregate(reps)
  self.assertEqual(means,list(range(1,11)));self.assertEqual(stat['mean'],5.5)
  self.assertAlmostEqual(stat['ci95_high']-5.5,2.2621571627409915*math.sqrt(82.5/9)/math.sqrt(10))
  with self.assertRaises(AssertionError):reports.aggregate({s:v for s,v in reps.items() if s!=10})
  reps[2].pop(2)
  with self.assertRaises(AssertionError):reports.aggregate(reps)
 def test_formal_partial_never_publishes(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(AssertionError):reports.formal(Path(d),[])
   self.assertFalse((Path(d)/'derived/formal_complete').exists())
 def test_federation_local_remote_kept_separate(self):
  rows=[dict(phase='post',bin='1',direction=d,request_class=k,status='ok',total_latency_ns=t,execution_ns=t,node_queue_ns=0,scheduler_delay_ns=0,via='data') for d,k,t in [('A_local','local_control',100),('A_to_B','remote',1000)]]
  gs=reports.grouped(rows);self.assertEqual(len(gs),2);self.assertNotEqual(gs[0]['phase'],gs[1]['phase'])
 def test_status_read_only(self):
  with tempfile.TemporaryDirectory() as d:
   with patch.object(pilot,'ROOT',Path(d)),patch.object(pilot,'LAB',Path(d)/'lab'),patch.object(pilot,'DERIVED',Path(d)/'derived'),patch.object(pilot.subprocess,'run') as call:
    call.return_value.stdout='inactive';s=pilot.status();self.assertEqual(s['stages']['formal']['target'],1280);self.assertEqual(list(Path(d).iterdir()),[])
 def test_quiescence_dynamic_inventory(self):
  for nodes in (16,32,64):
   with tempfile.TemporaryDirectory() as d:
    root=Path(d);(root/'quiesced').mkdir()
    for n in range(1,nodes+1):(root/'quiesced'/f'test-node{n}.ack').write_text('ok')
    with patch.object(c.time,'sleep'):c.quiesce(root,'test',nodes=nodes)
    self.assertEqual(list((root/'quiesced').iterdir()),[])

if __name__=='__main__':unittest.main()
