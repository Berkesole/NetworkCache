#!/usr/bin/env python3
"""Teardown and lifetime-validation fixes for the supplement campaign.

1. stop() must not abort finalization when stopping ~60 containers takes longer than
   60 s under 4-way concurrency: stop without check, with a generous timeout, then
   always write containers.after.json (validate requires it) from whatever remains.
2. reports.validate hardcoded RSYNC_QUERY_TIMEOUT_MS=30000, which rejected the E
   family's legitimate per-slot 7 s override; expect the slot's own lifetime.
3. supplement_queue.summarize reported workers_max as the number of merged results.
"""
import sys
from pathlib import Path

SRC = Path(sys.argv[1])


def rep(path, old, new, count=1):
    p = SRC / path
    s = p.read_text()
    got = s.count(old)
    assert got == count, f"{path}: anchor x{got} != {count}: {old[:70]!r}"
    p.write_text(s.replace(old, new, count))
    print("patched", path)


rep("pilot.py",
    """   current=containers_for(lab)
   if current:
    subprocess.run(['docker','stop','--time','5',*[c['Id'] for c in current]],capture_output=True,check=True,timeout=60)
    after=json.loads(out(['docker','inspect',*[c['Id'] for c in current]]));ctl.dump(raw/'containers.after.json',after)
    if any(c['State'].get('OOMKilled') for c in after):raise ctl.ProcessFailure('container OOM killed')""",
    """   current=containers_for(lab)
   if current:
    subprocess.run(['docker','stop','--time','5',*[c['Id'] for c in current]],capture_output=True,check=False,timeout=600)
   remaining=containers_for(lab)
   after=json.loads(out(['docker','inspect',*[c['Id'] for c in remaining]])) if remaining else []
   ctl.dump(raw/'containers.after.json',after)
   if any(c['State'].get('OOMKilled') for c in after):raise ctl.ProcessFailure('container OOM killed')""")

rep("reports.py",
    "for k,v in dict(RSYNC_QUERY_TIMEOUT_MS='30000',RSYNC_FALLBACK_DELAY_MS='1500',",
    "for k,v in dict(RSYNC_QUERY_TIMEOUT_MS=str(c['query_lifetime_seconds']*1000),RSYNC_FALLBACK_DELAY_MS='1500',")

rep("supplement_queue.py",
    "failed=failed, host_limited=limited, workers_max=len(all_results),",
    "failed=failed, host_limited=limited, workers_max=max(1, len(proc)) if proc else 0,")

# summarize() has no access to proc; pass the worker count in explicitly
p = SRC / "supplement_queue.py"
s = p.read_text()
s = s.replace("def summarize(root, all_results):", "def summarize(root, all_results, workers=0):")
s = s.replace("failed=failed, host_limited=limited, workers_max=max(1, len(proc)) if proc else 0,",
              "failed=failed, host_limited=limited, workers_max=workers,")
s = s.replace("summary = summarize(root, all_results)", "summary = summarize(root, all_results, args.workers)")
p.write_text(s)
print("patched summarize signature")
print("done")