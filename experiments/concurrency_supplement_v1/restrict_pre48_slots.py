#!/usr/bin/env python3
"""R4: restrict the campaign's supplement_v2 stage to the 20 federation slots.

The R4 root measures only the federation arm, and it must go through the queue driver
(which writes the derived per-slot artifacts and progress records the extractor reads),
not a bespoke runner. Narrowing slots('supplement_v2') is recorded in the campaign, and
the root is fresh so re-freezing is free.

Usage: restrict_pre48_slots.py <new_root> <old_root> <label>
"""
import subprocess
import sys
from pathlib import Path

NEW, OLD, LABEL = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
SRC = NEW / "source"
D = "experiments/concurrency_slides_v5"

matrix = SRC / D / "matrix.py"
s = matrix.read_text()
anchor = "  assert len(out)==180 and len({c['id'] for c in out})==180\n"
assert s.count(anchor) == 1, "identity-assert anchor not found"
if "out=[c for c in out if c['family']=='federation']" not in s:
    s = s.replace(anchor, anchor + "  out=[c for c in out if c['family']=='federation']"
                                      "  # R4 scope: federation arm only\n", 1)
    matrix.write_text(s)
    print(f"[{LABEL}] supplement_v2 restricted to the federation arm", flush=True)
assert "out=[c for c in out if c['family']=='federation']" in matrix.read_text()
subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "add", f"{D}/matrix.py"], check=True)
if subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "diff", "--cached", "--quiet"]).returncode != 0:
    subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "commit", "-q", "-m",
                    "R4: restrict supplement_v2 to the federation arm\n\n"
                    "This root measures the pre-48-bin federation re-run only; the queue "
                    "driver therefore schedules just the 20 federation slots."], check=True)
print("commit", subprocess.check_output(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "rev-parse", "--short", "HEAD"],
                                        text=True).strip(), flush=True)
subprocess.run(["python3", str(NEW / "recovery" / "finish_pre48.py"), str(NEW), str(OLD), LABEL],
               check=True)
sys.path.insert(0, str(SRC / D))
import matrix as m  # noqa: E402
slots = m.slots("supplement_v2")
print(f"[{LABEL}] supplement_v2 slots: {len(slots)} e.g. {slots[0]['id']}", flush=True)
assert len(slots) == 20, len(slots)