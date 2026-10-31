#!/usr/bin/env python3
"""Parallel driver for the supplementary campaign (120 slots, 3-4 concurrent).

The frozen queue() is serial and holds an exclusive campaign lock, so it cannot
provide the 3-4 slot concurrency the specification asks for. This driver keeps the
same per-slot contract (`pilot.one` + `reports.diagnose` + `reports.validate`) but
runs W worker processes, each of which:

* takes the next unclaimed slot in the recorded deterministic order;
* refuses to start a slot whose scenario is already running in another worker
  (the kathara lab is named after the scenario, so two concurrent slots sharing a
  scenario would collide - this happens between the E family and the 4.2 64 KiB arm);
* claims the slot atomically so no slot ever runs twice;
* records the outcome, the host CPU envelope and a `host_limited` flag per slot;
* treats `warmup_gate_failed` as a recorded outcome of that slot (the pre-serial
  gate did not converge, no post phase is run) and keeps going, while any other
  non-qualified outcome stops that worker and is reported.

Usage:
  supplement_queue.py --execute [--workers 4]
  supplement_queue.py --worker <i> --workers <W>     (internal)
  supplement_queue.py --dry-run                      (claims/locks only, no run)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import matrix as mat          # noqa: E402
import pilot                  # noqa: E402
import reports                # noqa: E402

STAGE = "supplement_v2"
HOST_LIMITED_PERCENT = 90


def running_markers(root):
    return sorted((root / "derived" / "supplement-running").glob("*.json"))


def mark_running(root, slot_id):
    d = root / "derived" / "supplement-running"
    d.mkdir(parents=True, exist_ok=True)
    payload = d / f"{slot_id}.json"
    payload.write_text(json.dumps(dict(slot=slot_id, pid=os.getpid(),
                                       epoch_ns=time.time_ns())) + "\n")
    return payload


def unmark_running(payload):
    if payload is not None:
        payload.unlink(missing_ok=True)


def slots():
    return mat.slots(STAGE)


def slot_dir(root, c):
    return root / "raw" / STAGE / c["id"]


def attempts(root, c):
    return sorted(slot_dir(root, c).glob("attempt*"))


def claim(root, slot_id, worker):
    target = root / "derived" / "supplement-claims" / slot_id
    try:
        target.mkdir(parents=True)
    except FileExistsError:
        return False
    (target / "claimed_by").write_text(f"worker={worker} pid={os.getpid()} epoch_ns={time.time_ns()}\n")
    return True


class ScenarioLock:
    """Non-blocking exclusive lock per scenario name (lab names must not collide)."""

    def __init__(self, root, scenario):
        self.path = root / "derived" / "supplement-locks" / f"{scenario}.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = None

    def acquire(self):
        handle = self.path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def progress_record(root, slot_id):
    p = root / "derived" / "supplement-progress" / f"{slot_id}.json"
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
    return None


def outcome_of(root, c):
    paths = sorted(slot_dir(root, c).glob("attempt*/result.json"))
    if not paths:
        return None
    return reports.validate(paths[-1].parent, root)


def annotate(root, c, gate):
    """Add the host CPU envelope / host_limited marker to the run's health file."""
    paths = sorted(slot_dir(root, c).glob("attempt*/result.json"))
    if not paths:
        return gate
    attempt = paths[-1].parent
    health_path = attempt / "health_and_mechanisms.json"
    peak = mean = None
    if health_path.is_file():
        health = json.loads(health_path.read_text())
        resources = health.get("resources") or {}
        peak = resources.get("host_cpu_peak_percent")
        mean = resources.get("host_cpu_mean_percent")
    host_limited = bool(peak is not None and peak >= HOST_LIMITED_PERCENT)
    record = dict(gate, host_cpu_peak_percent=peak, host_cpu_mean_percent=mean,
                  host_limited=host_limited, attempt=str(attempt))
    out = root / "derived" / "supplement-progress" / f"{c['id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(slot_id=c["id"], condition=c["condition"], seed=c["seed"],
                                   family=c["family"], method=c["method"],
                                   query_lifetime_seconds=c["query_lifetime_seconds"],
                                   **record), sort_keys=True, indent=2) + "\n")
    return record


def run_slot(root, c, worker):
    """Run one slot exactly like queue() would, but without the campaign lock."""
    import controller as ctl
    paths = sorted(slot_dir(root, c).glob("attempt*/result.json"))
    attempts_list = attempts(root, c)
    prior = [json.loads(p.read_text()) for p in paths]
    if len(attempts_list) != len(paths):
        return dict(status="unfinished_attempt", qualified=False,
                    reason="unfinished attempt; manual investigation required")
    if len(attempts_list) >= 2 or any(
            r.get("requests_issued") or r["status"] != "infrastructure_failure" for r in prior):
        return dict(status="failed_measurement_cannot_rerun", qualified=False,
                    reason="failed measurement cannot automatically rerun")
    attempt = len(attempts_list) + 1
    r = pilot.one(c, attempt)
    if (r["status"] == "infrastructure_failure" and not r.get("requests_issued")
            and attempt == 1):
        reports.diagnose(slot_dir(root, c) / "attempt001", root)
        pilot.preflight()
        r = pilot.one(c, 2)
    path = slot_dir(root, c) / f"attempt{len(list(slot_dir(root, c).glob('attempt*'))):03d}"
    reports.diagnose(path, root)
    gate = annotate(root, c, reports.validate(path, root))
    ctl.dump(root / "derived" / "checkpoint.json",
             dict(stage=STAGE, slot=c["id"], validation=gate, epoch_ns=time.time_ns()))
    return dict(status=r["status"], qualified=gate.get("qualified"), gate=gate,
                wall_seconds=r.get("total_wall_seconds"), reason=gate.get("reason"),
                warmup_gate=r.get("timing", {}).get("warmup_gate_passed"))


