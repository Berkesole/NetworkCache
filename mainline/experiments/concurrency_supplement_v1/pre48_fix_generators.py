#!/usr/bin/env python3
"""R4: make the fallback-pilot generator honour the extended pre phase, then regenerate.

`generate_federation_v3_bf1024_h3_fallback_pilot.py` (the generator behind the scenario
the federation slots actually use) delegates to `generate_federation_v3_bf2048_h3_pilot`,
whose validate() still hard-codes the 8 pre / 16 post counts. Those four assertions are
made bin-count aware, committed, and the workloads are regenerated and installed.

Usage: pre48_fix_generators.py <new_root>
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

NEW = Path(sys.argv[1])
SRC = NEW / "source"
GEN = "experiments/content_discovery/delay_find_content/scripts"
WORK = "experiments/content_discovery/delay_find_content/workloads"
SUITE = "federation-v3-bf1024-h3-fallback-pilot"

mod = SRC / GEN / "generate_federation_v3_bf2048_h3_pilot.py"
s = mod.read_text()
pairs = [
    ('        assert len(rows) == 3584',
     '        assert len(rows) == base.PRE_BINS * 128 + base.POST_BINS * 160'),
    ('        assert sum(row["phase"] == "pre" for row in rows) == 1024',
     '        assert sum(row["phase"] == "pre" for row in rows) == base.PRE_BINS * 128'),
    ('        assert sum(row["request_class"] == "remote" for row in rows) == 2048',
     '        assert sum(row["request_class"] == "remote" for row in rows) == base.POST_BINS * 128'),
    ('        assert sum(row["request_class"] == "local_control" for row in rows) == 512',
     '        assert sum(row["request_class"] == "local_control" for row in rows) == base.POST_BINS * 32'),
]
changed = 0
for old, new in pairs:
    if old in s:
        s = s.replace(old, new, 1)
        changed += 1
assert changed == 4 or "base.PRE_BINS" in s, "unexpected generator state"
mod.write_text(s)
print(f"patched {changed} count assertions", flush=True)
subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "add", "experiments"], check=True)
if subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "diff", "--cached", "--quiet"]).returncode != 0:
    subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "commit", "-q", "-m",
                    "R4: fallback-pilot generator follows the configured pre/post bins\n\n"
                    "validate() hard-coded the 8/16-bin counts; it now derives them from the "
                    "configured PRE_BINS/POST_BINS so the 48-bin pre phase validates."], check=True)
print("commit", subprocess.check_output(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "rev-parse", "--short", "HEAD"],
                                        text=True).strip(), flush=True)

env = dict(os.environ, FEDERATION_PRE_BINS="48", FEDERATION_POST_BINS="48")
log = (NEW / "recovery" / "generator-fallback.log").open("w")
proc = subprocess.run(["python3", "generate_federation_v3_bf1024_h3_fallback_pilot.py"],
                      cwd=SRC / GEN, env=env, stdout=log, stderr=subprocess.STDOUT)
log.close()
assert proc.returncode == 0, "generator failed; see recovery/generator-fallback.log"
print("generated the fallback-pilot workloads", flush=True)

for sub in ("mapped", "scenarios", "canonical"):
    src = SRC / WORK / SUITE / sub
    if src.is_dir():
        shutil.copytree(src, NEW / "inputs" / "federation" / sub, dirs_exist_ok=True)

need = (NEW / "inputs/federation/scenarios/"
        "kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot-catalog512-zipf-seed001.yaml")
assert need.is_file(), need
sched = (NEW / "inputs/federation/mapped/"
         "kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot-catalog512-zipf-seed001/schedule.tsv")
rows = [l.split("\t") for l in sched.read_text().splitlines()[1:]]
pre = sum(1 for r in rows if r[1] == "pre")
post = sum(1 for r in rows if r[1] == "post")
print(f"schedule pre={pre} post={post}", flush=True)
assert (pre, post) == (6144, 7680), (pre, post)
print("OK", flush=True)