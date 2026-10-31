#!/usr/bin/env python3
"""R4 follow-up: make the pipeline expect the 48-bin pre phase, then re-freeze.

Two edits beyond the gate tightening are required, because extending the pre phase
changes the expected request counts:
  * matrix.request_count(federation) 11776 -> 13824 (pre 6144 + post 7680);
  * pilot's federation group-size assertion [4096, 7680] -> [6144, 7680].
Both are applied idempotently, committed, and the campaign record is regenerated so its
source hashes match the patched tree (the root is fresh, so re-freezing loses nothing).

Usage: finish_pre48.py <new_root> <old_root> <label>
"""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

NEW, OLD, LABEL = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
SRC = NEW / "source"
D = "experiments/concurrency_slides_v5"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def out(a):
    return subprocess.check_output(a, text=True).strip()


# ---- edits ----
matrix = SRC / D / "matrix.py"
s = matrix.read_text()
if "if c['family']=='federation':return 11776" in s:
    s = s.replace("if c['family']=='federation':return 11776",
                  "if c['family']=='federation':return 13824", 1)
    matrix.write_text(s)
    print(f"[{LABEL}] request_count(federation) -> 13824", flush=True)
assert "if c['family']=='federation':return 13824" in matrix.read_text()

pilot = SRC / D / "pilot.py"
s = pilot.read_text()
if "==[4096,7680]" in s:
    s = s.replace("==[4096,7680]", "==[6144,7680]", 1)
    pilot.write_text(s)
    print(f"[{LABEL}] federation group sizes -> [6144,7680]", flush=True)
assert "==[6144,7680]" in pilot.read_text()
assert "def warmup_gate(ledger,bins=8):" in pilot.read_text()

# ---- verify the regenerated inputs really carry 48 pre bins ----
sched = sorted((NEW / "inputs/federation/mapped").glob("*/schedule.tsv"))[0]
rows = [l.split("\t") for l in sched.read_text().splitlines()[1:]]
pre = sum(1 for r in rows if r[1] == "pre")
post = sum(1 for r in rows if r[1] == "post")
print(f"[{LABEL}] sample schedule pre={pre} post={post}", flush=True)
assert (pre, post) == (6144, 7680), (pre, post)

subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "add", "-A", D], check=True)
if subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "diff", "--cached", "--quiet"]).returncode != 0:
    subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "commit", "-q", "-m",
                    "R4: expect the 48-bin pre phase (federation 13824 requests)\n\n"
                    "request_count(federation) 11776 -> 13824 and the federation group-size "
                    "assertion [4096,7680] -> [6144,7680], matching the regenerated inputs "
                    "(pre 48 bins x 128 requests = 6144)."], check=True)
commit = out(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "rev-parse", "HEAD"])
diff = out(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "show", "HEAD"])
(NEW / "recovery" / "pre48-changes.patch").write_text(diff)
print(f"[{LABEL}] commit {commit[:8]}", flush=True)

# ---- re-freeze ----
sys.path.insert(0, str(SRC / D))
import matrix as matrix_mod  # noqa: E402
import pilot as pilot_mod  # noqa: E402

record = json.loads((NEW / "campaign.json").read_text())
old_record = json.loads((OLD / "campaign.json").read_text())
files = set(out(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC), "ls-files"]).splitlines())
record.update(
    parent_campaign=old_record.get("fingerprint"),
    commit=commit,
    sources={n: sha(SRC / n) for n in sorted(files) if (SRC / n).is_file()},
    inputs={str(p.relative_to(NEW / "inputs")): sha(p)
            for p in sorted((NEW / "inputs").rglob("*")) if p.is_file()},
    matrix=matrix_mod.conditions(),
    assignments={s: matrix_mod.slots(s) for s in ("smoke", "pilot", "formal", "supplement_v2")},
    timeout_policy=pilot_mod.timeout_policy(),
    epoch_ns=time.time_ns(),
    changes_patch_sha256=sha(NEW / "recovery" / "pre48-changes.patch"),
)
record.pop("fingerprint", None)
record["fingerprint"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
(NEW / "campaign.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
(NEW / "recovery" / "complete.json").write_text(json.dumps(
    dict(cleanup_complete=True, reason="R4 pre48 re-freeze complete",
         campaign=record["fingerprint"]), indent=2) + "\n")
print(json.dumps(dict(label=LABEL, campaign=record["fingerprint"], commit=commit,
                      pre_bins=record.get("pre_bins"), gate_bins=record.get("gate_bins"),
                      inputs=len(record["inputs"])), indent=2), flush=True)