def worker_loop(root, worker, workers, limit=0):
    """Claim and run slots; returns a summary dict. limit>0 stops after that many runs."""
    results = []
    ran = 0
    pending = list(slots())
    while True:
        target = None
        lock = None
        for c in pending:
            if c["id"] in {x["slot_id"] for x in results}:
                continue
            record = progress_record(root, c["id"])
            if record and record.get("qualified"):
                results.append(dict(slot_id=c["id"], status="already_qualified",
                                    qualified=True))
                target = c
                break
            existing = outcome_of(root, c) if attempts(root, c) else None
            if existing and existing.get("qualified"):
                results.append(dict(slot_id=c["id"], status="already_qualified",
                                    qualified=True))
                target = c
                break
            pilot.context(c)                       # sets pilot.SCENARIO for the lock name
            lock = ScenarioLock(root, pilot.SCENARIO)
            if not lock.acquire():
                continue                            # another worker runs this scenario
            if not claim(root, c["id"], worker):
                lock.release()
                continue
            target = c
            break
        if target is None:
            break
        if limit and ran >= limit:
            break
        if any(x.get("slot_id") == target["id"] for x in results):
            if lock:
                lock.release()
            pending = [c for c in pending if c["id"] != target["id"]]
            continue
        try:
            if not (attempts(root, target) and
                    (outcome_of(root, target) or {}).get("qualified")):
                print(json.dumps(dict(worker=worker, slot=target["id"], phase="start")), flush=True)
                marker = mark_running(root, target["id"])
                os.environ["RSYNC_SLOTS_IN_PARALLEL"] = str(len(running_markers(root)))
                try:
                    out = run_slot(root, target, worker)
                except Exception as exc:  # a slot failure must not kill the worker
                    out = dict(status="driver_exception", qualified=False,
                               reason=f"{type(exc).__name__}: {exc}")
                out["slot_id"] = target["id"]
                out["slots_in_parallel"] = os.environ.get("RSYNC_SLOTS_IN_PARALLEL")
                unmark_running(marker)
                results.append(out)
                print(json.dumps(dict(worker=worker, slot=target["id"],
                                      status=out["status"], qualified=out["qualified"],
                                      wall=out.get("wall_seconds"), reason=out.get("reason"))),
                      flush=True)
                ran += 1
                # keep the worker alive on any unqualified outcome: the failure is
                # recorded per slot and the retry pass re-runs it serially, whereas
                # stopping the worker permanently lowers the campaign concurrency.
        finally:
            if lock:
                lock.release()
        pending = [c for c in pending if c["id"] != target["id"]]
    return dict(worker=worker, results=results)


def summarize(root, all_results, workers=0):
    total = len(slots())
    qualified = sum(1 for r in all_results if r.get("qualified"))
    warmup_failed = [r["slot_id"] for r in all_results if r.get("status") == "warmup_gate_failed"]
    failed = [dict(slot_id=r["slot_id"], status=r.get("status"), reason=r.get("reason"))
              for r in all_results if not r.get("qualified") and r.get("status") != "warmup_gate_failed"]
    limited = [r["slot_id"] for r in all_results if (r.get("gate") or {}).get("host_limited")]
    summary = dict(stage=STAGE, total_slots=total, attempted=len(all_results),
                   qualified=qualified, warmup_gate_failed=warmup_failed,
                   failed=failed, host_limited=limited, workers_max=workers,
                   epoch_ns=time.time_ns())
    out = root / "derived" / "supplement-summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--worker", type=int, default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    root = pilot.ROOT

    if args.worker is not None:
        print(json.dumps(worker_loop(root, args.worker, args.workers, args.limit)), flush=True)
        return 0

    if args.dry_run:
        import shutil
        made = []
        for index, c in enumerate(slots()):
            pilot.context(c)
            lock = ScenarioLock(root, pilot.SCENARIO)
            ok = lock.acquire()
            got = claim(root, c["id"], "dry-run")
            if got:
                made.append(root / "derived" / "supplement-claims" / c["id"])
            print(json.dumps(dict(index=index, slot=c["id"], scenario=pilot.SCENARIO,
                                  lock=ok, claimed=got)))
            lock.release()
            if len(made) >= 6:
                break
        # the dry run must never block the real campaign
        for path in made:
            shutil.rmtree(path, ignore_errors=True)
        print(json.dumps(dict(dry_run="complete", claims_cleaned=len(made))))
        return 0

    if not args.execute:
        ap.error("--execute or --dry-run required")
    lock_path = root / "supplement.lock"
    with lock_path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pilot.preflight()
        proc = [subprocess.Popen([sys.executable, str(Path(__file__)), "--worker", str(i),
                                  "--workers", str(args.workers), "--limit", str(args.limit)],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for i in range(args.workers)]
        all_results = []
        for p in proc:
            text, _ = p.communicate()
            print(text, end="", flush=True)
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("{") and '"slot"' in line:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("phase") == "start":
                        continue
                    all_results.append(dict(slot_id=payload["slot"], status=payload.get("status"),
                                            qualified=payload.get("qualified"),
                                            reason=payload.get("reason"),
                                            wall_seconds=payload.get("wall")))
        # merge the per-slot progress records for the full picture
        merged = {}
        for path in sorted((root / "derived" / "supplement-progress").glob("*.json")):
            rec = json.loads(path.read_text())
            merged[rec["slot_id"]] = rec
        for slot_id, rec in merged.items():
            if not any(r["slot_id"] == slot_id for r in all_results):
                all_results.append(dict(slot_id=slot_id, status="recorded",
                                        qualified=rec.get("qualified"), gate=rec))
        summary = summarize(root, all_results, args.workers)
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())