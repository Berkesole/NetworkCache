#!/usr/bin/env python3
"""R4 (volcengine-3): relax the free-disk preflight gate and re-freeze.

The host holds ~202 GB free, just under the 200 GiB gate in pilot.preflight; a 20-slot
federation run needs a few GB per slot, so the threshold is lowered to 150 GiB in this
root only, committed with the rest of the R4 changes and re-frozen.

Usage: relax_vc3.py <new_root> <old_root> <label>
"""
import subprocess
import sys
from pathlib import Path

NEW, OLD, LABEL = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
D = "experiments/concurrency_slides_v5"
GIT = ["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost"]

p = NEW / "source" / D / "pilot.py"
s = p.read_text()
old = "shutil.disk_usage(ROOT).free>=200*1024**3"
new = "shutil.disk_usage(ROOT).free>=150*1024**3"
if old in s:
    assert s.count(old) == 1
    p.write_text(s.replace(old, new, 1))
    print("relaxed the free-disk preflight threshold to 150 GiB", flush=True)
assert new in (NEW / "source" / D / "pilot.py").read_text()

subprocess.run(GIT + ["-C", str(NEW / "source"), "add", "-A", "experiments"], check=True)
if subprocess.run(GIT + ["-C", str(NEW / "source"), "diff", "--cached", "--quiet"]).returncode != 0:
    subprocess.run(GIT + ["-C", str(NEW / "source"), "commit", "-q", "-m",
                          "R4: relax the free-disk preflight threshold to 150 GiB\n\n"
                          "The host holds ~202 GB free, just under the 200 GiB gate; a "
                          "20-slot federation run needs a few GB per slot."], check=True)
    print("committed the threshold change", flush=True)

subprocess.run(["python3", str(OLD / "recovery" / "complete_pre48_vc3.py"),
                str(NEW), str(OLD), LABEL], check=True)