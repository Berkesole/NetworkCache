"""Passive diagnostic sampler; never changes a socket or arrival clock."""
import json,subprocess,threading,time
import controller
class Observer:
 def __init__(self,cs,raw):
  self.cs=cs;self.raw=raw;self.stop=threading.Event();self.errors=[]
  self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def snapshot(self,all_nodes=False):
  records=[]
  for c in self.cs:
   name=c['Config']['Labels']['name'];pid=str(c['State']['Pid'])
   if not all_nodes and name not in ('node1','node5','node9','node13'):continue
   commands=[['ss','-tinm']] if not all_nodes else [['tc','-s','-j','qdisc'],['ip','-s','-j','link'],['ss','-tinm']]
   for command in commands:
    p=subprocess.run(['nsenter','-t',pid,'-n','--',*command],capture_output=True,text=True,timeout=3)
    records.append(dict(node=name,mono_ns=time.monotonic_ns(),command=command,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr))
  return records
 def run(self):
  try:
   with (self.raw/'passive_network.jsonl').open('w') as h:
    first=True
    while not self.stop.is_set():
     for x in self.snapshot(first):h.write(json.dumps(x)+'\n')
     first=False;h.flush();self.stop.wait(1)
    for x in self.snapshot(True):h.write(json.dumps(x)+'\n')
  except Exception as e:self.errors.append(repr(e))
 def finish(self):
  self.stop.set();self.thread.join(timeout=180)
  if self.thread.is_alive():raise RuntimeError('observer failed to stop')
  controller.dump(self.raw/'observer_status.json',dict(errors=self.errors,mode='passive kernel snapshots; no change to arrival clock or socket options'))
