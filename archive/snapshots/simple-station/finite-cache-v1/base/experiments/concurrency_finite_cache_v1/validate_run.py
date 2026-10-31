#!/usr/bin/env python3
"""Acceptance checks for the concurrency-finite-cache-v1 experiment.

Two distinct jobs, deliberately not conflated:

  --backend-gate <run_dir>
      The backend-error gate ONLY. It scans the run's traces and fatal markers
      and reports a *local* status: ``BACKEND_GATE_PASS`` / ``BACKEND_GATE_FAIL``.
      It never publishes a run-level READY. A missing/empty trace set fails the
      gate (absence of errors cannot be established from no evidence).

  --run <run_dir> --manifest <manifest.json>
      The full validator. READY requires ALL of:
        * the backend gate passes (no fatal marker, no cache_backend_error, no
          corrupt/truncated/non-object JSON record);
        * provenance: every file listed in the manifest exists and matches its
          recorded sha256, and NO unregistered ``*.jsonl`` trace exists;
        * trace completeness: every expected node appears, and every required
          event is present;
        * monitor evidence (when the manifest asks for it);
        * request completion and content correctness for every listed request.

The manifest is the traceable record of which evidence is expected and what it
hashed to. Without a trusted manifest the tool refuses to claim completeness or
READY (``manifest_required``); it never infers the expected set by scanning
"whatever files are left".

The tool reads raw evidence and never rewrites or deletes it. Deleting or
modifying a registered file cannot turn a failing run into a passing one: the
file is either missing (fail) or hash-mismatched (fail).

Exit codes: 0 = pass/READY, 2 = rejected, 3 = usage/IO error.
``--self-test`` builds derived fixtures (not a real process chain) and asserts
every outcome above.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

FATAL_DIR = Path("control") / "fatal"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_traces(run_dir: Path) -> list[Path]:
    if not run_dir.is_dir():
        return []
    return sorted(p for p in run_dir.rglob("*.jsonl") if p.is_file())


def _parse_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Strict JSONL parse. Non-empty lines must be JSON objects."""
    records: list[dict[str, Any]] = []
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return records, [f"trace_unreadable:{path.name}:{exc}"]
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"corrupt_json:{path.name}:{lineno}:{exc.msg}")
            continue
        if not isinstance(record, dict):
            problems.append(f"non_object_json:{path.name}:{lineno}")
            continue
        records.append(record)
    return records, problems


def backend_gate(run_dir: Path) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    traces = _iter_traces(run_dir)
    if not traces:
        reasons.append("no_traces")

    fatal_dir = run_dir / FATAL_DIR
    if fatal_dir.is_dir():
        for marker in sorted(p for p in fatal_dir.rglob("*") if p.is_file()):
            reasons.append(f"fatal_marker:{marker.name}")

    for trace in traces:
        records, problems = _parse_records(trace)
        reasons.extend(problems)
        for record in records:
            if record.get("event") == "cache_backend_error":
                reasons.append(
                    "backend_error:"
                    + str(record.get("reason", "<missing>"))
                    + ":"
                    + str(record.get("category", "<missing>"))
                    + f":{trace.name}"
                )
    return (not reasons), reasons


def validate_run(run_dir: Path, manifest_path: Path | None) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if manifest_path is None or not manifest_path.is_file():
        return False, ["manifest_required"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"manifest_unreadable:{exc}"]
    if not isinstance(manifest, dict):
        return False, ["manifest_not_object"]

    _, gate_reasons = backend_gate(run_dir)
    reasons.extend(gate_reasons)

    # Provenance: the manifest is the expected file set, not "what is left".
    listed: dict[str, dict[str, Any]] = {}
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or "path" not in entry:
            reasons.append("manifest_bad_file_entry")
            continue
        rel = str(entry["path"])
        listed[rel] = entry
        path = run_dir / rel
        if not path.is_file():
            reasons.append(f"registered_file_missing:{rel}")
            continue
        expected_hash = entry.get("sha256")
        if expected_hash and _sha256(path) != expected_hash:
            reasons.append(f"registered_hash_mismatch:{rel}")

    on_disk = {str(t.relative_to(run_dir)) for t in _iter_traces(run_dir)}
    for rel in sorted(on_disk - set(listed)):
        reasons.append(f"unregistered_trace:{rel}")

    records: list[dict[str, Any]] = []
    for rel in sorted(listed):
        path = run_dir / rel
        if path.is_file():
            file_records, _ = _parse_records(path)
            records.extend(file_records)

    # Trace completeness: expected nodes.
    expected_nodes = {int(n) for n in manifest.get("expected_nodes", [])}
    seen_nodes = set()
    for record in records:
        try:
            seen_nodes.add(int(record.get("node")))
        except (TypeError, ValueError):
            continue
    for node in sorted(expected_nodes - seen_nodes):
        reasons.append(f"expected_node_missing:{node}")

    # Required events.
    for event in manifest.get("required_events", []):
        if not any(record.get("event") == event for record in records):
            reasons.append(f"required_event_missing:{event}")

    # Monitor evidence per expected node.
    if manifest.get("require_monitor", False):
        for node in sorted(expected_nodes):
            present = any(
                record.get("event") == "cache_gauge"
                and str(record.get("node")) == str(node)
                for record in records
            )
            if not present:
                reasons.append(f"monitor_evidence_missing:node{node}")

    # Request completion and content correctness.
    for request in manifest.get("requests", []):
        if not isinstance(request, dict):
            reasons.append("manifest_bad_request_entry")
            continue
        index = str(request.get("index"))
        key = request.get("key")
        value_bytes = request.get("value_bytes")
        started = any(
            record.get("event") == "experiment_request_start"
            and str(record.get("experiment_request_index")) == index
            for record in records
        )
        if not started:
            reasons.append(f"request_not_started:{index}")
        delivered = [
            record for record in records
            if record.get("event") == "app_receive" and record.get("key") == key
        ]
        if not delivered:
            reasons.append(f"request_not_completed:{index}")
        elif value_bytes is not None and not any(
            str(record.get("value_bytes")) == str(value_bytes) for record in delivered
        ):
            reasons.append(f"content_mismatch:{index}")

    return (not reasons), reasons


