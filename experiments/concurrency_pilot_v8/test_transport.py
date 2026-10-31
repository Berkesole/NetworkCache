import tempfile,unittest
from pathlib import Path
import pilot,compare_transport
class TransportTests(unittest.TestCase):
 def test_smokes_first(self):
  m=pilot.matrix();self.assertEqual([c['stage'] for c in m[:2]],['smoke','smoke']);self.assertEqual([c['reuse_connections'] for c in m[:2]],[False,True]);self.assertEqual(len({c['id'] for c in m}),6)
 def test_frozen_parameters(self):
  for c in pilot.matrix():
   e=pilot.environment(Path('/lab'),Path('/raw/run/attempt001'),'nc','hash',c['query_lifetime_seconds'],c['loop_sleep_ms'])
   self.assertEqual(e['RSYNC_QUERY_TIMEOUT_MS'],'30000');self.assertEqual(e['KATHARA_NODE_LOOP_SLEEP_MS'],'25');self.assertEqual(e['RSYNC_FALLBACK_DELAY_MS'],'1500')
  with self.assertRaises(AssertionError):pilot.environment(Path('/lab'),Path('/raw'),'nc','d',30,1)
 def test_runtime_mode_gate(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   for n in range(1,33):(p/f'node{n}.log').write_text('loop_sleep_ms: 25\nfallback_delay_ms=1500 query_timeout_ms=30000\npilot_connection_reuse=1\n')
   pilot.validate_runtime_logs(p,30,25,True)
   with self.assertRaises(AssertionError):pilot.validate_runtime_logs(p,30,25,False)
 def test_identity(self):
  r={k:'x' for k in ('request_index','key','requester','owner','value_size_bytes','expected_fnv1a64','schedule_sha256')};compare_transport.matched([r],[r])
  with self.assertRaises(AssertionError):compare_transport.matched([r],[dict(r,key='wrong')])
