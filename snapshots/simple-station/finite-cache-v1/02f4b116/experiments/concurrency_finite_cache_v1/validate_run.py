#!/usr/bin/env python3
"""Acceptance validator for the concurrency-finite-cache-v1 experiment.

Two deliberately separate jobs:

  --backend-gate <run_dir>
      LOCAL backend-error gate only: it reports BACKEND_GATE_PASS /
      BACKEND_GATE_FAIL and never a run-level READY. It fails when there are no
      traces (absence of errors cannot be established from no evidence), when a
      corrupt/truncated/non-object JSON record exists, when any fatal marker is
      present, or when any cache_backend_error record exists.

  --run <run_dir> --manifest <manifest.json>
      Full run validation. READY requires ALL of:
        * the backend gate passes;
        * the manifest itself satisfies the formal contract below;
        * provenance: every registered file exists, matches its SHA-256, no
          duplicate/missing/unsafe path, and no unregistered *.jsonl exists;
        * every registered trace parses cleanly, is non-empty and has records;
        * trace completeness: every expected node appears and every required
          event is present;
        * request identity + content: every planned request starts exactly once
          and completes exactly once (canonical completion evidence), with the
          exact node/key/expected size and the ACTUAL delivered content hash
          equal to the planned hash; app-level completion evidence is required
          (protocol app_receive alone is not enough); duplicate, unknown,
          mismatched or non-ok completions are rejected;
        * periodic Redis monitoring: registered monitor evidence in the real
          schema covers the run window with no gap beyond the declared limit,
          a consistent Redis run_id, non-decreasing evicted_keys, and every
          sample status ok. cache_gauge is NOT accepted as monitor evidence.

Manifest contract (JSON, schema/version mandatory):

  {
    "schema": "finite-cache-acceptance-manifest",
    "version": 1,
    "run": {"run_id": ..., "campaign": ..., "created_by": ...,
            "source_sha": ..., "image_id": ...},
    "expected_nodes": [1, ...],                 # non-empty, unique, positive
    "required_events": ["cache_config", ...],   # non-empty
    "files": [
      {"path": "traces/node1.jsonl", "sha256": <64 hex>, "kind": "trace",
       "nodes": [1]},
      {"path": "completions/req-1.tsv", "sha256": ..., "kind": "completion",
       "request_index": "req-1"},
      {"path": "delivery-audit/node1.tsv", "sha256": ..., "kind": "audit"},
      {"path": "monitor/redis_monitor.jsonl", "sha256": ..., "kind": "monitor"}
    ],
    "requests": [
      {"index": "req-1", "node": 1, "key": "k", "value_bytes": 64,
       "value_hash": <16 hex>}
    ],
    "monitor": {"require": true, "instance_identity": ...,
                "baseline_run_id": ..., "interval_ms": 200,
                "max_gap_ms": 1500,
                "window": {"start_epoch_ns": ..., "end_epoch_ns": ...}}
  }

The manifest is the run's pre-declared plan; collection records the actual
evidence hashes. A freely rewritable manifest cannot prove that historical
evidence was never altered -- this tool only proves that the evidence present
matches the recorded plan. It reads raw evidence and never rewrites it.

Exit codes: 0 = pass/READY, 2 = rejected, 3 = usage/IO error.
--self-test builds derived acceptance fixtures and asserts every outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "finite-cache-acceptance-manifest"
VERSION = 1
FATAL_DIR = Path("control") / "fatal"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")
KINDS = ("trace", "completion", "audit", "monitor", "config")


# --------------------------------------------------------------------------- #
# low-level helpers
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_jsonl(run_dir: Path) -> list[Path]:
    if not run_dir.is_dir():
        return []
    return sorted(p for p in run_dir.rglob("*.jsonl") if p.is_file())


def _parse_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Strict JSONL: non-empty lines must be JSON objects; encoding is checked."""
    records: list[dict[str, Any]] = []
    problems: list[str] = []
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        return records, [f"trace_unreadable:{path.name}:{exc}"]
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return records, [f"trace_bad_encoding:{path.name}:{exc}"]
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


