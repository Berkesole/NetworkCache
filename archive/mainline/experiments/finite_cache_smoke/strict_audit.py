#!/usr/bin/env python3
"""Shared strict delivery audit: planned request <-> unique canonical completion
<-> unique delivery-audit row.

This mirrors the rules already enforced by
experiments/concurrency_finite_cache_v1/validate_run.py (which is NOT modified):
no ID-set shortcuts, no event-count-as-delivery, every field checked against the
frozen plan, unknown/duplicate/conflicting records rejected, and corrupt rows
rejected rather than skipped.

The NC and TCP paths use the SAME completion/audit row schema (verified against
the application and tcp_node.py writers):

  index \\t node \\t key \\t status \\t value_size \\t actual_hash \\t start \\t end
  \\t request_id \\t via \\t schedule_hash \\t provider \\t origin_query_uid
"""

from __future__ import annotations

import re

HEX16 = re.compile(r"^[0-9a-f]{16}$")
MIN_FIELDS = 6


def parse_tsv_strict(path) -> tuple[list[list[str]], list[str]]:
    rows, problems = [], []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return rows, [f"unreadable:{path.name}:{exc}"]
    for lineno, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        fields = [f.strip() for f in line.split("\t")]
        if len(fields) < MIN_FIELDS:
            problems.append(f"short_row:{path.name}:{lineno}")
            continue
        rows.append(fields)
    return rows, problems


def check_delivery(planned: list[dict], completion_entries: list, audit_paths: list):
    """planned: [{index,node,key,value_bytes,expected_fnv1a64}]
    completion_entries: [(request_index, path)] -- a LIST so two files mapping
    the same request are detected instead of silently overwritten.
    audit_paths: [path]"""
    problems: list[str] = []
    completion_by_index: dict[str, list] = {}
    for index, path in completion_entries:
        completion_by_index.setdefault(index, []).append(path)

    planned_indexes = {r["index"] for r in planned}
    for index, paths in completion_by_index.items():
        if index not in planned_indexes:
            problems.append(f"unknown_request_completion:{index}")
        if len(paths) != 1:
            problems.append(f"duplicate_completion_mapping:{index}:{len(paths)}")

    audit_rows: list[list[str]] = []
    for path in audit_paths:
        rows, row_problems = parse_tsv_strict(path)
        problems.extend(row_problems)
        audit_rows.extend(rows)

    counts = {"completion_ok": 0, "audit_ok": 0}
    for request in planned:
        index = request["index"]
        paths = completion_by_index.get(index, [])
        if len(paths) != 1:
            problems.append(f"request_completion_file_missing:{index}")
            continue
        rows, row_problems = parse_tsv_strict(paths[0])
        problems.extend(row_problems)
        if len(rows) != 1:
            problems.append(f"request_completion_count:{index}:{len(rows)}")
            continue
        row = rows[0]
        got = row[:6]
        if got[0] != index:
            problems.append(f"completion_wrong_index:{index}:{got[0]}")
        if got[1] != str(request["node"]):
            problems.append(f"completion_wrong_node:{index}")
        if got[2] != request["key"]:
            problems.append(f"completion_wrong_key:{index}")
        if got[3] != "ok":
            problems.append(f"completion_status_not_ok:{index}:{got[3]}")
        if got[4] != str(request["value_bytes"]):
            problems.append(f"completion_wrong_size:{index}")
        if not HEX16.match(got[5] or ""):
            problems.append(f"completion_hash_invalid:{index}")
        elif got[5] != request["expected_fnv1a64"]:
            problems.append(f"content_hash_mismatch:{index}")
        else:
            counts["completion_ok"] += 1

        matching = [r for r in audit_rows if r[0] == index]
        if len(matching) != 1:
            problems.append(f"audit_completion_count:{index}:{len(matching)}")
            continue
        arow = matching[0][:6]
        if arow[1] != str(request["node"]):
            problems.append(f"audit_wrong_node:{index}")
        if arow[2] != request["key"]:
            problems.append(f"audit_wrong_key:{index}")
        if arow[3] != "ok":
            problems.append(f"audit_status_not_ok:{index}:{arow[3]}")
        if arow[4] != str(request["value_bytes"]):
            problems.append(f"audit_wrong_size:{index}")
        if arow[5] != request["expected_fnv1a64"]:
            problems.append(f"audit_hash_mismatch:{index}")
        else:
            counts["audit_ok"] += 1

    for row in audit_rows:
        if row[0] not in planned_indexes:
            problems.append(f"unknown_audit_completion:{row[0]}")

    return (not problems), problems, counts


