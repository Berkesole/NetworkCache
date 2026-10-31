#!/usr/bin/env python3
"""Deterministic design, occupancy, and selection tests for federation-v2."""

from __future__ import annotations

import importlib.util
import csv
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module); return module


gen = load("generate_federation_v2")


def test_generator() -> None:
    serializations = []
    for seed in gen.SEEDS:
        repeated_a = gen.make(seed, "repeated"); repeated_b = gen.make(seed, "repeated")
        uniform = gen.make(seed, "uniform")
        assert json.dumps(repeated_a, sort_keys=True) == json.dumps(repeated_b, sort_keys=True)
        serializations.append(json.dumps(repeated_a, sort_keys=True))
        rows = repeated_a["rows"]
        pre = [row for row in rows if row["phase"] == "pre"]
        remote = [row for row in rows if row["request_class"] == "remote"]
        local_control = [row for row in rows if row["request_class"] == "local_control"]
        assert (len(rows), len(pre), len(remote), len(local_control)) == (320, 128, 128, 64)
        assert all(gen.cluster(row["requester"]) == gen.cluster(row["owner"]) for row in pre + local_control)
        assert all(gen.cluster(row["requester"]) != gen.cluster(row["owner"]) for row in remote)
        assert all(row["requester"] != row["owner"] for row in rows)
        assert all(int(row["original_owner_overlay_hops"]) >= 1 for row in rows)
        assert all(row["overlay_edge_physical_signature"] for row in rows)
        assert all("cross_cluster" not in row["overlay_edge_physical_signature"] for row in pre + local_control)
        assert all("cross_cluster" in row["overlay_edge_physical_signature"] for row in remote)
        for round_index in range(1, 9):
            assert sum(row["phase"] == "pre" and row["round"] == round_index for row in rows) == 16
            assert sum(row["request_class"] == "remote" and row["round"] == round_index for row in rows) == 16
            assert sum(row["request_class"] == "local_control" and row["round"] == round_index for row in rows) == 8
        repeated_counts = Counter((gen.cluster(row["owner"]), row["key"]) for row in remote)
        assert len(repeated_counts) == 32 and set(repeated_counts.values()) == {4}
        uniform_remote = [row for row in uniform["rows"] if row["request_class"] == "remote"]
        assert all(1 <= row["key_rank"] <= 32 for row in uniform_remote)
        rep_slots = [(row["phase"], row["round"], row["request_class"], row["direction"],
                      row["requester"], row["owner"], row["original_owner_overlay_hops"],
                      row["overlay_edge_physical_signature"]) for row in rows]
        uni_slots = [(row["phase"], row["round"], row["request_class"], row["direction"],
                      row["requester"], row["owner"], row["original_owner_overlay_hops"],
                      row["overlay_edge_physical_signature"]) for row in uniform["rows"]]
        assert rep_slots == uni_slots
        keys = {"local": {row["key"] for row in pre},
                "repeated": {row["key"] for row in remote},
                "uniform": {row["key"] for row in uniform_remote},
                "probe": {row["key"] for row in repeated_a["isolation_probes"]}}
        for left, left_keys in keys.items():
            for right, right_keys in keys.items():
                if left < right: assert left_keys.isdisjoint(right_keys)
    assert len(set(serializations)) == 10
    assert len(gen.collision_free_catalog()) == len(set(gen.collision_free_catalog())) == 130
    scenario = gen.render_scenario()
    assert "- id: 64" in scenario
    node1 = scenario.split("- id: 1\n", 1)[1].split("\n", 2)[0]
    assert "33" not in node1


def test_cache_probe() -> None:
    probe = load("federation_v2_cache_probe")
    values = {"owner-key": 65536, "cached-key": 65536}
    def fake_redis(*arguments: str) -> str:
        if arguments[0] == "SCAN": return "0\nowner-key\ncached-key"
        if arguments[0] == "STRLEN": return str(values[arguments[1]])
        if arguments[:2] == ("MEMORY", "USAGE"): return "65600"
        if arguments[:2] == ("INFO", "memory"): return "used_memory:150000\n"
        raise AssertionError(arguments)
    probe.redis = fake_redis
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); marker = root / "ready.tsv"; output = root / "out.tsv"
        marker.write_text("ready\tready\t0\n")
        probe.snapshot(1, {"owner-key"}, marker, output)
        fields = output.read_text().strip().split("\t")
        assert fields[6:12] == ["2", "131072", "1", "65536", "1", "65536"]


