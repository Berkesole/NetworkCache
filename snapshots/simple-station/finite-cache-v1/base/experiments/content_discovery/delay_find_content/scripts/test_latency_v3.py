#!/usr/bin/env python3
"""Deterministic design checks for latency-v3."""

from __future__ import annotations

import json
import csv
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import analyze_latency_v3 as analysis
import generate_latency_v3 as gen
import latency_v3_tcp_node as tcp
import run_latency_v3_suite as suite


def measurement(data):
    return [row for row in data["requests"] if row["phase"] == "measurement"]


def test_controlled() -> None:
    first = gen.controlled_canonical(1)
    again = gen.controlled_canonical(1)
    other = gen.controlled_canonical(2)
    assert first == again and first != other
    rows = measurement(first)
    assert len(rows) == 128
    for round_index in range(1, 9):
        current = [row for row in rows if row["round"] == round_index]
        assert Counter(row["signature"][2] for row in current) == {1: 4, 2: 4, 3: 4, 4: 4}
        assert Counter(row["signature"][0] for row in current) == {"G": 4, "O": 12}
    key_roles = {}
    for row in rows:
        key_roles.setdefault(row["key"], row["signature"][1])
        assert key_roles[row["key"]] == row["signature"][1]
    assert Counter(key_roles.values()) == {"G": 4, "O": 12}


def test_iid(workload: str) -> None:
    data = gen.iid_canonical(workload, 1)
    rows = measurement(data)
    assert len(rows) == 240
    for bin_index in range(1, 13):
        current = [row for row in rows if row["bin"] == bin_index]
        assert Counter(row["signature"][2] for row in current) == {1: 5, 2: 5, 3: 5, 4: 5}
        assert Counter(row["signature"][0] for row in current) == {"G": 5, "O": 15}
        assert Counter(row["signature"][1] for row in current) == {"G": 5, "O": 15}


def test_mapping() -> None:
    for seed in gen.SEEDS:
        for workload in ("controlled", "iid-zipf", "iid-uniform"):
            canonical = gen.canonical(workload, seed)
            canonical["canonical_sha256"] = gen.json_hash(canonical)
            left = gen.map_schedule("kua-dc-v3-16", canonical, seed)
            right = gen.map_schedule("kua-dc-v3-32", canonical, seed)
            for a, b in zip(left["requests"], right["requests"]):
                assert a["key"] == b["key"]
                assert a["request_index"] == b["request_index"]
                assert a["requester"] != a["owner"]
                assert b["requester"] != b["owner"]
                signature_fields = ("requester_role", "owner_role", "original_owner_overlay_hops",
                                    "overlay_edge_physical_signature")
                assert tuple(a[field] for field in signature_fields) == tuple(b[field] for field in signature_fields)


def test_catalog_and_data() -> None:
    catalog = gen.collision_free_catalog()
    assert len(catalog) == len(set(catalog)) == 80
    helper = Path(gen.__file__).with_name("bloom_hash_slots.cpp")
    binary = Path(tempfile.gettempdir()) / "networkcache-latency-v3-test-slots"
    subprocess.run(["c++", "-std=c++20", "-O2", str(helper), "-o", str(binary)], check=True)
    result = subprocess.run([str(binary), *catalog], text=True, capture_output=True, check=True)
    assert len({line.split("\t")[1] for line in result.stdout.splitlines()}) == 80
    value = tcp.deterministic_value(65536, 123)
    assert b"\0" in value and b"\n" in value and b"\t" in value
    assert tcp.fnv1a64(value) == gen.content_hash(65536, 123)


