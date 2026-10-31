#!/usr/bin/env python3
"""Complete and freeze the pre-48-bin root on the volcengine-3 tree.

The bootstrap stopped before its initial freeze, and this tree spells the stage branch
differently from the simple-station copy, so the edits are applied here in their own
form: federation request_count 11776 -> 13824, the pilot group-size assertion
[4096,7680] -> [6144,7680], and the supplement_v2 stage restricted to the federation arm.
The federation workloads of all three lineages are installed, a sample schedule is
checked (pre 6144 / post 7680), the change is committed, and campaign.json is written.

Usage: complete_pre48_vc3.py <new_root> <old_root> <label>
"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

NEW, OLD, LABEL = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
SRC = NEW / "source"
D = "experiments/concurrency_slides_v5"
WORK = "experiments/content_discovery/delay_find_content/workloads"
GIT = ["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def out(a):
    return subprocess.check_output(a, text=True).strip()


mx = SRC / D / "matrix.py"
s = mx.read_text()
if "        return 11776" in s:
    assert s.count("        return 11776") == 1
    s = s.replace("        return 11776", "        return 13824", 1)
    print(f"[{LABEL}] request_count(federation) -> 13824", flush=True)
anchor = ("        assert len({(s['condition'], s['seed']) for s in out}) == 180\n"
          "        return out")
if "out = [c for c in out if c['family'] == 'federation']" not in s:
    assert s.count(anchor) == 1, "stage tail anchor not found"
    s = s.replace(anchor, anchor.replace(
        "        return out",
        "        out = [c for c in out if c['family'] == 'federation']  # R4 scope\n"
        "        return out"), 1)
    print(f"[{LABEL}] supplement_v2 restricted to the federation arm", flush=True)
mx.write_text(s)
assert "return 13824" in mx.read_text()

pl = SRC / D / "pilot.py"
t = pl.read_text()
if "==[4096,7680]" in t:
    assert t.count("==[4096,7680]") == 1
    t = t.replace("==[4096,7680]", "==[6144,7680]", 1)
    pl.write_text(t)
    print(f"[{LABEL}] federation group sizes -> [6144,7680]", flush=True)
t = pl.read_text()
assert "==[6144,7680]" in t
assert "def warmup_gate(ledger,bins=8):" in t, "gate not yet tightened"

for stem in ("federation-v4-bf1024-h3-formal", "federation-v3-bf1024-h3-fallback-pilot",
             "federation-v3-pilot"):
    for sub in ("mapped", "scenarios", "canonical"):
        src = SRC / WORK / stem / sub
        if src.is_dir():
            shutil.copytree(src, NEW / "inputs" / "federation" / sub, dirs_exist_ok=True)
shutil.copytree(OLD / "inputs/federation/scenarios", NEW / "inputs/federation/scenarios",
                dirs_exist_ok=True)

sample = sorted((NEW / "inputs/federation/mapped").glob("*/schedule.tsv"))[0]
rows = [l.split("\t") for l in sample.read_text().splitlines()[1:]]
pre = sum(1 for r in rows if r[1] == "pre")
post = sum(1 for r in rows if r[1] == "post")
print(f"[{LABEL}] sample {sample.parent.name[-40:]} pre={pre} post={post}", flush=True)
assert (pre, post) == (6144, 7680), (pre, post)

subprocess.run(GIT + ["-C", str(SRC), "add", "-A", "experiments"], check=True)
if subprocess.run(GIT + ["-C", str(SRC), "diff", "--cached", "--quiet"]).returncode != 0:
    subprocess.run(GIT + ["-C", str(SRC), "commit", "-q", "-m",
                          "R4: expect the 48-bin pre phase and scope the stage to the "
                          "federation arm\n\nrequest_count(federation) 11776 -> 13824, "
                          "the group-size assertion [4096,7680] -> [6144,7680], and "
                          "slots('supplement_v2') restricted to the federation conditions "
                          "so this root measures only the R4 arm."], check=True)
commit = out(GIT + ["-C", str(SRC), "rev-parse", "HEAD"])
(NEW / "recovery" / "pre48-changes.patch").write_text(out(GIT + ["-C", str(SRC), "show", "HEAD"]))
print(f"[{LABEL}] commit {commit[:8]}", flush=True)

sys.path.insert(0, str(SRC / D))
import matrix as matrix_mod  # noqa: E402
import pilot as pilot_mod  # noqa: E402

record = json.loads((OLD / "campaign.json").read_text())
record.update(
    parent_campaign=record.get("fingerprint"),
    branch=out(GIT + ["-C", str(SRC), "branch", "--show-current"]),
    commit=commit,
    sources={n: sha(SRC / n) for n in sorted(out(GIT + ["-C", str(SRC), "ls-files"]).splitlines())
             if (SRC / n).is_file()},
    inputs={str(p.relative_to(NEW / "inputs")): sha(p)
            for p in sorted((NEW / "inputs").rglob("*")) if p.is_file()},
    matrix=matrix_mod.conditions(),
    assignments={st: matrix_mod.slots(st) for st in ("smoke", "pilot", "formal", "supplement_v2")},
    timeout_policy=pilot_mod.timeout_policy(),
    evidence="RERUN_SPEC_R4_FEDERATION_PRE48",
    epoch_ns=time.time_ns(),
    pre_bins=48, post_bins=48, pre_requests_per_domain_bin=64, gate_bins=8,
    gate_rule=("each domain's OLS slope of local latency over the final 8 pre bins must "
               "have a 95% t CI containing zero; otherwise warmup_gate_failed stops the slot"),
    changes_patch_sha256=sha(NEW / "recovery" / "pre48-changes.patch"),
    scope=("RERUN_SPEC R4: federation pre 32 -> 48 bins (4096 -> 6144 pre requests), post "
           "48 bins (160 requests/bin), gate tightened to the final 8 bins, stage scoped to "
           "the 20 federation slots"),
)
record.pop("fingerprint", None)
record["fingerprint"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
(NEW / "campaign.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
(NEW / "recovery" / "complete.json").write_text(json.dumps(
    dict(cleanup_complete=True, reason="R4 pre48 freeze complete (vc3)",
         campaign=record["fingerprint"]), indent=2) + "\n")
slots = matrix_mod.slots("supplement_v2")
print(json.dumps(dict(label=LABEL, campaign=record["fingerprint"], commit=commit[:8],
                      pre_bins=48, gate_bins=8, v2_slots=len(slots),
                      inputs=len(record["inputs"])), indent=2), flush=True)
assert len(slots) == 20, len(slots)