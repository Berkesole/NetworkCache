import json,tempfile,unittest
from pathlib import Path
import pilot,controller,compare_polling
class PollingTests(unittest.TestCase):
 def test_only_polling_environment_varies(self):
  envs=[pilot.environment(Path('/lab'),Path('/raw/config/attempt001'),'nc','digest',30,poll) for poll in (25,5,1)]
  self.assertEqual([e.pop('KATHARA_NODE_LOOP_SLEEP_MS') for e in envs],['25','5','1'])
  self.assertEqual(envs[0],envs[1]);self.assertEqual(envs[1],envs[2])
  with self.assertRaises(AssertionError):pilot.environment(Path('/lab'),Path('/raw'),'nc','d',30,0)
  with self.assertRaises(AssertionError):pilot.environment(Path('/lab'),Path('/raw'),'nc','d',7,25)
 def test_all_runtime_nodes_match(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   for n in range(1,33):(p/f'node{n}.log').write_text('loop_sleep_ms: 5\nfallback_delay_ms=1500 query_timeout_ms=30000\n')
   pilot.validate_runtime_logs(p,30,5)
   with self.assertRaises(AssertionError):pilot.validate_runtime_logs(p,30,25)
   with self.assertRaises(AssertionError):pilot.validate_runtime_logs(p,7,5)
   (p/'node32.log').unlink()
   with self.assertRaises(FileNotFoundError):pilot.validate_runtime_logs(p,30,5)
 def test_request_identity_comparison(self):
  r={k:'a' for k in ('request_index','key','requester','owner','value_size_bytes','expected_fnv1a64','schedule_sha256')}
  compare_polling.matched([r],[r])
  with self.assertRaises(AssertionError):compare_polling.matched([r],[dict(r,key='wrong')])
 def test_no_ttl_condition_skipped_by_previous_timeout(self):
  for config in pilot.matrix():self.assertFalse(pilot.can_skip(config,{('nc',config['model']):128}))
 def test_runtime_policy_not_controller_timeout(self):
  self.assertIsNone(pilot.timeout_policy()['started_request_abort_seconds'])
  self.assertEqual(pilot.timeout_policy()['watchdog_seconds_after_last_arrival'],60)
  for model in ('poisson','burst'):
   schedule=controller.offsets(4096,model,128)
   self.assertTrue(all(controller.offsets(4096,c['model'],c['rate'])==schedule for c in pilot.matrix() if c['model']==model))
if __name__=='__main__':unittest.main()
