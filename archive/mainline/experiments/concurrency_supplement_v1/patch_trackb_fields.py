#!/usr/bin/env python3
"""Track B: record slots_in_parallel per slot and build the v2 (<=2) driver.

1. pilot.py records the runtime concurrency the worker observed for this slot
   (RSYNC_SLOTS_IN_PARALLEL), so it lands in result.json/summary.json.
   offered_mib_per_s is derived from the frozen config by the extractor and needs no
   pilot change.
2. supplement_queue_v2.py is the <=2 slot driver: it keeps a per-slot "running"
   marker so every worker can count the slots actually in flight, exports that count
   for pilot.one(), and never exceeds the configured worker count.
"""
import shutil
import sys
from pathlib import Path

N = Path("/data/networkcache-concurrency-supplement-v1")
D = N / "source/experiments/concurrency_slides_v5"

# ---- 1. pilot.py records slots_in_parallel ----
p = D / "pilot.py"
s = p.read_text()
old = "  result.update(config=config,started_mono_ns=begin,measurement_finished_mono_ns=time.monotonic_ns(),timing=timing,requests_issued=any((control/'commands').glob('node*/*.tsv')))"
assert s.count(old) == 1, "pilot anchor missing"
new = (old + "\n  result['slots_in_parallel']=int(os.environ.get('RSYNC_SLOTS_IN_PARALLEL','0')) or None")
s = s.replace(old, new, 1)
assert ",os," in s, "pilot must import os"
old_unit = "UNIT=\'networkcache-concurrency-supplement-v1\'"
if old_unit in s:
    # the same branch must serve the v2 root, so derive the unit from the root name
    s = s.replace(old_unit, "UNIT=ROOT.name", 1)
    print("pilot.py: UNIT now derives from the root name")
p.write_text(s)
print("patched pilot.py (records slots_in_parallel)")

# ---- 2. the v2 driver ----
src = D / "supplement_queue2.py"
dst = D / "supplement_queue_v2.py"
shutil.copy2(src, dst)
s = dst.read_text()

old = "STAGE = \"supplement\""
new = "STAGE = \"supplement_v2\""
assert s.count(old) == 1
s = s.replace(old, new, 1)

old = "HOST_LIMITED_PERCENT = 90"
new = '''HOST_LIMITED_PERCENT = 90


def running_markers(root):
    return sorted((root / "derived" / "supplement-running").glob("*.json"))


def mark_running(root, slot_id):
    d = root / "derived" / "supplement-running"
    d.mkdir(parents=True, exist_ok=True)
    payload = d / f"{slot_id}.json"
    payload.write_text(json.dumps(dict(slot=slot_id, pid=os.getpid(),
                                       epoch_ns=time.time_ns())) + "\\n")
    return payload


def unmark_running(payload):
    if payload is not None:
        payload.unlink(missing_ok=True)'''
assert s.count(old) == 1
s = s.replace(old, new, 1)

old = '''                try:
                    out = run_slot(root, target, worker)'''
new = '''                marker = mark_running(root, target["id"])
                os.environ["RSYNC_SLOTS_IN_PARALLEL"] = str(len(running_markers(root)))
                try:
                    out = run_slot(root, target, worker)'''
assert s.count(old) == 1
s = s.replace(old, new, 1)

old = '''                out["slot_id"] = target["id"]'''
new = '''                out["slot_id"] = target["id"]
                out["slots_in_parallel"] = os.environ.get("RSYNC_SLOTS_IN_PARALLEL")
                unmark_running(marker)'''
assert s.count(old) == 1
s = s.replace(old, new, 1)
dst.write_text(s)
print("created supplement_queue_v2.py")
for f in (D / "pilot.py", dst):
    compile(f.read_text(), str(f), "exec")
print("syntax ok")