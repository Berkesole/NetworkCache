"""Verify actual launch_app function preserves output and supervises real child."""
import os,pathlib,shlex,subprocess,tempfile,unittest
HERE=pathlib.Path(__file__).resolve().parent
class LoggingTests(unittest.TestCase):
 def test_no_per_line_pipe_and_preserves_all_bytes(self):
  source=(HERE/'start-node.sh').read_text()
  body='launch_app() {'+source.split('launch_app() {',1)[1].split('\n}\n',1)[0]+'\n}\n'
  self.assertNotIn('while IFS=',body);self.assertNotIn('$(date',body)
  self.assertIn('nohup /app/build/rsync-node-dev',body)
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);fake=root/'fake_node';log=root/'app.log';pid=root/'app.pid'
   payload=''.join(f'line={n} key=中文\t100%\\value\n' for n in range(10000)).encode()
   expected=root/'payload';expected.write_bytes(payload)
   fake.write_text('#!/usr/bin/env python3\nimport os,time,pathlib\np=pathlib.Path('+repr(str(expected))+').read_bytes()\nfor i in range(0,len(p),2048):os.write(1,p[i:i+2048])\nos.write(2,b"stderr-marker\\n")\ntime.sleep(2)\n')
   fake.chmod(0o755)
   body=body.replace('/app/build/rsync-node-dev',shlex.quote(str(fake)))
   script='set -eu\nnode_id=33\nnode_name=node33\napp_log='+shlex.quote(str(log))+'\npid_file='+shlex.quote(str(pid))+'\nlog_startup() { :; }\n'+body+'launch_app\nkill -0 "$app_pid"\nwait "$app_pid"\n'
   subprocess.run(['sh','-c',script],env=dict(os.environ,RSYNC_EVENT_TRACE='0'),check=True,timeout=8,capture_output=True)
   self.assertEqual(log.read_bytes(),payload+b'stderr-marker\n')
   self.assertTrue(pid.read_text().strip().isdigit())
 def test_shell_syntax(self):
  subprocess.run(['sh','-n',str(HERE/'start-node.sh')],check=True,capture_output=True)
if __name__=='__main__':unittest.main()
