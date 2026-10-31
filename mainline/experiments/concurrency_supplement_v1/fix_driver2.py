#!/usr/bin/env python3
"""Make the untracked driver robust and clear stale lab directories (run on volcengine-3).

1. supplement_queue2.py: a slot-level failure must never kill the worker process.
2. retry_failed.py: when archiving a slot's attempts, also remove its stale lab
   directory. pilot.one() creates lab/.../attempt00N/shared/<ns>/control with
   mkdir() (no exist_ok), so a leftover lab directory from a killed attempt makes a
   re-run of the same attempt number fail immediately with FileExistsError.
3. Clear the stale lab directories that already exist for slots with no live
   (result.json-bearing) attempt.
"""
import glob
import os
import shutil
from pathlib import Path

N = Path("/data/networkcache-concurrency-supplement-v1")
D = N / "source/experiments/concurrency_slides_v5"

p = D / "supplement_queue2.py"
s = p.read_text()
old = """                print(json.dumps(dict(worker=worker, slot=target["id"], phase="start")), flush=True)
                out = run_slot(root, target, worker)
                out["slot_id"] = target["id"]"""
new = """                print(json.dumps(dict(worker=worker, slot=target["id"], phase="start")), flush=True)
                try:
                    out = run_slot(root, target, worker)
                except Exception as exc:  # a slot failure must not kill the worker
                    out = dict(status="driver_exception", qualified=False,
                               reason=f"{type(exc).__name__}: {exc}")
                out["slot_id"] = target["id"]"""
assert s.count(old) == 1, "driver anchor missing"
p.write_text(s.replace(old, new, 1))
print("patched supplement_queue2.py (worker survives slot failures)")

p = N / "recovery/retry_failed.py"
s = p.read_text()
old = """        if derived.exists():
            shutil.rmtree(derived)
        claim = root / "derived" / "supplement-claims" / config["id"]"""
new = """        if derived.exists():
            shutil.rmtree(derived)
        for lab_attempt in (root / "lab" / STAGE / config["id"]).glob("attempt*"):
            shutil.rmtree(lab_attempt, ignore_errors=True)
        claim = root / "derived" / "supplement-claims" / config["id"]"""
assert s.count(old) == 1, "retry anchor missing"
p.write_text(s.replace(old, new, 1))
print("patched retry_failed.py (also clears stale lab dirs)")

removed = []
for lab_attempt in sorted(glob.glob(str(N / "lab" / "supplement" / "*" / "attempt*"))):
    slot = Path(lab_attempt).parent.name
    raw_attempts = sorted(glob.glob(str(N / "raw" / "supplement" / slot / "attempt*")))
    live = [a for a in raw_attempts if os.path.isfile(a + "/result.json")]
    if not live:
        shutil.rmtree(lab_attempt, ignore_errors=True)
        removed.append(slot)
print("cleared stale lab dirs:", len(removed))
for slot in removed[:6]:
    print("   ", slot[:60])