def _self_test() -> int:
    import tempfile
    from pathlib import Path
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        print(f"{name}: {'PASS' if ok else 'FAIL'}{' ' + detail if detail and not ok else ''}")
        if not ok:
            failures += 1

    h = "0123456789abcdef"
    planned = [{"index": "1", "node": 1, "key": "k1", "value_bytes": 64,
                "expected_fnv1a64": h}]

    def build(row_over=None, audit=None):
        tmp = Path(tempfile.mkdtemp(prefix="strict-audit-"))
        row = ["1", "1", "k1", "ok", "64", h]
        if row_over:
            row = [row_over.get(i, v) for i, v in enumerate(row)]
        comp = tmp / "1.tsv"
        comp.write_text("\t".join(row) + "\n", encoding="utf-8")
        audit_path = tmp / "audit.tsv"
        audit_path.write_text((audit if audit is not None else "\t".join(row) + "\n"),
                              encoding="utf-8")
        return tmp, [("1", comp)], [audit_path]

    _, cf, ap = build()
    ok, problems, counts = check_delivery(planned, cf, ap)
    check("positive", ok, str(problems))
    check("counts", counts == {"completion_ok": 1, "audit_ok": 1}, str(counts))

    for field, pos, value, expect in [
        ("node", 1, "9", "completion_wrong_node"),
        ("key", 2, "x", "completion_wrong_key"),
        ("status", 3, "io_error", "completion_status_not_ok"),
        ("size", 4, "63", "completion_wrong_size"),
        ("hash", 5, "f" * 16, "content_hash_mismatch"),
    ]:
        _, cf, ap = build(row_over={pos: value})
        ok, problems, _ = check_delivery(planned, cf, ap)
        check(f"completion_{field}_tamper_rejected",
              not ok and any(p.startswith(expect) for p in problems), str(problems))
    # audit tamper on each field
    for pos, value, expect in [(1, "9", "audit_wrong_node"),
                               (2, "x", "audit_wrong_key"),
                               (3, "io_error", "audit_status_not_ok"),
                               (4, "63", "audit_wrong_size"),
                               (5, "f" * 16, "audit_hash_mismatch")]:
        row = ["1", "1", "k1", "ok", "64", h]
        row[pos] = value
        _, cf, ap = build(audit="\t".join(row) + "\n")
        ok, problems, _ = check_delivery(planned, cf, ap)
        check(f"audit_{expect}_rejected",
              not ok and any(p.startswith(expect) for p in problems), str(problems))

    # duplicate completion mapping
    _, cf, ap = build()
    ok, problems, _ = check_delivery(planned, [("1", cf[0][1]), ("1", cf[0][1])], ap)
    check("duplicate_mapping_rejected", not ok and any(
        p.startswith("duplicate_completion_mapping") for p in problems))
    # unknown request completion
    tmp, cf2, ap2 = build()
    ghost = tmp / "ghost.tsv"
    ghost.write_text("\t".join(["ghost", "1", "k1", "ok", "64", h]) + "\n", encoding="utf-8")
    ok, problems, _ = check_delivery(planned, cf2 + [("ghost", ghost)], ap2)
    check("unknown_completion_rejected", not ok and any(
        p.startswith("unknown_request_completion") for p in problems))
    # duplicate audit row
    _, cf, ap = build(audit=("\t".join(["1", "1", "k1", "ok", "64", h]) + "\n") * 2)
    ok, problems, _ = check_delivery(planned, cf, ap)
    check("duplicate_audit_rejected", not ok and any(
        p.startswith("audit_completion_count") for p in problems))
    # short/corrupt row
    _, cf, ap = build(audit="1\t1\tk1\n")
    ok, problems, _ = check_delivery(planned, cf, ap)
    check("short_row_rejected", not ok and any(
        p.startswith("short_row") for p in problems))
    return 0 if failures == 0 else 1


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    ap.error("choose --self-test")
    return 3


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))