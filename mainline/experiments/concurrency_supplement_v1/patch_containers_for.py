#!/usr/bin/env python3
"""Make containers_for() resilient to containers vanishing between ps and inspect.

Under the 3-4 way concurrency the specification asks for, another lab's teardown can
remove a container after `docker ps -aq` listed it, so the batched `docker inspect`
exits 1 and finalize() records a bogus finalization failure for a slot whose
measurement was fine. Inspect per container and skip the ones that are already gone.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1])
s = P.read_text()
OLD = """def containers_for(lab,expected=None):
 ids=out(['docker','ps','-aq','--filter','label=app=kathara']).splitlines()
 allc=json.loads(out(['docker','inspect',*ids])) if ids else []
 cs=[c for c in allc if any(m['Source']==str(lab/'shared') for m in c['Mounts'])]"""
NEW = """def containers_for(lab,expected=None):
 ids=out(['docker','ps','-aq','--filter','label=app=kathara']).splitlines()
 allc=[]
 for cid in ids:
  probe=subprocess.run(['docker','inspect',cid],capture_output=True,text=True)
  if probe.returncode==0:allc+=json.loads(probe.stdout)
 cs=[c for c in allc if any(m['Source']==str(lab/'shared') for m in c['Mounts'])]"""
assert s.count(OLD) == 1, s.count(OLD)
P.write_text(s.replace(OLD, NEW, 1))
print("patched containers_for")