def _write_manifest(run_dir: Path, paths: list[str], **extra: Any) -> Path:
    files = [{"path": rel, "sha256": _sha256(run_dir / rel)} for rel in paths]
    manifest = {"files": files, **extra}
    path = run_dir.parent / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def _self_test() -> int:
    failures = 0

    def check(name: str, condition: bool) -> None:
        nonlocal failures
        print(f"{name}: {'PASS' if condition else 'FAIL'}")
        if not condition:
            failures += 1

    cfg = json.dumps({"event": "cache_config", "node": 1, "run_id": "r1"}) + "\n"
    gauge = json.dumps({"event": "cache_gauge", "node": 1, "evicted_keys": "0"}) + "\n"
    start = json.dumps({"event": "experiment_request_start",
                        "experiment_request_index": "0", "key": "k"}) + "\n"
    recv = json.dumps({"event": "app_receive", "key": "k", "value_bytes": "64",
                       "node": 1}) + "\n"

    with tempfile.TemporaryDirectory(prefix="finite-cache-validate-") as tmp:
        run = Path(tmp) / "run"
        (run / "traces").mkdir(parents=True)
        (run / "traces" / "node1.jsonl").write_text(cfg + gauge + start + recv,
                                                    encoding="utf-8")
        manifest = _write_manifest(
            run, ["traces/node1.jsonl"], expected_nodes=[1],
            required_events=["cache_config", "cache_gauge"], require_monitor=True,
            requests=[{"index": "0", "key": "k", "value_bytes": 64}])
        ready, reasons = validate_run(run, manifest)
        check("complete_run_is_ready", ready and not reasons)

        gate_ok, gate_reasons = backend_gate(run)
        check("backend_gate_passes_clean_run", gate_ok and not gate_reasons)

        # Empty run: never READY, and the gate cannot pass on no evidence.
        empty = Path(tmp) / "empty"
        empty.mkdir()
        ready, reasons = validate_run(empty, manifest)
        check("empty_run_is_not_ready", not ready and any(
            r == "no_traces" or r.startswith("registered_file_missing") for r in reasons))
        gate_ok, _ = backend_gate(empty)
        check("backend_gate_fails_without_traces", not gate_ok)

        # No manifest -> cannot claim completeness.
        ready, reasons = validate_run(run, Path(tmp) / "missing-manifest.json")
        check("missing_manifest_is_not_ready", not ready and reasons == ["manifest_required"])

        # Fatal marker rejects.
        fatal = run / FATAL_DIR
        fatal.mkdir(parents=True)
        (fatal / "node1.backend-indeterminate").write_text("x\n", encoding="utf-8")
        ready, reasons = validate_run(run, manifest)
        check("fatal_marker_rejects", not ready and any(
            r.startswith("fatal_marker:") for r in reasons))
        (fatal / "node1.backend-indeterminate").unlink()
        fatal.rmdir()

        # Backend error rejects even with 100% completion evidence.
        (run / "traces" / "node1.jsonl").write_text(
            cfg + gauge + start + recv
            + json.dumps({"event": "cache_backend_error", "reason":
                          "replica_put_indeterminate", "category": "indeterminate",
                          "fatal": "1"}) + "\n",
            encoding="utf-8")
        ready, reasons = validate_run(run, manifest)
        check("backend_error_rejects_even_at_100pct", not ready and any(
            r.startswith("backend_error:") for r in reasons))
        check("modified_registered_file_is_detected",
              any(r.startswith("registered_hash_mismatch") for r in reasons))

        # Restore, verify READY, then delete the registered file.
        (run / "traces" / "node1.jsonl").write_text(cfg + gauge + start + recv,
                                                    encoding="utf-8")
        manifest = _write_manifest(
            run, ["traces/node1.jsonl"], expected_nodes=[1],
            required_events=["cache_config", "cache_gauge"], require_monitor=True,
            requests=[{"index": "0", "key": "k", "value_bytes": 64}])
        (run / "traces" / "node1.jsonl").unlink()
        ready, reasons = validate_run(run, manifest)
        check("deleted_registered_file_rejects", not ready and any(
            r.startswith("registered_file_missing") for r in reasons))

        # Recreate, verify READY, render one request incomplete.
        (run / "traces" / "node1.jsonl").write_text(cfg + gauge + start,
                                                    encoding="utf-8")
        manifest = _write_manifest(
            run, ["traces/node1.jsonl"], expected_nodes=[1], require_monitor=True,
            requests=[{"index": "0", "key": "k", "value_bytes": 64}])
        ready, reasons = validate_run(run, manifest)
        check("incomplete_request_rejects", not ready and any(
            r.startswith("request_not_completed") for r in reasons))

        # Wrong content bytes rejects.
        (run / "traces" / "node1.jsonl").write_text(
            cfg + gauge + start
            + json.dumps({"event": "app_receive", "key": "k", "value_bytes": "63"}) + "\n",
            encoding="utf-8")
        manifest = _write_manifest(
            run, ["traces/node1.jsonl"], expected_nodes=[1],
            requests=[{"index": "0", "key": "k", "value_bytes": 64}])
        ready, reasons = validate_run(run, manifest)
        check("content_mismatch_rejects", not ready and any(
            r.startswith("content_mismatch") for r in reasons))

        # Unregistered trace rejects.
        (run / "traces" / "node1.jsonl").write_text(cfg + gauge + start + recv,
                                                    encoding="utf-8")
        manifest = _write_manifest(run, ["traces/node1.jsonl"], expected_nodes=[1])
        (run / "traces" / "node2.jsonl").write_text(
            json.dumps({"event": "cache_config", "node": 2}) + "\n", encoding="utf-8")
        ready, reasons = validate_run(run, manifest)
        check("unregistered_trace_rejects", not ready and any(
            r.startswith("unregistered_trace") for r in reasons))
        (run / "traces" / "node2.jsonl").unlink()

        # Corrupt / truncated / non-object JSON.
        (run / "traces" / "node1.jsonl").write_text(
            cfg + '{"event": "cache_gauge", "node": 1\n' + "not json\n"
            + "[1, 2, 3]\n", encoding="utf-8")
        manifest = _write_manifest(run, ["traces/node1.jsonl"], expected_nodes=[1])
        ready, reasons = validate_run(run, manifest)
        check("corrupt_json_rejects", not ready and any(
            r.startswith("corrupt_json") for r in reasons))
        check("non_object_json_rejects", any(
            r.startswith("non_object_json") for r in reasons))

        # Missing expected node / required event / monitor evidence.
        (run / "traces" / "node1.jsonl").write_text(cfg, encoding="utf-8")
        manifest = _write_manifest(
            run, ["traces/node1.jsonl"], expected_nodes=[1, 7],
            required_events=["cache_gauge"], require_monitor=True)
        ready, reasons = validate_run(run, manifest)
        check("missing_expected_node_rejects", any(
            r == "expected_node_missing:7" for r in reasons))
        check("missing_required_event_rejects", any(
            r == "required_event_missing:cache_gauge" for r in reasons))
        check("missing_monitor_evidence_rejects", any(
            r == "monitor_evidence_missing:node1" for r in reasons))

        before = _sha256(run / "traces" / "node1.jsonl")
        validate_run(run, manifest)
        after = _sha256(run / "traces" / "node1.jsonl")
        check("validator_does_not_rewrite_evidence", before == after)

    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend-gate", metavar="RUN_DIR",
                        help="run only the backend-error gate")
    parser.add_argument("--run", metavar="RUN_DIR",
                        help="full validation of a run directory")
    parser.add_argument("--manifest", metavar="MANIFEST",
                        help="trusted evidence manifest for --run")
    parser.add_argument("--self-test", action="store_true",
                        help="run built-in derived-fixture checks")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.backend_gate:
        run_dir = Path(args.backend_gate)
        if not run_dir.is_dir():
            print(f"BACKEND_GATE_FAIL: run directory not found: {run_dir}",
                  file=sys.stderr)
            return 3
        ok, reasons = backend_gate(run_dir)
        if ok:
            print(f"BACKEND_GATE_PASS: {run_dir}")
            return 0
        print(f"BACKEND_GATE_FAIL: {run_dir}")
        for reason in reasons:
            print(f"  - {reason}")
        return 2

    if args.run:
        run_dir = Path(args.run)
        if not run_dir.is_dir():
            print(f"REJECTED: run directory not found: {run_dir}", file=sys.stderr)
            return 3
        ready, reasons = validate_run(
            run_dir, Path(args.manifest) if args.manifest else None)
        if ready:
            print(f"READY: {run_dir}")
            return 0
        print(f"REJECTED: {run_dir}")
        for reason in reasons:
            print(f"  - {reason}")
        return 2

    parser.error("choose --backend-gate, --run (with --manifest) or --self-test")
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))