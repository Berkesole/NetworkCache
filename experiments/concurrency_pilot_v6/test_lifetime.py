import json,tempfile,unittest
from pathlib import Path
import pilot,controller,compare_lifetime
class LifetimeTests(unittest.TestCase):
 def test_only_lifetime_environment_varies(self):
  envs=[pilot.environment(Path('/lab'),Path('/raw/config/attempt001'),'nc','digest',ttl) for ttl in (7,15,30)]
  self.assertEqual([e.pop('RSYNC_QUERY_TIMEOUT_MS') for e in envs],['7000','15000','30000'])
  self.assertEqual(envs[0],envs[1]);self.assertEqual(envs[1],envs[2])
  with self.assertRaises(AssertionError):pilot.environment(Path('/lab'),Path('/raw'),'nc','d',60)
 def test_all_runtime_nodes_match(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   for n in range(1,33):(p/f'node{n}.log').write_text('loop_sleep_ms: 25\nfallback_delay_ms=1500 query_timeout_ms=15000\n')
   pilot.validate_runtime_logs(p,15)
   with self.assertRaises(AssertionError):pilot.validate_runtime_logs(p,7)
   (p/'node32.log').unlink()
   with self.assertRaises(FileNotFoundError):pilot.validate_runtime_logs(p,15)
 def test_request_identity_comparison(self):
  r={k:'a' for k in ('request_index','key','requester','owner','value_size_bytes','expected_fnv1a64','schedule_sha256')}
  compare_lifetime.matched([r],[r])
  with self.assertRaises(AssertionError):compare_lifetime.matched([r],[dict(r,key='wrong')])
 def test_no_ttl_condition_skipped_by_previous_timeout(self):
  for config in pilot.matrix():self.assertFalse(pilot.can_skip(config,{('nc',config['model']):128}))
 def test_runtime_policy_not_controller_timeout(self):
  self.assertIsNone(pilot.timeout_policy()['started_request_abort_seconds'])
  self.assertEqual(pilot.timeout_policy()['watchdog_seconds_after_last_arrival'],60)
  for model in ('poisson','burst'):
   schedule=controller.offsets(4096,model,128)
   self.assertTrue(all(controller.offsets(4096,c['model'],c['rate'])==schedule for c in pilot.matrix() if c['model']==model))
if __name__=='__main__':unittest.main()
