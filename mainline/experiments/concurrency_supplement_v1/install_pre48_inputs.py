#!/usr/bin/env python3
"""R4 inputs: regenerate the federation-v3-pilot workloads with 48 pre bins and install.

The federation conditions' lab scenario comes from the v3-pilot generator
(kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot-catalog512-zipf-seedNNN), so that
lineage - not only the v4-formal one - must carry the extended pre phase in the new root.

Usage: install_pre48_inputs.py <new_root>
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

NEW = Path(sys.argv[1])
SRC = NEW / "source"
GEN = "experiments/content_discovery/delay_find_content/scripts"
WORK = "experiments/content_discovery/delay_find_content/workloads"

for gen in ("generate_federation_v3_pilot.py", "generate_federation_v4_bf1024_h3_formal.py"):
    env = dict(os.environ, FEDERATION_PRE_BINS="48", FEDERATION_POST_BINS="48")
    log = (NEW / "recovery" / f"generator-{gen}.log").open("w")
    proc = subprocess.run(["python3", gen], cwd=SRC / GEN, env=env,
                          stdout=log, stderr=subprocess.STDOUT)
    log.close()
    assert proc.returncode == 0, f"{gen} failed; see recovery/generator-{gen}.log"
    print(f"generated {gen}", flush=True)

for stem in ("federation-v3-pilot", "federation-v4-bf1024-h3-formal"):
    for sub in ("mapped", "scenarios", "canonical"):
        src = SRC / WORK / stem / sub
        if src.is_dir():
            shutil.copytree(src, NEW / "inputs" / "federation" / sub, dirs_exist_ok=True)

mapped = sorted((NEW / "inputs/federation/mapped").glob("*/schedule.tsv"))
print("mapped schedules:", len(mapped), flush=True)
for p in mapped:
    rows = [l.split("\t") for l in p.read_text().splitlines()[1:]]
    pre = sum(1 for r in rows if r[1] == "pre")
    post = sum(1 for r in rows if r[1] == "post")
    assert pre == 6144 and post == 7680, (p.name, pre, post)
target = (NEW / "inputs/federation/scenarios/"
          "kua-dc-v3-federation-v3-bf1024-h3-fallback-pilot-catalog512-zipf-seed001.yaml")
assert target.is_file(), target
print("scenario present:", target.name, flush=True)
print(json.dumps(dict(mapped=len(mapped), pre=6144, post=7680), indent=2), flush=True)