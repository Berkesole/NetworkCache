"""Read and bind sealed monitor inputs; never trust an in-memory PASS flag."""
import json
from pathlib import Path


def load(root, record, identity):
    problems, series = [], {}
    root = Path(root).resolve()
    try:
        if record.get("schema") != "finite-cache-monitor-acceptance" or record.get("version") != 1:
            problems.append("monitor_schema")
        for field in ("run_id", "slot_id", "attempt", "transport"):
            if field not in identity or record.get(field) != identity[field]:
                problems.append(f"monitor_identity:{field}")
        expected = record["expected_instances"]
        if (not isinstance(expected, list) or not expected
                or any(type(n) is not int or n < 1 for n in expected)
                or len(expected) != len(set(expected))):
            raise ValueError("invalid expected_instances")
        keys = {str(n) for n in expected}
        for field in ("instances", "baseline", "exit_codes"):
            if set(record[field]) != keys:
                problems.append(f"monitor_instance_set:{field}")
        refs = set()
        for node in expected:
            info = record["instances"][str(node)]
            rel = info["raw_file"]
            path = (root / rel).resolve()
            if (not isinstance(rel, str) or Path(rel).is_absolute()
                    or not path.is_relative_to(root) or rel != f"monitor/node{node}.jsonl"
                    or path in refs):
                raise ValueError(f"unsafe or duplicate monitor reference:{node}")
            refs.add(path)
            rows = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"non-object monitor sample:{node}")
                if "node" in row and row["node"] != node:
                    problems.append(f"sample_node:{node}")
                if row.get("redis_run_id") and row.get("run_id") and row["redis_run_id"] != row["run_id"]:
                    problems.append(f"sample_run_id_conflict:{node}")
                row = dict(row, node=node, instance_identity=f"node{node}",
                           redis_run_id=row.get("redis_run_id") or row.get("run_id", ""))
                rows.append(row)
            if not rows:
                problems.append(f"empty_monitor:{node}")
            elif record["baseline"][str(node)] != rows[0]:
                problems.append(f"baseline_sample_mismatch:{node}")
            if info.get("baseline") != record["baseline"][str(node)]:
                problems.append(f"instance_baseline_mismatch:{node}")
            if rows and (info.get("first_sample") != rows[0] or info.get("last_sample") != rows[-1]):
                problems.append(f"boundary_sample_mismatch:{node}")
            series[node] = rows
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        problems.append(f"monitor_evidence_invalid:{exc}")
    return series, problems