def _parse_tsv(path: Path) -> tuple[list[list[str]], list[str]]:
    rows: list[list[str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return rows, [f"tsv_unreadable:{path.name}:{exc}"]
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        rows.append([field.strip() for field in line.split("\t")])
    return rows, []


def _safe_relative(run_dir: Path, rel: str) -> Path | None:
    """A manifest path must stay inside the run root."""
    if not rel or rel.startswith("/") or "\\" in rel:
        return None
    candidate = (run_dir / rel).resolve()
    try:
        candidate.relative_to(run_dir.resolve())
    except ValueError:
        return None
    return candidate


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# --------------------------------------------------------------------------- #
# backend gate (local status only)
# --------------------------------------------------------------------------- #
def backend_gate(run_dir: Path) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    traces = _iter_jsonl(run_dir)
    if not traces:
        reasons.append("no_traces")
    elif all(t.stat().st_size == 0 for t in traces):
        reasons.append("all_traces_empty")

    fatal_dir = run_dir / FATAL_DIR
    if fatal_dir.is_dir():
        for marker in sorted(p for p in fatal_dir.rglob("*") if p.is_file()):
            reasons.append(f"fatal_marker:{marker.name}")

    for trace in traces:
        records, problems = _parse_jsonl(trace)
        reasons.extend(problems)
        if trace.stat().st_size > 0 and not records and not problems:
            reasons.append(f"trace_without_records:{trace.name}")
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


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
class ManifestError(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def load_manifest(path: Path) -> dict[str, Any]:
    reasons: list[str] = []
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError([f"manifest_unreadable:{exc}"])
    if not isinstance(manifest, dict):
        raise ManifestError(["manifest_not_object"])

    if manifest.get("schema") != SCHEMA:
        reasons.append("manifest_bad_schema")
    if manifest.get("version") != VERSION:
        reasons.append("manifest_bad_version")

    run = manifest.get("run")
    if not isinstance(run, dict):
        reasons.append("manifest_run_missing")
    else:
        for field in ("run_id", "campaign", "created_by", "source_sha", "image_id"):
            value = run.get(field)
            if not isinstance(value, str) or not value:
                reasons.append(f"manifest_run_field_missing:{field}")

    nodes = manifest.get("expected_nodes")
    if not isinstance(nodes, list) or not nodes:
        reasons.append("manifest_expected_nodes_missing")
    elif any(not _is_int(n) or n <= 0 for n in nodes) or len(set(nodes)) != len(nodes):
        reasons.append("manifest_expected_nodes_invalid")

    events = manifest.get("required_events")
    if not isinstance(events, list) or not events:
        reasons.append("manifest_required_events_missing")
    elif any(not isinstance(e, str) or not e for e in events):
        reasons.append("manifest_required_events_invalid")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        reasons.append("manifest_files_missing")
    else:
        seen: set[str] = set()
        for entry in files:
            if not isinstance(entry, dict):
                reasons.append("manifest_file_entry_not_object")
                continue
            rel = entry.get("path")
            if not isinstance(rel, str) or not rel:
                reasons.append("manifest_file_path_invalid")
                continue
            if rel in seen:
                reasons.append(f"manifest_duplicate_file:{rel}")
            seen.add(rel)
            digest = entry.get("sha256")
            if not isinstance(digest, str) or not HEX64.match(digest):
                reasons.append(f"manifest_file_hash_invalid:{rel}")
            kind = entry.get("kind")
            if kind not in KINDS:
                reasons.append(f"manifest_file_kind_invalid:{rel}")
            if kind == "completion" and not isinstance(entry.get("request_index"), str):
                reasons.append(f"manifest_completion_request_index_missing:{rel}")
            if kind == "trace":
                file_nodes = entry.get("nodes")
                if not isinstance(file_nodes, list) or not file_nodes:
                    reasons.append(f"manifest_trace_nodes_missing:{rel}")

    requests = manifest.get("requests")
    if not isinstance(requests, list) or not requests:
        reasons.append("manifest_requests_missing")
    else:
        seen_index: set[str] = set()
        for request in requests:
            if not isinstance(request, dict):
                reasons.append("manifest_request_entry_not_object")
                continue
            index = request.get("index")
            if not isinstance(index, str) or not index:
                reasons.append("manifest_request_index_invalid")
                continue
            if index in seen_index:
                reasons.append(f"manifest_duplicate_request:{index}")
            seen_index.add(index)
            if not _is_int(request.get("node")) or request.get("node") <= 0:
                reasons.append(f"manifest_request_node_invalid:{index}")
            if not isinstance(request.get("key"), str) or not request.get("key"):
                reasons.append(f"manifest_request_key_invalid:{index}")
            if not _is_int(request.get("value_bytes")) or request.get("value_bytes") <= 0:
                reasons.append(f"manifest_request_size_invalid:{index}")
            if not isinstance(request.get("value_hash"), str) or not HEX16.match(
                request["value_hash"]
            ):
                reasons.append(f"manifest_request_hash_invalid:{index}")

    monitor = manifest.get("monitor")
    if not isinstance(monitor, dict):
        reasons.append("manifest_monitor_missing")
    else:
        for field in ("instance_identity", "baseline_run_id"):
            if not isinstance(monitor.get(field), str) or not monitor.get(field):
                reasons.append(f"manifest_monitor_field_missing:{field}")
        if not _is_int(monitor.get("baseline_evicted_keys")) \
                or monitor["baseline_evicted_keys"] < 0:
            reasons.append("manifest_monitor_baseline_evicted_invalid")
        if not _is_int(monitor.get("interval_ms")) or monitor["interval_ms"] <= 0:
            reasons.append("manifest_monitor_interval_invalid")
        if not _is_int(monitor.get("max_gap_ms")) or monitor["max_gap_ms"] <= 0:
            reasons.append("manifest_monitor_max_gap_invalid")
        window = monitor.get("window")
        if not isinstance(window, dict) or not _is_int(window.get("start_epoch_ns")) \
                or not _is_int(window.get("end_epoch_ns")) \
                or window["end_epoch_ns"] <= window["start_epoch_ns"]:
            reasons.append("manifest_monitor_window_invalid")

    if reasons:
        raise ManifestError(reasons)
    return manifest


# --------------------------------------------------------------------------- #
# full validation
# --------------------------------------------------------------------------- #
def validate_run(run_dir: Path, manifest_path: Path | None) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if manifest_path is None or not manifest_path.is_file():
        return False, ["manifest_required"]
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        return False, exc.reasons

    _, gate_reasons = backend_gate(run_dir)
    reasons.extend(gate_reasons)

    expected_nodes = [int(n) for n in manifest["expected_nodes"]]
    files = manifest["files"]
    requests = manifest["requests"]
    monitor_spec = manifest["monitor"]

    # ---- provenance --------------------------------------------------------
    listed: dict[str, dict[str, Any]] = {}
    for entry in files:
        rel = entry["path"]
        listed[rel] = entry
        path = _safe_relative(run_dir, rel)
        if path is None:
            reasons.append(f"registered_path_unsafe:{rel}")
            continue
        if not path.is_file():
            reasons.append(f"registered_file_missing:{rel}")
            continue
        if _sha256(path) != entry["sha256"]:
            reasons.append(f"registered_hash_mismatch:{rel}")

    on_disk = {str(p.relative_to(run_dir)) for p in _iter_jsonl(run_dir)}
    for rel in sorted(on_disk - set(listed)):
        reasons.append(f"unregistered_trace:{rel}")

    # ---- parse registered evidence ----------------------------------------
    trace_records: list[dict[str, Any]] = []
    tsv_rows: dict[str, list[list[str]]] = {}
    monitor_records: list[dict[str, Any]] = []
    for rel, entry in listed.items():
        path = _safe_relative(run_dir, rel)
        if path is None or not path.is_file():
            continue
        kind = entry["kind"]
        if kind in ("trace", "monitor"):
            records, problems = _parse_jsonl(path)
            reasons.extend(problems)
            if path.stat().st_size == 0:
                reasons.append(f"registered_file_empty:{rel}")
            elif not records and not problems:
                reasons.append(f"registered_file_no_records:{rel}")
            if kind == "trace":
                trace_records.extend(records)
            else:
                monitor_records.extend(records)
        elif kind in ("completion", "audit"):
            rows, problems = _parse_tsv(path)
            reasons.extend(problems)
            if path.stat().st_size == 0:
                reasons.append(f"registered_file_empty:{rel}")
            tsv_rows[rel] = rows

    # ---- trace completeness ------------------------------------------------
    seen_nodes = set()
    for record in trace_records:
        try:
            seen_nodes.add(int(record.get("node")))
        except (TypeError, ValueError):
            continue
    for node in sorted(set(expected_nodes) - seen_nodes):
        reasons.append(f"expected_node_missing:{node}")
    for event in manifest["required_events"]:
        if not any(record.get("event") == event for record in trace_records):
            reasons.append(f"required_event_missing:{event}")

    # ---- request identity + content ---------------------------------------
    start_counts: dict[str, int] = {}
    for record in trace_records:
        if record.get("event") != "experiment_request_start":
            continue
        index = str(record.get("experiment_request_index", ""))
        start_counts[index] = start_counts.get(index, 0) + 1
        planned = next((r for r in requests if r["index"] == index), None)
        if planned is None:
            reasons.append(f"unknown_request_start:{index}")
            continue
        if str(record.get("node")) != str(planned["node"]):
            reasons.append(f"request_start_wrong_node:{index}")
        if record.get("key") != planned["key"]:
            reasons.append(f"request_start_wrong_key:{index}")
        if str(record.get("value_bytes")) != str(planned["value_bytes"]):
            reasons.append(f"request_start_wrong_size:{index}")

    completion_files = {
        entry.get("request_index"): rel
        for rel, entry in listed.items()
        if entry["kind"] == "completion"
    }
    audit_rows: list[list[str]] = []
    for rel, entry in listed.items():
        if entry["kind"] == "audit":
            audit_rows.extend(tsv_rows.get(rel, []))

    for request in requests:
        index = request["index"]
        if start_counts.get(index, 0) != 1:
            reasons.append(
                f"request_start_count:{index}:{start_counts.get(index, 0)}")

        rel = completion_files.get(index)
        if rel is None:
            reasons.append(f"request_completion_file_missing:{index}")
            continue
        rows = tsv_rows.get(rel, [])
        if len(rows) != 1:
            reasons.append(f"request_completion_count:{index}:{len(rows)}")
            continue
        row = rows[0]
        if len(row) < 6:
            reasons.append(f"request_completion_row_short:{index}")
            continue
        got_index, got_node, got_key, got_status, got_size, got_hash = row[:6]
        if got_index != index:
            reasons.append(f"completion_wrong_index:{index}:{got_index}")
        if got_node != str(request["node"]):
            reasons.append(f"completion_wrong_node:{index}")
        if got_key != request["key"]:
            reasons.append(f"completion_wrong_key:{index}")
        if got_status != "ok":
            reasons.append(f"completion_status_not_ok:{index}:{got_status}")
        if got_size != str(request["value_bytes"]):
            reasons.append(f"completion_wrong_size:{index}")
        if not HEX16.match(got_hash or ""):
            reasons.append(f"completion_hash_invalid:{index}")
        elif got_hash != request["value_hash"]:
            reasons.append(f"content_hash_mismatch:{index}")

        # The append audit is a second observation layer. It must show exactly
        # one completion for this request: >1 means the request completed twice.
        if audit_rows:
            matching = [r for r in audit_rows if r and r[0] == index]
            if len(matching) != 1:
                reasons.append(f"audit_completion_count:{index}:{len(matching)}")

    planned_indexes = {r["index"] for r in requests}
    for row in audit_rows:
        if row and row[0] and row[0] not in planned_indexes:
            reasons.append(f"unknown_audit_completion:{row[0]}")

    # ---- periodic Redis monitoring ----------------------------------------
    samples = [r for r in monitor_records if r.get("event") == "redis_monitor_sample"]
    starts = [r for r in monitor_records if r.get("event") == "redis_monitor_start"]
    ends = [r for r in monitor_records if r.get("event") == "redis_monitor_end"]
    if not starts:
        reasons.append("monitor_start_missing")
    if not ends:
        reasons.append("monitor_end_missing")
    if len(samples) < 2:
        reasons.append(f"monitor_samples_insufficient:{len(samples)}")
    if ends and str(ends[-1].get("exit_status", "")) not in ("0", "ok"):
        reasons.append("monitor_exit_status_not_ok")

    window = monitor_spec["window"]
    prev_mono: int | None = None
    prev_evicted: int | None = None
    seen_run_id: str | None = None
    covered_start = False
    covered_end = False
    for position, sample in enumerate(samples):
        for field in ("instance_identity", "redis_run_id", "sample_mono_ms",
                      "ts_epoch_ns", "used_memory", "evicted_keys", "status"):
            if field not in sample:
                reasons.append(f"monitor_sample_field_missing:{position}:{field}")
        if sample.get("instance_identity") != monitor_spec["instance_identity"]:
            reasons.append(f"monitor_identity_mismatch:{position}")
        status = str(sample.get("status", ""))
        if status != "ok":
            reasons.append(f"monitor_sample_not_ok:{position}:{status}")
        run_id = str(sample.get("redis_run_id", ""))
        if seen_run_id is None:
            seen_run_id = run_id
            if run_id != monitor_spec["baseline_run_id"]:
                reasons.append("monitor_run_id_not_baseline")
        elif run_id != seen_run_id:
            reasons.append(f"monitor_run_id_changed:{position}")
        try:
            evicted = int(sample.get("evicted_keys"))
            used = int(sample.get("used_memory"))
        except (TypeError, ValueError):
            reasons.append(f"monitor_numeric_invalid:{position}")
            continue
        if used < 0:
            reasons.append(f"monitor_used_memory_missing:{position}")
        if evicted > monitor_spec["baseline_evicted_keys"]:
            reasons.append(f"monitor_eviction_increase:{position}")
        if prev_evicted is not None and evicted < prev_evicted:
            reasons.append(f"monitor_evicted_regressed:{position}")
            prev_evicted = evicted
        elif prev_evicted is None or evicted > prev_evicted:
            prev_evicted = evicted
        try:
            mono = int(sample.get("sample_mono_ms"))
            epoch = int(sample.get("ts_epoch_ns"))
        except (TypeError, ValueError):
            reasons.append(f"monitor_time_invalid:{position}")
            continue
        if prev_mono is not None:
            gap = mono - prev_mono
            if gap < 0:
                reasons.append(f"monitor_time_regressed:{position}")
            elif gap > monitor_spec["max_gap_ms"]:
                reasons.append(f"monitor_gap_exceeded:{position}:{gap}")
        prev_mono = mono
        if epoch <= window["start_epoch_ns"]:
            covered_start = True
        if epoch >= window["end_epoch_ns"]:
            covered_end = True
    if samples and not covered_start:
        reasons.append("monitor_does_not_cover_start")
    if samples and not covered_end:
        reasons.append("monitor_does_not_cover_end")

    return (not reasons), reasons


# --------------------------------------------------------------------------- #
# self-test fixtures
# --------------------------------------------------------------------------- #
def _fixture(tmp: Path) -> tuple[Path, Path, str, str]:
    """Build a complete, valid acceptance fixture. Returns (run, manifest, idx, hash)."""
    base = Path(tempfile.mkdtemp(dir=str(tmp), prefix="fixture-"))
    run = base / "run"
    (run / "traces").mkdir(parents=True)
    (run / "completions").mkdir(parents=True)
    (run / "delivery-audit").mkdir(parents=True)
    (run / "monitor").mkdir(parents=True)

    key = "k1"
    index = "req-1"
    payload_hash = "0123456789abcdef"
    now = 1_000_000_000
    (run / "traces" / "node1.jsonl").write_text(
        json.dumps({"event": "cache_config", "node": 1}) + "\n"
        + json.dumps({"event": "cache_gauge", "node": 1}) + "\n"
        + json.dumps({"event": "experiment_request_start",
                      "experiment_request_index": index, "node": 1,
                      "key": key, "value_bytes": 64}) + "\n"
        + json.dumps({"event": "app_receive", "node": 1, "key": key}) + "\n",
        encoding="utf-8")
    completion = "\t".join([index, "1", key, "ok", "64", payload_hash,
                            "100", "200", "77", "data", "hash", "", "0"]) + "\n"
    (run / "completions" / f"{index}.tsv").write_text(completion, encoding="utf-8")
    (run / "delivery-audit" / "node1.tsv").write_text(completion, encoding="utf-8")

    samples = []
    for i in range(6):
        samples.append(json.dumps({
            "event": "redis_monitor_sample",
            "instance_identity": "fixture-instance",
            "redis_run_id": "runid-abc",
            "sample_mono_ms": i * 200,
            "ts_epoch_ns": now + i * 200_000_000,
            "used_memory": 1_000_000 + i,
            "evicted_keys": 0,
            "status": "ok"}))
    (run / "monitor" / "redis_monitor.jsonl").write_text(
        json.dumps({"event": "redis_monitor_start",
                    "instance_identity": "fixture-instance",
                    "redis_run_id": "runid-abc"}) + "\n"
        + "\n".join(samples) + "\n"
        + json.dumps({"event": "redis_monitor_end", "exit_status": "ok"}) + "\n",
        encoding="utf-8")

    def entry(rel: str, kind: str, **extra: Any) -> dict[str, Any]:
        return {"path": rel, "kind": kind, "sha256": _sha256(run / rel), **extra}

    manifest = {
        "schema": SCHEMA,
        "version": VERSION,
        "run": {"run_id": "fixture-run", "campaign": "acceptance-fixture",
                "created_by": "self-test", "source_sha": "deadbeef",
                "image_id": "sha256:fixture"},
        "expected_nodes": [1],
        "required_events": ["cache_config", "cache_gauge"],
        "files": [
            entry("traces/node1.jsonl", "trace", nodes=[1]),
            entry(f"completions/{index}.tsv", "completion", request_index=index),
            entry("delivery-audit/node1.tsv", "audit"),
            entry("monitor/redis_monitor.jsonl", "monitor"),
        ],
        "requests": [{"index": index, "node": 1, "key": key,
                      "value_bytes": 64, "value_hash": payload_hash}],
        "monitor": {"require": True, "instance_identity": "fixture-instance",
                    "baseline_run_id": "runid-abc", "baseline_evicted_keys": 0,
                    "interval_ms": 200,
                    "max_gap_ms": 500,
                    "window": {"start_epoch_ns": now, "end_epoch_ns": now + 1_000_000_000}},
    }
    manifest_path = base / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return run, manifest_path, index, payload_hash


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _rehash_manifest(run: Path, manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        candidate = run / entry["path"]
        if candidate.is_file():
            entry["sha256"] = _sha256(candidate)
    _write_manifest(manifest_path, manifest)


def _self_test() -> int:
    failures = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"{name}: {'PASS' if condition else 'FAIL'}{' ' + detail if detail and not condition else ''}")
        if not condition:
            failures += 1

    with tempfile.TemporaryDirectory(prefix="finite-cache-validate-") as tmp:
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        ready, reasons = validate_run(run, manifest_path)
        check("complete_fixture_is_ready", ready, str(reasons))
        gate_ok, _ = backend_gate(run)
        check("backend_gate_passes_complete_fixture", gate_ok)

        # empty run / empty trace
        empty = Path(tmp) / "empty"
        empty.mkdir()
        check("empty_dir_backend_gate_fails", not backend_gate(empty)[0])
        ready, _ = validate_run(empty, manifest_path)
        check("empty_dir_not_ready", not ready)

        (run / "traces" / "node1.jsonl").write_text("", encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("empty_registered_trace_rejects", not ready and any(
            r.startswith("registered_file_empty") for r in reasons), str(reasons))

        # restore fixture
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # manifest contract violations
        for name, mutate, expect in [
            ("manifest_missing_schema", lambda m: m.pop("schema"), "manifest_bad_schema"),
            ("manifest_missing_version", lambda m: m.pop("version"), "manifest_bad_version"),
            ("manifest_empty_nodes", lambda m: m.__setitem__("expected_nodes", []),
             "manifest_expected_nodes_missing"),
            ("manifest_empty_requests", lambda m: m.__setitem__("requests", []),
             "manifest_requests_missing"),
            ("manifest_empty_files", lambda m: m.__setitem__("files", []),
             "manifest_files_missing"),
            ("manifest_bad_hash", lambda m: m["files"][0].__setitem__("sha256", "xyz"),
             "manifest_file_hash_invalid"),
            ("manifest_unsafe_path",
             lambda m: m["files"][0].__setitem__("path", "../outside.jsonl"),
             None),
            ("manifest_duplicate_file",
             lambda m: m["files"].append(dict(m["files"][0])),
             "manifest_duplicate_file"),
            ("manifest_duplicate_request",
             lambda m: m["requests"].append(dict(m["requests"][0])),
             "manifest_duplicate_request"),
            ("manifest_bad_type",
             lambda m: m["requests"][0].__setitem__("value_bytes", "64"),
             "manifest_request_size_invalid"),
        ]:
            mutated = json.loads(json.dumps(manifest))
            mutate(mutated)
            _write_manifest(Path(tmp) / "bad.json", mutated)
            ready, reasons = validate_run(run, Path(tmp) / "bad.json")
            ok = not ready and (expect is None or any(expect in r for r in reasons))
            check(name, ok, str(reasons))

        # missing / mismatched registered file
        (run / "completions" / f"{index}.tsv").unlink()
        ready, reasons = validate_run(run, manifest_path)
        check("deleted_registered_file_rejects",
              not ready and any(r.startswith("registered_file_missing") for r in reasons))
        (run / "completions" / f"{index}.tsv").write_text("tampered\n", encoding="utf-8")
        ready, reasons = validate_run(run, manifest_path)
        check("tampered_registered_file_rejects",
              not ready and any(r.startswith("registered_hash_mismatch") for r in reasons))

        # restore, then corrupt JSON / non-object / truncated
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        (run / "traces" / "node1.jsonl").write_text(
            '{"event": "cache_config", "node": 1\n' + "not json\n" + "[1,2]\n",
            encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("corrupt_json_rejects", not ready and any(
            r.startswith("corrupt_json") for r in reasons))
        check("non_object_json_rejects", any(
            r.startswith("non_object_json") for r in reasons))
        check("truncated_json_rejects", any(
            r.startswith("corrupt_json") for r in reasons))

        # missing expected node / required event
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["expected_nodes"] = [1, 7]
        manifest["required_events"] = ["cache_config", "never_emitted"]
        _write_manifest(manifest_path, manifest)
        ready, reasons = validate_run(run, manifest_path)
        check("missing_node_rejects", "expected_node_missing:7" in reasons)
        check("missing_event_rejects", "required_event_missing:never_emitted" in reasons)

        # unregistered trace
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        (run / "traces" / "node2.jsonl").write_text(
            json.dumps({"event": "cache_config", "node": 2}) + "\n", encoding="utf-8")
        ready, reasons = validate_run(run, manifest_path)
        check("unregistered_trace_rejects", not ready and any(
            r.startswith("unregistered_trace") for r in reasons))

        # --- request identity / content ---
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # two same-key requests, one completion
        manifest["requests"].append({"index": "req-2", "node": 1, "key": "k1",
                                     "value_bytes": 64, "value_hash": payload_hash})
        _write_manifest(manifest_path, manifest)
        ready, reasons = validate_run(run, manifest_path)
        check("same_key_two_requests_one_completion_rejects", not ready and any(
            r.startswith("request_completion_file_missing:req-2") for r in reasons))

        # duplicate completion in the audit layer
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        audit = run / "delivery-audit" / "node1.tsv"
        audit.write_text(audit.read_text(encoding="utf-8") * 2, encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("duplicate_completion_rejects", not ready and any(
            r.startswith("audit_completion_count") for r in reasons))

        # wrong content hash (same length)
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        comp = run / "completions" / f"{index}.tsv"
        row = comp.read_text(encoding="utf-8").split("\t")
        row[5] = "ffffffffffffffff"
        comp.write_text("\t".join(row), encoding="utf-8")
        audit = run / "delivery-audit" / "node1.tsv"
        audit.write_text("\t".join(row), encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("content_hash_mismatch_rejects", not ready and any(
            r.startswith("content_hash_mismatch") for r in reasons))

        # wrong key
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        comp = run / "completions" / f"{index}.tsv"
        row = comp.read_text(encoding="utf-8").split("\t")
        row[2] = "wrong-key"
        comp.write_text("\t".join(row), encoding="utf-8")
        audit = run / "delivery-audit" / "node1.tsv"
        audit.write_text("\t".join(row), encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("completion_wrong_key_rejects", not ready and any(
            r.startswith("completion_wrong_key") for r in reasons))

        # wrong node
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        comp = run / "completions" / f"{index}.tsv"
        row = comp.read_text(encoding="utf-8").split("\t")
        row[1] = "9"
        comp.write_text("\t".join(row), encoding="utf-8")
        audit = run / "delivery-audit" / "node1.tsv"
        audit.write_text("\t".join(row), encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("completion_wrong_node_rejects", not ready and any(
            r.startswith("completion_wrong_node") for r in reasons))

        # only start, no completion (the manifest no longer registers one)
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        (run / "completions" / f"{index}.tsv").unlink()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = [e for e in manifest["files"]
                             if e["kind"] != "completion"]
        _write_manifest(manifest_path, manifest)
        (run / "delivery-audit" / "node1.tsv").write_text("", encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("start_without_completion_rejects", not ready and any(
            r.startswith("request_completion_file_missing") for r in reasons))

        # only protocol app_receive: remove app-level completion evidence
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        (run / "completions" / f"{index}.tsv").unlink()
        (run / "delivery-audit" / "node1.tsv").unlink()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = [e for e in manifest["files"]
                             if e["kind"] not in ("completion", "audit")]
        _write_manifest(manifest_path, manifest)
        ready, reasons = validate_run(run, manifest_path)
        check("protocol_only_completion_rejects", not ready and any(
            r.startswith("request_completion_file_missing") for r in reasons))

        # unknown request completion in the audit
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        audit = run / "delivery-audit" / "node1.tsv"
        audit.write_text(audit.read_text(encoding="utf-8")
                         + "req-unknown\t1\tk1\tok\t64\t" + payload_hash + "\n",
                         encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("unknown_audit_completion_rejects", not ready and any(
            r.startswith("unknown_audit_completion:req-unknown") for r in reasons))

        # --- monitor ---
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        monitor = run / "monitor" / "redis_monitor.jsonl"
        lines = monitor.read_text(encoding="utf-8").splitlines()
        samples = [l for l in lines if "redis_monitor_sample" in l]
        broken = [l for l in lines if "redis_monitor_start" in l]
        # single sample only
        (run / "monitor" / "redis_monitor.jsonl").write_text(
            "\n".join(broken + samples[:1] + [lines[-1]]) + "\n", encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("monitor_single_sample_rejects", not ready and any(
            r.startswith("monitor_samples_insufficient") for r in reasons))

        # gap exceeded
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        monitor = run / "monitor" / "redis_monitor.jsonl"
        recs = [json.loads(l) for l in monitor.read_text(encoding="utf-8").splitlines()]
        for r in recs:
            if r.get("event") == "redis_monitor_sample" and r["sample_mono_ms"] == 600:
                r["sample_mono_ms"] = 5000
        monitor.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("monitor_gap_rejects", not ready and any(
            r.startswith("monitor_gap_exceeded") for r in reasons))

        # evicted increase
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        monitor = run / "monitor" / "redis_monitor.jsonl"
        recs = [json.loads(l) for l in monitor.read_text(encoding="utf-8").splitlines()]
        recs[-2]["evicted_keys"] = 1
        monitor.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("monitor_eviction_increase_rejects", not ready and any(
            r.startswith("monitor_eviction_increase") for r in reasons))

        # run_id change
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        monitor = run / "monitor" / "redis_monitor.jsonl"
        recs = [json.loads(l) for l in monitor.read_text(encoding="utf-8").splitlines()]
        for r in recs:
            if r.get("event") == "redis_monitor_sample" and r["sample_mono_ms"] == 800:
                r["redis_run_id"] = "different"
        monitor.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("monitor_restart_rejects", not ready and any(
            r.startswith("monitor_run_id_changed") for r in reasons))

        # bad sample status
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        monitor = run / "monitor" / "redis_monitor.jsonl"
        recs = [json.loads(l) for l in monitor.read_text(encoding="utf-8").splitlines()]
        recs[-2]["status"] = "info_unreadable"
        monitor.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("monitor_bad_sample_rejects", not ready and any(
            r.startswith("monitor_sample_not_ok") for r in reasons))

        # cache_gauge cannot substitute for monitor evidence
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = [e for e in manifest["files"] if e["kind"] != "monitor"]
        _write_manifest(manifest_path, manifest)
        ready, reasons = validate_run(run, manifest_path)
        check("cache_gauge_not_monitor_evidence", not ready and any(
            r.startswith("monitor_start_missing") for r in reasons))

        # fatal marker and backend error
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        fatal = run / FATAL_DIR
        fatal.mkdir(parents=True)
        (fatal / "node1.backend-indeterminate").write_text("x\n", encoding="utf-8")
        ready, reasons = validate_run(run, manifest_path)
        check("fatal_marker_rejects", not ready and any(
            r.startswith("fatal_marker:") for r in reasons))

        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        trace = run / "traces" / "node1.jsonl"
        trace.write_text(trace.read_text(encoding="utf-8")
                         + json.dumps({"event": "cache_backend_error",
                                       "reason": "replica_put_indeterminate",
                                       "category": "indeterminate",
                                       "fatal": "1"}) + "\n", encoding="utf-8")
        _rehash_manifest(run, manifest_path)
        ready, reasons = validate_run(run, manifest_path)
        check("backend_error_rejects", not ready and any(
            r.startswith("backend_error:") for r in reasons))

        # validator never rewrites evidence
        run, manifest_path, index, payload_hash = _fixture(Path(tmp))
        before = _sha256(run / "traces" / "node1.jsonl")
        validate_run(run, manifest_path)
        after = _sha256(run / "traces" / "node1.jsonl")
        check("validator_does_not_rewrite_evidence", before == after)

    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend-gate", metavar="RUN_DIR")
    parser.add_argument("--run", metavar="RUN_DIR")
    parser.add_argument("--manifest", metavar="MANIFEST")
    parser.add_argument("--self-test", action="store_true")
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