def test_quality_and_selection() -> None:
    items = [(Path("attempt001"), True, "ok"), (Path("attempt002"), False, "bad"),
             (Path("attempt003"), True, "ok"), (Path("attempt004"), True, "ok")]
    assert suite.select_earliest(items) == [Path("attempt001"), Path("attempt003")]
    controlled = []
    for round_index in range(1, 9):
        count = 15 if round_index == 1 else 16
        controlled.extend({"phase": "measurement", "trace_complete": "1", "round_index": str(round_index)} for _ in range(count))
    assert suite.trace_coverage_ok(controlled, "controlled", 128)
    assert not suite.trace_coverage_ok(controlled[:-1], "controlled", 128)
    iid = []
    for bin_index in range(1, 13):
        count = 19 if bin_index <= 2 else 20
        iid.extend({"phase": "measurement", "trace_complete": "1", "request_bin": str(bin_index)} for _ in range(count))
    assert suite.trace_coverage_ok(iid, "iid-zipf", 240)
    assert not suite.trace_coverage_ok(iid[:-1], "iid-zipf", 240)

    # A Docker build never reached the experiment and therefore must remain
    # auditable without consuming the four-attempt runtime quota.
    original_root = suite.RESULTS_ROOT
    with tempfile.TemporaryDirectory() as temporary:
        suite.RESULTS_ROOT = Path(temporary)
        try:
            config = suite.CONFIGS[0]
            seed_root = suite.RESULTS_ROOT / "attempts" / config.slug / "seed001"
            build_failure = seed_root / "attempt001"
            build_failure.mkdir(parents=True)
            (build_failure / "metadata.json").write_text('{"status":"driver_failed"}\n')
            (build_failure / "driver.stderr.log").write_text("FAIL: docker build\n")
            runtime_failure = seed_root / "attempt002"
            runtime_failure.mkdir()
            (runtime_failure / "metadata.json").write_text('{"status":"incomplete"}\n')
            (runtime_failure / "driver.stderr.log").write_text("controller timeout\n")
            assert suite.is_build_failure(build_failure)
            assert not suite.is_build_failure(runtime_failure)
            assert suite.quota_run_dirs(config, 1) == [runtime_failure]
            assert suite.next_attempt(config, 1)[0] == 3
        finally:
            suite.RESULTS_ROOT = original_root


def test_aggregation() -> None:
    assert analysis.topology_host_count(16) == 16
    assert analysis.topology_host_count("32") == 32
    assert analysis.topology_host_count("kua-dc-v3-16") == 16
    try:
        analysis.topology_host_count("kua-dc-v3-64")
        raise AssertionError("unsupported topology was accepted")
    except ValueError:
        pass
    rows = []
    for seed in range(1, 11):
        rows.extend([
            {"config": "c", "seed": seed, "repetition": 1, "period": "early", **{f"{m}_mean": 10.0 for m in analysis.METRICS}},
            {"config": "c", "seed": seed, "repetition": 2, "period": "early", **{f"{m}_mean": 100.0 for m in analysis.METRICS}},
        ])
    seeds = analysis.seed_aggregate(rows, "period")
    assert len(seeds) == 10 and all(row["end_to_end_latency_ns_mean"] == 55.0 for row in seeds)
    overall = analysis.overall_aggregate(seeds, "period")
    assert overall[0]["seed_count"] == 10 and overall[0]["end_to_end_latency_ns_mean"] == 55.0
    metadata = {config.slug: {"workload": config.workload, "topology": config.topology,
                             "method": config.method, "value_size_bytes": config.size}
                for config in suite.CONFIGS}
    period_rows = []
    for config_index, config in enumerate(suite.CONFIGS, 1):
        for seed in range(1, 11):
            for period in ("early", "late", "all"):
                value = float(config_index * 100 + seed)
                period_rows.append({"config": config.slug, "seed": seed, "period": period,
                                    **{f"{metric}_mean": value for metric in analysis.METRICS}})
    paired, sizes = analysis.paired_comparisons(period_rows, metadata)
    assert paired and sizes and all(row["paired_seed_count"] == 10 for row in paired)


