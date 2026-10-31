import json,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import controller as c,compare_method as m,pilot
from test_pilot import requests,completion

class MethodTests(unittest.TestCase):
 def test_strict_does_not_release_until_quiescence(self):
  e=c.Engine(requests(),100,[0,0,0],serial=True,per_request_quiescence=True)
  e.admit(100);r=e.dispatch(101)[0];e.complete(completion(r,102,103),104)
  e.admit(105);self.assertEqual(e.next,1)
  e.release_after_quiet=500;e.admit(499);self.assertEqual(e.next,1)
  e.admit(501);r=e.dispatch(502)[0];self.assertEqual(r['planned_mono_ns'],500)
  self.assertIsNone(e.release_after_quiet)
 def test_strict_forbidden_on_open(self):
  with self.assertRaises(c.CorrectnessError):c.Engine(requests(),100,[0,0,0],per_request_quiescence=True)
 def test_serial_disabled_eight_seconds_still_bounded(self):
  e=c.Engine(requests(),100,[0,0,0],serial=True,request_timeout_abort=False)
  e.admit(100);e.dispatch(101);e.started('1',1,102);e.check_timeout(9_000_000_000)
  with self.assertRaisesRegex(c.LoadTimeout,'serial last-release watchdog'):e.check_timeout(60_000_000_101)
  e.finish('watchdog',60_000_000_101);self.assertEqual(len(e.rows),3);self.assertEqual(e.rows[-1]['status'],'not_released_at_abort')
 def test_pairs_keep_method_provenance_and_denominator(self):
  def row(model,method='nc',wall=300):return dict(config=dict(model=model,method=method),campaign='v9',schedule_sha256='same',status='ok',completed=4096,measurement_seconds=wall-100,total_wall_seconds=wall)
  a,b,d=row('serial-quiescent',wall=2500),row('serial'),row('poisson',wall=140)
  pairs=m.pairs([a,b,d]);self.assertEqual(len(pairs),3)
  self.assertEqual(m.pairs([a,dict(d,completed=4095)]),[])
  self.assertEqual(m.pairs([a,row('poisson','tcp-direct',140)]),[])
  with self.assertRaises(ValueError):m.pairs([a,dict(d,campaign='old')])
 def test_identity_order_rejected(self):
  r=requests();self.assertIsNone(m.matched(r,r))
  with self.assertRaises(ValueError):m.matched(r,list(reversed(r)))
 def test_controller_strict_end_to_end_and_retained_failures(self):
  for fail in (False,True):
   with self.subTest(fail=fail),tempfile.TemporaryDirectory() as directory:
    root=Path(directory);control=root/'control';control.mkdir();stop=threading.Event();seen=set()
    def helper():
     while not stop.is_set():
      for p in control.glob('commands/node*/*.tsv'):
       if p in seen:continue
       seen.add(p);f=p.read_text().strip().split('\t');start=time.monotonic_ns();time.sleep(.003);end=time.monotonic_ns()
       c.atomic(control/'completions'/f'{f[0]}.tsv','\t'.join([f[0],'1',f[2],'ok',f[4],f[5],str(start),str(end),'999','data','schedule'])+'\n')
      for p in control.glob('quiesce/*.marker'):
       for n in range(1,33):
        ack=control/'quiesced'/f'{p.stem}-node{n}.ack'
        if not ack.exists():c.atomic(ack,'quiet\n')
      time.sleep(.001)
    t=threading.Thread(target=helper);t.start()
    try:
     if fail:
      with patch.object(c,'quiesce',side_effect=c.LoadTimeout('fixture quiet failure')):res,rows=c.execute(requests((1,1,1)),control,root/'out','nc','serial-quiescent',request_timeout_abort=False)
      self.assertEqual(res['status'],'load_timeout');self.assertEqual(res['completed'],1);self.assertEqual(len(rows),3)
      self.assertTrue(all(r['status']=='not_released_at_abort' for r in rows[1:]))
     else:
      res,rows=c.execute(requests((1,1,1)),control,root/'out','nc','serial-quiescent',request_timeout_abort=False)
      self.assertEqual(res['status'],'ok',res);self.assertEqual(res['inter_request_quiescence_count'],2)
      self.assertGreaterEqual(res['inter_request_quiescence_seconds'],1.0);self.assertGreaterEqual(res['final_quiescence_seconds'],.5)
      self.assertEqual(pilot.peak(rows).get('all'),1)
      for a,b in zip(rows,rows[1:]):self.assertGreaterEqual(b['planned_mono_ns']-a['completed_mono_ns'],500_000_000)
    finally:stop.set();t.join()

if __name__=='__main__':unittest.main()