def test_selection_order() -> None:
    runner = load("run_federation_v2_suite")
    config = runner.CONFIGS[0]
    paths = [Path(f"attempt{index:03d}") for index in range(1, 5)]
    runner.dirs = lambda _config, _seed: paths
    accepted = {"attempt001", "attempt003", "attempt004"}
    runner.validate = lambda _config, path: (path.name in accepted, "fixture")
    assert [path.name for path in runner.selected(config, 1)] == ["attempt001", "attempt003"]


def test_analysis_classification() -> None:
    analysis = load("analyze_federation_v2")
    schedule = gen.make(1, "repeated")["rows"]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "activation.json").write_text(json.dumps({"activation_apply_latency_ns": 50}))
        request_columns = ["request_index", "phase", "round", "request_class", "direction", "key",
                           "end_to_end_latency_ns", "activation_to_request_start_ns",
                           "activation_to_remote_data_ns", "federation_ready_ns", "status"]
        trace_columns = ["request_index", "phase", "round", "request_class", "direction", "key",
                         "end_to_end_latency_ns", "discovery_latency_ns", "return_path_latency_ns",
                         "request_forward_events", "request_frame_bytes", "serving_overlay_hops",
                         "cross_link_request_frames", "cross_link_data_frames", "service_class",
                         "app_receive_mono_ns"]
        requests, traces = [], []
        seen_remote = set()
        for row in schedule:
            latency = 1000 + row["round"] * 10
            request = {**row, "end_to_end_latency_ns": latency,
                       "activation_to_request_start_ns": latency,
                       "activation_to_remote_data_ns": latency + 100 if row["phase"] == "post" else "",
                       "federation_ready_ns": 100, "status": "ok"}
            requests.append(request)
            remote_id = (row["direction"], row["key"])
            service = "original_owner"
            if row["request_class"] == "remote" and remote_id in seen_remote: service = "cache"
            if row["request_class"] == "remote": seen_remote.add(remote_id)
            traces.append({**row, "end_to_end_latency_ns": latency, "discovery_latency_ns": 400,
                           "return_path_latency_ns": latency - 400, "request_forward_events": 3,
                           "request_frame_bytes": 474, "serving_overlay_hops": 4,
                           "cross_link_request_frames": int(row["request_class"] == "remote"),
                           "cross_link_data_frames": int(row["request_class"] == "remote"),
                           "service_class": service, "app_receive_mono_ns": latency + 100})
        for path, columns, rows in ((root / "request_results.csv", request_columns, requests),
                                    (root / "trace_breakdown.csv", trace_columns, traces)):
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                writer.writeheader(); writer.writerows(rows)
        selected = {"config": "repeated-nc", "workload": "repeated", "method": "nc",
                    "seed": "1", "selection_index": "1", "run_dir": str(root)}
        trajectory, metrics, service, occupancy = analysis.run_outputs(selected)
        assert len(trajectory) == 72 and len(metrics) == 3 and len(service) == 24 and not occupancy
        both = next(row for row in metrics if row["direction"] == "both")
        assert both["activation_apply_latency_ns"] == 50
        assert both["cold_remote_latency_ns"] != "" and both["cache_assisted_latency_ns"] != ""
        aggregated = analysis.seed_aggregate(
            [{"config": "x", "seed": "1", "value": 10}, {"config": "x", "seed": "1", "value": 30}],
            ("config", "seed"), ("value",))
        assert aggregated[0]["value"] == 20


def write_csv(path: Path, rows: list[dict], delimiter: str = ",") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=delimiter)
        writer.writeheader(); writer.writerows(rows)


