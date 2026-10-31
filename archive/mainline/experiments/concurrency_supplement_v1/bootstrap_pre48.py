#!/usr/bin/env python3
"""R4: build the pre-48-bin federation campaign root and freeze it.

Creates an independent root (never touching the existing campaigns), carrying the leaf's
own source copy with the tightened activation gate (the last 8 pre bins per domain
instead of 4), regenerates the federation inputs with 48 pre bins (6144 pre requests),
installs them, and writes campaign.json with pre_bins recorded.

Usage: bootstrap_pre48.py <old_root> <new_root> <label> [--skip-generate]
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
plain = [a for a in argv if not a.startswith("--")]
OLD, NEW, LABEL = Path(plain[0]), Path(plain[1]), plain[2]
SKIP_GEN = "--skip-generate" in argv
SRC_OLD, SRC_NEW = OLD / "source", NEW / "source"
D = "experiments/concurrency_slides_v5"
GEN_DIR = "experiments/content_discovery/delay_find_content/scripts"
WORK = "experiments/content_discovery/delay_find_content/workloads"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def out(a, **kw):
    return subprocess.check_output(a, text=True, **kw).strip()


def dump(p, x):
    Path(p).write_text(json.dumps(x, indent=2, sort_keys=True) + "\n")


assert OLD.is_dir(), OLD
assert not NEW.exists(), f"{NEW} already exists"
NODES = ["/"] if os.getuid() == 0 else ["sudo"]

def run(a, **kw):
    return subprocess.run(NODES + a if os.getuid() else a, check=True, **kw)


NEW.mkdir(parents=True)
(NEW / "recovery").mkdir()
(NEW / "lab").mkdir()
(NEW / "raw").mkdir()
(NEW / "derived").mkdir()

# ---- 1. source + configs ----
subprocess.run(["cp", "-a", str(SRC_OLD), str(SRC_NEW)], check=True)
for name in ("bin", "kathara.conf", "backend.json"):
    src = OLD / name
    if src.is_dir():
        subprocess.run(["cp", "-a", str(src), str(NEW / name)], check=True)
    elif src.is_file():
        shutil.copy2(src, NEW / name)
for key in ("user.name", "user.email"):
    try:
        val = out(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC_OLD), "config", key])
        subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC_NEW), "config", key, val], check=True)
    except subprocess.CalledProcessError:
        pass
print(f"[{LABEL}] source copied", flush=True)

# ---- 2. tighten the activation gate: last 8 pre bins per domain ----
pilot = SRC_NEW / D / "pilot.py"
s = pilot.read_text()
old = "def warmup_gate(ledger,bins=4):"
assert s.count(old) == 1, "gate anchor not found"
pilot.write_text(s.replace(old, "def warmup_gate(ledger,bins=8):", 1))
print(f"[{LABEL}] gate now uses the last 8 pre bins", flush=True)
subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC_NEW), "add", f"{D}/pilot.py"], check=True)
subprocess.run(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC_NEW), "commit", "-q", "-m",
                "R4: federation warm-up gate over the last 8 pre bins\n\n"
                "The activation gate now requires each domain's OLS slope over the final "
                "8 pre bins to have a 95% t CI containing zero (was 4). Paired with the "
                "48-bin pre phase this gives the federation time to reach a plateau, as "
                "the leaf's own measurement showed the 8-bin slope still significantly "
                "negative (ss -1.125 ms/bin [-1.99,-0.26], vc3 -0.947 [-1.64,-0.26])."], check=True)
commit = out(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC_NEW), "rev-parse", "HEAD"])
diff = out(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC_NEW), "show", "HEAD"])
(NEW / "recovery" / "pre48-changes.patch").write_text(diff)
print(f"[{LABEL}] commit {commit[:8]}", flush=True)

# ---- 3. regenerate the federation inputs with 48 pre bins ----
if not SKIP_GEN:
    env = dict(os.environ, FEDERATION_PRE_BINS="48", FEDERATION_POST_BINS="48")
    log = (NEW / "recovery" / "generator-federation-pre48.log").open("w")
    proc = subprocess.run(["python3", "generate_federation_v4_bf1024_h3_formal.py"],
                          cwd=SRC_NEW / GEN_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    log.close()
    assert proc.returncode == 0, "generator failed; see recovery/generator-federation-pre48.log"
print(f"[{LABEL}] federation inputs generated", flush=True)

for sub in ("mapped", "scenarios"):
    src = SRC_NEW / WORK / "federation-v4-bf1024-h3-formal" / sub
    shutil.copytree(src, NEW / "inputs" / "federation" / sub, dirs_exist_ok=True)

# ---- 4. freeze ----
old_record = json.loads((OLD / "campaign.json").read_text())
sys.path.insert(0, str(SRC_NEW / D))
import matrix  # noqa: E402
import pilot as pilot_mod  # noqa: E402

startup = SRC_NEW / "kathara/ruikekkk/shared/start-node.sh"
files = set(out(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC_NEW), "ls-files"]).splitlines())
record = dict(old_record)
record.update(
    parent_campaign=old_record.get("fingerprint"),
    branch=out(["git", "-c", "user.name=dual-track runner", "-c", "user.email=runner@localhost", "-C", str(SRC_NEW), "branch", "--show-current"]),
    commit=commit,
    workers=old_record.get("worker"),
    sources={n: sha(SRC_NEW / n) for n in sorted(files) if (SRC_NEW / n).is_file()},
    inputs={str(p.relative_to(NEW / "inputs")): sha(p)
            for p in sorted((NEW / "inputs").rglob("*")) if p.is_file()},
    matrix=matrix.conditions(),
    assignments={s: matrix.slots(s) for s in ("smoke", "pilot", "formal", "supplement_v2")},
    timeout_policy=pilot_mod.timeout_policy(),
    logging_policy=dict(old_record.get("logging_policy") or {},
                        startup_sha256=sha(startup)),
    evidence="RERUN_SPEC_R4_FEDERATION_PRE48",
    epoch_ns=time.time_ns(),
    pre_bins=48, post_bins=48, pre_requests_per_domain_bin=64,
    gate_bins=8,
    gate_rule=("each domain's OLS slope of local latency over the final 8 pre bins must "
               "have a 95% t CI containing zero; otherwise warmup_gate_failed stops the slot"),
    changes_patch_sha256=sha(NEW / "recovery" / "pre48-changes.patch"),
    scope=("RERUN_SPEC R4: federation pre phase extended 32 -> 48 bins (4096 -> 6144 pre "
           "requests), post unchanged at 48 bins (160 requests/bin), activation gate "
           "tightened to the last 8 pre bins; seeds, arrival, protocol, 30 s query "
           "lifetime and image unchanged"),
)
record.pop("fingerprint", None)
record["fingerprint"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
dump(NEW / "campaign.json", record)
dump(NEW / "recovery" / "complete.json",
     dict(cleanup_complete=True, reason="R4 pre48 freeze complete",
          campaign=record["fingerprint"]))
print(json.dumps(dict(label=LABEL, root=str(NEW), campaign=record["fingerprint"],
                      commit=commit, pre_bins=48, inputs=len(record["inputs"]),
                      v2_slots=len(record["assignments"]["supplement_v2"])), indent=2), flush=True)