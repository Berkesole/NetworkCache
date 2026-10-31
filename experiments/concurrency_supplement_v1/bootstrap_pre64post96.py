#!/usr/bin/env python3
"""R5: build the pre-64/post-96 federation root from the pre-48 root and freeze it.

Cloning the pre-48 root carries over the tightened gate (last 8 pre bins), the
bin-count-parametrised generator assertions, the federation-only stage scope and the
relaxed disk gate. On top, the expected request count moves to 23552 (pre 8192 + post
15360) and the group sizes to [8192, 15360]; the federation inputs are regenerated with
64 pre and 96 post bins, and campaign.json records both bin counts.

Usage: bootstrap_pre64post96.py <base_root> <new_root> <label>
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE, NEW, LABEL = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
SRC = NEW / "source"
BASE_SRC = BASE / "source"
D = "experiments/concurrency_slides_v5"
GEN = "experiments/content_discovery/delay_find_content/scripts"
WORK = "experiments/content_discovery/delay_find_content/workloads"
GIT = ["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def out(a):
    return subprocess.check_output(a, text=True).strip()


assert BASE.is_dir(), BASE
assert not NEW.exists(), f"{NEW} already exists"
NEW.mkdir(parents=True)
for sub in ("recovery", "lab", "raw", "derived"):
    (NEW / sub).mkdir()
subprocess.run(["cp", "-a", str(BASE_SRC), str(SRC)], check=True)
for name in ("bin", "kathara.conf", "backend.json"):
    src = BASE / name
    if src.is_dir():
        subprocess.run(["cp", "-a", str(src), str(NEW / name)], check=True)
    elif src.is_file():
        shutil.copy2(src, NEW / name)
print(f"[{LABEL}] source copied from {BASE.name}", flush=True)

mx = SRC / D / "matrix.py"
s = mx.read_text()
if "return 13824" in s:
    assert s.count("return 13824") == 1
    s = s.replace("return 13824", "return 23552", 1)
    mx.write_text(s)
    print(f"[{LABEL}] request_count(federation) -> 23552", flush=True)
assert "return 23552" in mx.read_text()

pl = SRC / D / "pilot.py"
t = pl.read_text()
if "==[6144,7680]" in t:
    assert t.count("==[6144,7680]") == 1
    pl.write_text(t.replace("==[6144,7680]", "==[8192,15360]", 1))
    print(f"[{LABEL}] federation group sizes -> [8192,15360]", flush=True)
t = pl.read_text()
assert "==[8192,15360]" in t
assert "def warmup_gate(ledger,bins=8):" in t, "gate must already use 8 bins"

subprocess.run(GIT + ["-C", str(SRC), "add", "-A", "experiments"], check=True)
if subprocess.run(GIT + ["-C", str(SRC), "diff", "--cached", "--quiet"]).returncode != 0:
    subprocess.run(GIT + ["-C", str(SRC), "commit", "-q", "-m",
                          "R5: expect the 64-bin pre and 96-bin post phase\n\n"
                          "request_count(federation) 13824 -> 23552 (pre 8192 + post "
                          "15360) and the group-size assertion [6144,7680] -> [8192,15360], "
                          "matching the regenerated inputs."], check=True)
commit = out(GIT + ["-C", str(SRC), "rev-parse", "HEAD"])
print(f"[{LABEL}] commit {commit[:8]}", flush=True)

env = dict(os.environ, FEDERATION_PRE_BINS="64", FEDERATION_POST_BINS="96")
for gen in ("generate_federation_v4_bf1024_h3_formal.py",
            "generate_federation_v3_bf1024_h3_fallback_pilot.py"):
    log = (NEW / "recovery" / f"generator-{gen}.log").open("w")
    r = subprocess.run(["python3", gen], cwd=SRC / GEN, env=env, stdout=log,
                       stderr=subprocess.STDOUT)
    log.close()
    assert r.returncode == 0, f"{gen} failed; see recovery/generator-{gen}.log"
    print(f"[{LABEL}] generated {gen}", flush=True)

for stem in ("federation-v4-bf1024-h3-formal", "federation-v3-bf1024-h3-fallback-pilot",
             "federation-v3-pilot"):
    for sub in ("mapped", "scenarios", "canonical"):
        src = SRC / WORK / stem / sub
        if src.is_dir():
            shutil.copytree(src, NEW / "inputs" / "federation" / sub, dirs_exist_ok=True)
shutil.copytree(BASE / "inputs/federation/scenarios", NEW / "inputs/federation/scenarios",
                dirs_exist_ok=True)

sched = (NEW / "inputs/federation/mapped/"
         "kua-dc-v4-federation-v4-bf1024-h3-formal-catalog512-zipf-seed001/schedule.tsv")
assert sched.is_file(), sched
rows = [l.split("\t") for l in sched.read_text().splitlines()[1:]]
pre = sum(1 for r in rows if r[1] == "pre")
post = sum(1 for r in rows if r[1] == "post")
print(f"[{LABEL}] sample schedule pre={pre} post={post} total={pre + post}", flush=True)
assert (pre, post) == (8192, 15360), (pre, post)

record = json.loads((BASE / "campaign.json").read_text())
record.update(
    parent_campaign=record.get("fingerprint"),
    branch=out(GIT + ["-C", str(SRC), "branch", "--show-current"]),
    commit=commit,
    sources={n: sha(SRC / n) for n in sorted(out(GIT + ["-C", str(SRC), "ls-files"]).splitlines())
             if (SRC / n).is_file()},
    inputs={str(p.relative_to(NEW / "inputs")): sha(p)
            for p in sorted((NEW / "inputs").rglob("*")) if p.is_file()},
    evidence="RERUN_FINAL_R5_FEDERATION_PRE64POST96",
    epoch_ns=time.time_ns(),
    pre_bins=64, post_bins=96, pre_requests=8192, post_requests=15360, total_requests=23552,
    gate_bins=8, gate_bins_range=[57, 64],
    gate_rule=("each domain's OLS slope of local latency over the final 8 pre bins (57-64) "
               "must have a 95% t CI containing zero; otherwise warmup_gate_failed"),
    post_slope_evidence=["post bins 81-96 (last 16)", "post bins 89-96 (last 8)"],
    changes_patch_sha256=None,
)
patch = subprocess.run(GIT + ["-C", str(SRC), "diff", f"{record.get('commit')}^",
                              str(record.get("commit"))], capture_output=True, text=True)
(NEW / "recovery" / "pre64post96-changes.patch").write_text(
    subprocess.run(GIT + ["-C", str(SRC), "show", commit], capture_output=True,
                   text=True).stdout)
record["changes_patch_sha256"] = sha(NEW / "recovery" / "pre64post96-changes.patch")
record.pop("fingerprint", None)
record["fingerprint"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
(NEW / "campaign.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
(NEW / "recovery" / "complete.json").write_text(json.dumps(
    dict(cleanup_complete=True, reason="R5 pre64post96 freeze complete",
         campaign=record["fingerprint"]), indent=2) + "\n")
sys.path.insert(0, str(SRC / D))
import matrix as matrix_mod  # noqa: E402
slots = matrix_mod.slots("supplement_v2")
print(json.dumps(dict(label=LABEL, root=str(NEW), campaign=record["fingerprint"],
                      commit=commit[:8], pre_bins=64, post_bins=96, v2_slots=len(slots),
                      inputs=len(record["inputs"])), indent=2), flush=True)
assert len(slots) == 20, len(slots)