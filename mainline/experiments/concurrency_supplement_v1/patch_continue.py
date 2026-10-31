"""Untracked driver: record a failed slot and keep the worker going."""
from pathlib import Path

D = Path("/data/networkcache-concurrency-supplement-v1/source/experiments/concurrency_slides_v5")
p = D / "supplement_queue2.py"
s = p.read_text()
old = """                ran += 1
                if not out.get("qualified") and out["status"] not in ("warmup_gate_failed",):
                    return dict(worker=worker, stopped_on=target["id"], results=results)"""
new = """                ran += 1
                # keep the worker alive on any unqualified outcome: the failure is
                # recorded per slot and the retry pass re-runs it serially, whereas
                # stopping the worker permanently lowers the campaign concurrency."""
s = s.replace(old, new, 1)
assert "keep the worker alive" in s, "anchor missing"
p.write_text(s)
print("driver now records failures and continues")