def test_controller() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); control = root / "control"; output = root / "results.csv"; schedule = root / "schedule.tsv"
        columns = ["request_index", "phase", "round", "bin", "key", "key_rank", "requester", "owner",
                   "requester_role", "owner_role", "original_owner_overlay_hops", "overlay_edge_physical_signature",
                   "value_size_bytes", "content_seed", "expected_fnv1a64", "schedule_sha256"]
        schedule.parent.mkdir(parents=True, exist_ok=True)
        with schedule.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t"); writer.writeheader()
            for index, requester in enumerate((1, 2, 1), 1):
                writer.writerow({"request_index": index, "phase": "measurement", "round": 1, "bin": 1,
                                 "key": f"k{index}", "key_rank": index, "requester": requester, "owner": 2 if requester == 1 else 1,
                                 "requester_role": "O", "owner_role": "O", "original_owner_overlay_hops": 1,
                                 "overlay_edge_physical_signature": "S", "value_size_bytes": 1, "content_seed": 1,
                                 "expected_fnv1a64": "af", "schedule_sha256": "hash"})
        active = 0; maximum = 0; lock = threading.Lock()
        def worker(node: int) -> None:
            nonlocal active, maximum
            (control / "ready").mkdir(parents=True, exist_ok=True)
            (control / "ready" / f"node{node}.ready").write_text("ready\n")
            seen = set()
            while not (control / "stop").exists():
                directory = control / "commands" / f"node{node}"
                for path in sorted(directory.glob("*.tsv")) if directory.exists() else []:
                    if path in seen: continue
                    seen.add(path); fields = path.read_text().strip().split("\t")
                    with lock: active += 1; maximum = max(maximum, active)
                    now = time.monotonic_ns(); time.sleep(.002)
                    completion = "\t".join([fields[0], str(node), fields[2], "ok", fields[4], fields[5], str(now), str(now+1000), "1", "fake", fields[6]]) + "\n"
                    target = control / "completions" / f"{fields[0]}.tsv"; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(completion)
                    with lock: active -= 1
                markers = sorted((control / "quiesce").glob("*.marker")) if (control / "quiesce").exists() else []
                if markers:
                    index = markers[-1].stem
                    ack = control / "quiesced" / f"{index}-node{node}.ack"
                    if not ack.exists():
                        time.sleep(.003); ack.parent.mkdir(parents=True, exist_ok=True); ack.write_text("quiet\n")
                time.sleep(.001)
            (control / "stopped").mkdir(parents=True, exist_ok=True)
            (control / "stopped" / f"node{node}.stopped").write_text("stopped\n")
        threads = [threading.Thread(target=worker, args=(node,)) for node in (1, 2)]
        for thread in threads: thread.start()
        command = [sys.executable, str(Path(__file__).with_name("latency_v3_controller.py")), "--schedule", str(schedule),
                   "--control-dir", str(control), "--host-count", "2", "--method", "nc", "--think-ms", "1", "--output", str(output)]
        subprocess.run(command, check=True)
        for thread in threads: thread.join(timeout=2)
        assert maximum == 1 and len(list(csv.DictReader(output.open()))) == 3