def test_validator_boundaries() -> None:
    runner = load("run_federation_v2_suite")
    config = runner.CONFIGS[0]
    made = gen.make(1, "repeated"); schedule = []
    for row in made["rows"]:
        schedule.append({**row, "requester_cluster": gen.cluster(row["requester"]),
                         "owner_cluster": gen.cluster(row["owner"]), **gen.descriptor(row["key"]),
                         "schedule_sha256": "fixture"})
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "metadata.json").write_text(json.dumps({"status": "ok", "wire_version": 2}))
        (root / "activation.json").write_text(json.dumps({"federation_ready_ns": 10, "activation_apply_latency_ns": 1}))
        write_csv(root / "schedule.tsv", schedule, "\t")
        request_rows = []
        for index, row in enumerate(schedule):
            request_rows.append({**row, "status": "ok", "actual_value_bytes": "65536",
                                 "actual_fnv1a64": row["expected_fnv1a64"], "quiescence_ack_count": "64",
                                 "request_start_mono_ns": str(index * 20 + 100),
                                 "app_receive_mono_ns": str(index * 20 + 110),
                                 "activation_to_remote_data_ns": "100" if row["phase"] == "post" else ""})
        write_csv(root / "request_results.csv", request_rows)
        write_csv(root / "isolation_results.csv", [
            {"status": "ok", "completion_present": "0", "quiescence_ack_count": "64"},
            {"status": "ok", "completion_present": "0", "quiescence_ack_count": "64"}])
        (root / "completion_summary.json").write_text(json.dumps({"completed": 320, "completion_rate_issued": 1.0,
            "completion_rate_schedule": 1.0, "issued": 320, "scheduled": 320}))
        for node in (1, 33):
            path = root / "activation_acks" / f"node{node}.tsv"; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{node}\tpeer\tok\t100\n")
        write_csv(root / "clock_state.tsv", [{"node": node, "monotonic_ns": 100,
                  "timens_offsets_sha256": "same"} for node in range(1, 65)])
        trace_rows = [{"request_index": row["request_index"], "phase": row["phase"], "round": row["round"],
                       "request_class": row["request_class"], "direction": row["direction"], "trace_complete": "1",
                       "cross_link_request_frames": "0" if row["phase"] == "pre" else "1",
                       "cross_link_data_frames": "0" if row["phase"] == "pre" else "1"} for row in schedule]
        write_csv(root / "trace_breakdown.csv", trace_rows)
        snapshot_ids = [f"s{index:02d}" for index in range(20)]
        occupancy = [{"snapshot_id": snapshot, "node": node, "cached_copy_keys": 1,
                      "cached_copy_logical_bytes": 65536, "redis_used_memory_bytes": 70000}
                     for snapshot in snapshot_ids for node in range(1, 65)]
        write_csv(root / "cache_occupancy.csv", occupancy)
        logs = root / "logs"; logs.mkdir()
        for node in range(1, 65): (logs / f"node{node}.log").write_text("cache backend: redis\n")
        devices = list(range(1, 65)) + list(range(101, 109)) + list(range(201, 205)) + list(range(301, 309)) + list(range(401, 405))
        (root / "network_state.txt").write_text("".join(
            f"## node{node}\nmtu 1500\nqdisc netem delay 2ms rate 1Gbit\nrate 1Gbit\n" for node in devices))
        generated = root / "generated_network"; generated.mkdir()
        (generated / "node1.network.sh").write_text("ip link set dev eth0 down\n")
        (generated / "node33.network.sh").write_text("ip link set dev eth0 down\n")
        assert runner.validate(config, root) == (True, "ok")
        request_rows[0]["status"] = "timeout"; write_csv(root / "request_results.csv", request_rows)
        assert runner.validate(config, root)[1] == "request_status"
        request_rows[0]["status"] = "ok"; write_csv(root / "request_results.csv", request_rows)
        (root / "activation_acks/node33.tsv").unlink()
        assert runner.validate(config, root)[1] == "activation_acks"


def main() -> int:
    test_generator(); test_cache_probe(); test_selection_order(); test_analysis_classification(); test_validator_boundaries()
    print("federation-v2 tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