def test_report_generator() -> None:
    def write(path: Path, rows) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); analysis_dir = root / "analysis"; output = root / "report"; figures = root / "figures"
        write(root / "run_status.csv", [{"status": "READY", "selected_runs": "20"} for _ in range(19)])
        write(root / "latency_selection.csv", [{"run": index} for index in range(380)])
        write(analysis_dir / "sensitivity.csv", [{"within_5_percent": "1"}])
        group_rows = []
        for config in ("controlled-16-nc-b65536", "controlled-32-nc-b65536",
                       "controlled-16-known-owner-b65536", "controlled-32-known-owner-b65536"):
            for group in range(1, 9):
                group_rows.append({"config": config, "group_index": group, "seed_count": 10,
                                   "end_to_end_latency_ns_mean": 1_000_000 + group,
                                   "end_to_end_latency_ns_ci95_low": 900_000,
                                   "end_to_end_latency_ns_ci95_high": 1_100_000})
        write(analysis_dir / "overall_group_mean_ci95.csv", group_rows)
        period_rows = []
        for method in ("nc", "known-owner", "tcp-direct"):
            for size in (1024, 65536, 1048576):
                period_rows.append({"config": f"controlled-32-{method}-b{size}", "period": "all", "seed_count": 10,
                                    "end_to_end_latency_ns_mean": size * 10,
                                    "end_to_end_latency_ns_ci95_low": size * 9,
                                    "end_to_end_latency_ns_ci95_high": size * 11,
                                    "discovery_latency_ns_mean": size * 2,
                                    "return_path_latency_ns_mean": size * 8})
        for topology in (16, 32):
            for period, value in (("early", 100_000_000), ("late", 50_000_000)):
                period_rows.append({"config": f"controlled-{topology}-nc-b65536", "period": period, "seed_count": 10,
                                    "end_to_end_latency_ns_mean": value,
                                    "end_to_end_latency_ns_ci95_low": value * .9,
                                    "end_to_end_latency_ns_ci95_high": value * 1.1,
                                    "discovery_latency_ns_mean": value * .4,
                                    "return_path_latency_ns_mean": value * .6})
        write(analysis_dir / "overall_early_late_ci95.csv", period_rows)
        effect_specs = [
            ("scaling_32_minus_16", "controlled-16-nc-b65536", "controlled-32-nc-b65536", "late"),
            ("scaling_32_minus_16", "iid-zipf-16-nc-b65536", "iid-zipf-32-nc-b65536", "late"),
            ("scaling_32_minus_16", "iid-uniform-16-nc-b65536", "iid-uniform-32-nc-b65536", "late"),
            ("uniform_minus_zipf", "iid-zipf-16-nc-b65536", "iid-uniform-16-nc-b65536", "late"),
            ("uniform_minus_zipf", "iid-zipf-32-nc-b65536", "iid-uniform-32-nc-b65536", "late"),
            ("known_owner_minus_nc", "controlled-16-nc-b65536", "controlled-16-known-owner-b65536", "late"),
            ("known_owner_minus_nc", "controlled-32-nc-b65536", "controlled-32-known-owner-b65536", "late"),
            ("size_1048576_minus_1024", "controlled-32-nc-b1024", "controlled-32-nc-b1048576", "all"),
            ("size_1048576_minus_1024", "controlled-32-known-owner-b1024", "controlled-32-known-owner-b1048576", "all"),
            ("size_1048576_minus_1024", "controlled-32-tcp-direct-b1024", "controlled-32-tcp-direct-b1048576", "all"),
        ]
        effects = [{"comparison": name, "left_config": left, "right_config": right, "period": period,
                    "metric": "end_to_end_latency_ns", "paired_seed_count": 10,
                    "difference_mean": 100_000, "difference_ci95_low": 50_000,
                    "difference_ci95_high": 150_000, "ratio_mean": 1.1,
                    "ratio_ci95_low": 1.0, "ratio_ci95_high": 1.2}
                   for name, left, right, period in effect_specs]
        write(analysis_dir / "paired_effects.csv", effects)
        write(analysis_dir / "size_effects.csv", [{**effects[-1], "left_size_bytes": 1024,
                                                    "right_size_bytes": 1048576, "slope_ns_per_byte": 1,
                                                    "slope_ns_per_log2_size": 1}])
        write(analysis_dir / "controlled_convergence.csv",
              [{"config": "controlled-32-nc-b65536", "seed": seed,
                "requests_to_converge_mean": 33} for seed in range(1, 11)])
        script = Path(__file__).resolve().parents[3] / "reports/generate_latency_v3_report.py"
        subprocess.run([sys.executable, str(script), "--results-root", str(root), "--output-dir", str(output),
                        "--figure-dir", str(figures), "--latency-only-test-mode"], check=True)
        for filename in ("overall.pdf", "overall.en.pdf"):
            data = (output / filename).read_bytes()
            assert data.startswith(b"%PDF") and len(data) > 10_000


def main() -> int:
    test_controlled()
    test_iid("iid-zipf")
    test_iid("iid-uniform")
    test_mapping()
    test_catalog_and_data()
    test_quality_and_selection()
    test_aggregation()
    test_controller()
    test_report_generator()
    assert gen.content_hash(1024, 123) == gen.content_hash(1024, 123)
    assert gen.content_hash(1024, 123) != gen.content_hash(1024, 124)
    print("latency-v3 generator tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
