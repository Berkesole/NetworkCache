#!/usr/bin/env python3
from __future__ import annotations

import json
import csv
import socket
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path

import border_monitor
import analyze_results
import cross_host
import decompose_latency as decomposition
import domain_runner
import generate_workloads
import queue_runner
import spec
import validate_attempt
from control_agent import AgentState


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


class SpecTests(unittest.TestCase):
    def test_border_pre_isolation_blocks_default_and_checks_all_remote_routes(self):
        from distributed_controller import LocalStore
        with tempfile.TemporaryDirectory() as directory:
            for role, border, gateway, first in [('domain-a', 250, '172.29.250.1', 33),
                                                  ('domain-b', 450, '172.29.251.1', 1)]:
                store = LocalStore(Path(directory), role, border, gateway)
                routes = [{'dst': f'10.90.0.{n}/32', 'type': 'unreachable'}
                          for n in range(first, first + 32)]
                routes.append({'dst': 'default', 'gateway': gateway})
                with mock.patch.object(store, '_container', return_value='border'), \
                     mock.patch('distributed_controller.subprocess.run') as run:
                    run.return_value.stdout = json.dumps(routes)
                    store.deactivate()
                    self.assertEqual(len(run.call_args_list), 33)
                    for call in run.call_args_list[:32]:
                        self.assertIn('unreachable', call.args[0])
                        self.assertTrue(call.kwargs['check'])
                    run.reset_mock()
                    run.return_value.returncode = 0
                    store.activate()
                    self.assertEqual(len(run.call_args_list), 32)
                    for call in run.call_args_list:
                        self.assertIn('replace', call.args[0])
                        self.assertIn(gateway, call.args[0])
                    run.return_value.stdout = json.dumps(routes[1:])
                    with self.assertRaisesRegex(RuntimeError, 'incomplete PRE'):
                        store.deactivate()

    def test_helper_options_are_after_kathara_separator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(domain_runner.subprocess, 'Popen') as popen:
                helpers = domain_runner.launch_helpers(
                    SimpleNamespace(role='domain-a', method='tcp-direct'), root, root, 'hash', root)
                for call in popen.call_args_list:
                    argv = call.args[0]
                    self.assertLess(argv.index('--'), argv.index('--trace'))
                    self.assertEqual(argv[argv.index('--') + 1:argv.index('--') + 4],
                                     ['env', 'RSYNC_FEDERATION_ACTIVATION_MODE=logical-only', 'python3'])
                for _, handle in helpers:
                    handle.close()

    def test_versioned_repair_budget_is_four_attempts(self):
        self.assertEqual(queue_runner.execution_budget({}, 'slot'), (1, 4))
        campaign = {'execution_attempt_floors': {'slot': 5}}
        first, last = queue_runner.execution_budget(campaign, 'slot')
        self.assertEqual((first, last), (5, 8))
        self.assertEqual(last - first + 1, 4)
        self.assertGreater(queue_runner.next_attempt_index([Path('attempt008')], first), last)

    def test_real_tcp_helper_logical_activation_ack(self):
        import os
        import time
        import sys
        helper = REPO / 'experiments/content_discovery/delay_find_content/scripts/latency_v3_tcp_node.py'
        for node, peer in [(1, 33), (33, 1)]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data = root / 'data.tsv'
                data.write_text('key\tvalue_size_bytes\tcontent_seed\texpected_fnv1a64\n')
                (root / 'activation').mkdir()
                (root / 'activation' / f'node{node}.tsv').write_text(
                    f'{peer}\t10.90.0.{peer}\t44001\tbootstrap\texternal-border-route\thash\n')
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                process = subprocess.Popen([sys.executable, str(helper), str(node), str(data),
                    str(root), 'hash', '--host', '127.0.0.1', '--port-base', str(port-node)],
                    env={**os.environ, 'RSYNC_FEDERATION_ACTIVATION_MODE': 'logical-only'},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    ack = root / 'activated' / f'node{node}.tsv'
                    deadline = time.monotonic() + 5
                    while not ack.exists() and time.monotonic() < deadline and process.poll() is None:
                        time.sleep(0.02)
                    self.assertTrue(ack.exists())
                    self.assertEqual(ack.read_text().split('\t')[:3], [str(node), str(peer), 'ok'])
                finally:
                    (root / 'stop').touch()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()

    def test_failed_peer_notification_checks_attempt_and_publishes_result_before_stop(self):
        with mock.patch('distributed_controller.RemoteStore') as factory:
            remote = factory.return_value
            remote.health.return_value = {'attempt_id': 'attempt003'}
            domain_runner.notify_failed_peer('url', 'token', 'attempt003', 'failed')
            self.assertEqual([c.args[0] for c in remote.write.call_args_list], ['pair/result.json', 'stop'])
            remote.write.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'peer attempt differs'):
                domain_runner.notify_failed_peer('url', 'token', 'attempt004', 'failed')
            remote.write.assert_not_called()

    def test_dci_shaping_snapshot_uses_structured_si_units(self) -> None:
        snapshot = {
            "tc": {
                "stdout": json.dumps([{
                    "kind": "netem",
                    "root": True,
                    "options": {
                        "limit": 1000,
                        "delay": {"delay": 0.002},
                        "rate": {"rate": 125000000},
                    },
                }])
            }
        }
        self.assertTrue(validate_attempt.dci_shaping_ok(snapshot))
        for field, value in (("delay", 0.003), ("rate", 100000000), ("limit", 999)):
            changed = json.loads(json.dumps(snapshot))
            options = json.loads(changed["tc"]["stdout"])[0]["options"]
            if field == "delay":
                options["delay"]["delay"] = value
            elif field == "rate":
                options["rate"]["rate"] = value
            else:
                options["limit"] = value
            changed["tc"]["stdout"] = json.dumps([{"kind": "netem", "root": True,
                                                      "options": options}])
            self.assertFalse(validate_attempt.dci_shaping_ok(changed))

    def test_smoke_gate_does_not_require_horizon_report(self) -> None:
        readiness = {
            "shared_runtime_image": "sha256:fixture",
            "simple_station_report": "PENDING",
            "workers": [{"ready": True}, {"ready": True}],
        }
        self.assertTrue(cross_host.stage_gate_ready("smoke", readiness))
        self.assertFalse(cross_host.stage_gate_ready("calibration", readiness))
        readiness["simple_station_report"] = "READY"
        self.assertTrue(cross_host.stage_gate_ready("calibration", readiness))
        readiness["workers"][1]["ready"] = False
        self.assertFalse(cross_host.stage_gate_ready("smoke", readiness))

    def test_slots_are_balanced_and_unique(self) -> None:
        slots = spec.formal_slots()
        self.assertEqual(len(slots), 40)
        self.assertEqual(len({slot.key for slot in slots}), 40)
        for seed in spec.SEEDS:
            chosen = [slot for slot in slots if slot.seed == seed]
            self.assertEqual({slot.method for slot in chosen}, set(spec.METHODS))
            self.assertEqual({slot.repetition for slot in chosen}, set(spec.REPETITIONS))
            first = [slot.method for slot in chosen if slot.repetition == 1]
            second = [slot.method for slot in chosen if slot.repetition == 2]
            self.assertEqual(first, list(reversed(second)))

    def test_horizon_requires_all_seeds_and_stops_at_96(self) -> None:
        reports = [
            {"seed": 1, "post_batches_observed": 64, "earliest_recommended_post_batches": 32},
            {"seed": 2, "post_batches_observed": 64, "earliest_recommended_post_batches": 40},
            {"seed": 3, "post_batches_observed": 64, "earliest_recommended_post_batches": 36},
        ]
        self.assertEqual(spec.choose_horizon(reports), 40)
        reports[2]["earliest_recommended_post_batches"] = None
        with self.assertRaisesRegex(ValueError, "extend"):
            spec.choose_horizon(reports)
        reports[2]["post_batches_observed"] = 96
        with self.assertRaisesRegex(ValueError, "blocked"):
            spec.choose_horizon(reports)

    def test_role_queues_share_the_same_joint_slots(self) -> None:
        formal = queue_runner.queue("formal", list(spec.SEEDS))
        self.assertEqual(len(formal), 40)
        self.assertEqual(formal[0:4], [
            ("nc", 1, 1), ("tcp-direct", 1, 1),
            ("tcp-direct", 1, 2), ("nc", 1, 2),
        ])

    def test_capture_requires_a_final_successful_pair_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            attempt = Path(directory) / "attempt001"
            attempt.mkdir()
            (attempt / "domain_result.json").write_text(
                json.dumps({"status": "CAPTURED"}), encoding="utf-8"
            )
            self.assertFalse(queue_runner.captured(attempt))
            (attempt / "pair_result.json").write_text(
                json.dumps({"status": "failed"}), encoding="utf-8"
            )
            self.assertFalse(queue_runner.captured(attempt))
            (attempt / "pair_result.json").write_text(
                json.dumps({"status": "ok"}), encoding="utf-8"
            )
            self.assertTrue(queue_runner.captured(attempt))

    def test_attempt_floor_aligns_asymmetric_history(self) -> None:
        attempts = [Path(f"attempt{index:03d}") for index in range(1, 7)]
        self.assertEqual(queue_runner.next_attempt_index(attempts, 9), 9)
        attempts.append(Path("attempt009"))
        self.assertEqual(queue_runner.next_attempt_index(attempts, 9), 10)

    def test_smoke_floor_ignores_older_captured_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            attempts = []
            for index in (9, 10):
                attempt = Path(directory) / f"attempt{index:03d}"
                attempt.mkdir()
                (attempt / "domain_result.json").write_text(
                    json.dumps({"status": "CAPTURED"}), encoding="utf-8"
                )
                (attempt / "pair_result.json").write_text(
                    json.dumps({"status": "ok"}), encoding="utf-8"
                )
                attempts.append(attempt)
            self.assertEqual(queue_runner.first_captured(attempts, 10).name, "attempt010")
            self.assertIsNone(queue_runner.first_captured(attempts[:1], 10))


class MonitorAndAgentTests(unittest.TestCase):
    def test_ipv4_parser_and_marker(self) -> None:
        ip = bytearray(40)
        ip[0] = 0x45
        struct.pack_into("!H", ip, 2, 40)
        ip[9] = 6
        ip[12:16] = socket.inet_aton("10.90.0.2")
        ip[16:20] = socket.inet_aton("10.90.0.34")
        struct.pack_into("!HH", ip, 20, 44002, 44034)
        struct.pack_into("!II", ip, 24, 12345, 98765)
        ip[32] = 0x50
        frame = b"\0" * 12 + b"\x08\x00" + bytes(ip)
        parsed = border_monitor.parse_ipv4(frame)
        self.assertEqual(parsed["src"], "10.90.0.2")
        self.assertEqual(parsed["transport_payload_bytes"], 0)
        self.assertEqual(parsed["tcp_sequence"], 12345)
        self.assertEqual(parsed["tcp_acknowledgment"], 98765)
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "current.tsv"
            marker.write_text("1\tnc\tpost\tremote\tA_to_B\tkey\t2\t40\thash\n")
            decoded = border_monitor.read_marker(marker)
            self.assertEqual(decoded["request_index"], "1")
            self.assertEqual(border_monitor.federation_direction(
                "10.90.0.2", "10.90.0.40"), "A_to_B")
            self.assertEqual(border_monitor.capture_direction("domain-a", "A_to_B"),
                             "egress")
            self.assertEqual(border_monitor.capture_direction("domain-b", "A_to_B"),
                             "ingress")
            self.assertEqual(border_monitor.logical_packet_type(decoded, "A_to_B"),
                             "REQUEST")
            self.assertEqual(border_monitor.logical_packet_type(decoded, "B_to_A"),
                             "DATA")

    def test_agent_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory), "x" * 32, "domain-b", 450,
                               "172.29.251.1", "fixture")
            with self.assertRaisesRegex(ValueError, "escapes"):
                state.path("../outside")


class DecompositionTests(unittest.TestCase):
    def test_tcp_prefix_excludes_tail_retransmission_and_late_fin(self):
        packets = [
            {"tcp_sequence": 100, "transport_payload_bytes": 60, "tcp_flags": 16},
            {"tcp_sequence": 160, "transport_payload_bytes": 40, "tcp_flags": 16},
            {"tcp_sequence": 160, "transport_payload_bytes": 40, "tcp_flags": 16},
            {"tcp_sequence": 200, "transport_payload_bytes": 0, "tcp_flags": 17},
        ]
        self.assertEqual(decomposition.first_complete_tcp_prefix(packets, 100), packets[:2])
        self.assertEqual(decomposition.first_complete_tcp_prefix(packets, 101), [])
        self.assertEqual(decomposition.first_complete_tcp_prefix([packets[0], packets[-1]], 100), [])
        self.assertEqual(decomposition.first_complete_tcp_prefix(packets[:-1], 100), [])

    def test_missing_decomposition_keeps_end_to_end_and_reports_coverage(self):
        row = {"phase": "post", "bin": "64", "request_class": "remote",
               "end_to_end_ns": "50000000", "trace_complete": "0"}
        result = analyze_results.batch_rows([row])[0]
        self.assertEqual(result["end_to_end_ms"], 50)
        self.assertEqual(result["decomposition_complete"], 0)

    def fixture(self):
        request = {
            "request_index": "1", "method": "nc", "phase": "post", "bin": "1",
            "request_class": "remote", "direction": "A_to_B", "key": "k",
            "requester": "2", "owner": "40", "packet_request_id": "9",
            "request_start_mono_ns": "1000", "app_receive_mono_ns": "11000",
            "expected_value_bytes": "100",
        }
        traces = [
            {"_domain": "B", "event": "provider_hit", "node": 40, "src": "40",
             "dest": "2", "key": "k", "request_id": "9", "ts_mono_ns": 5000},
            {"_domain": "B", "event": "data_send_start", "node": 40, "src": "40",
             "dest": "33", "key": "k", "request_id": "9", "ts_mono_ns": 5500},
            {"_domain": "B", "event": "data_handle_start", "node": 33, "src": "40",
             "dest": "33", "key": "k", "request_id": "9", "ts_mono_ns": 5700},
            {"_domain": "B", "event": "data_forward_start", "node": 33, "src": "33",
             "dest": "1", "key": "k", "request_id": "9", "ts_mono_ns": 5800},
            {"_domain": "A", "event": "data_handle_start", "node": 1, "src": "33",
             "dest": "1", "key": "k", "request_id": "9", "ts_mono_ns": 9100},
            {"_domain": "A", "event": "data_forward_start", "node": 1, "src": "1",
             "dest": "2", "key": "k", "request_id": "9", "ts_mono_ns": 9200},
            {"_domain": "A", "event": "app_receive", "node": 2, "src": "1",
             "dest": "2", "key": "k", "request_id": "9", "ts_mono_ns": 11000},
        ]
        base = {"request_index": "1", "transport_payload_bytes": 100,
                "ip_total_bytes": 140, "ts_mono_ns": 0}
        borders = [
            {**base, "role": "domain-a", "direction": "egress",
             "logical_packet_type": "REQUEST", "wire_stream": "request",
             "ts_mono_ns": 2000},
            {**base, "role": "domain-b", "direction": "ingress",
             "logical_packet_type": "REQUEST", "wire_stream": "request",
             "ts_mono_ns": 3000},
            {**base, "role": "domain-b", "direction": "egress",
             "logical_packet_type": "DATA", "wire_stream": "data",
             "ts_mono_ns": 6000},
            {**base, "role": "domain-a", "direction": "ingress",
             "logical_packet_type": "DATA", "wire_stream": "data",
             "ts_mono_ns": 9000},
        ]
        return request, traces, borders

    def test_components_close_without_cross_host_timestamp_subtraction(self) -> None:
        request, traces, borders = self.fixture()
        row = decomposition.decompose(request, traces, borders, 1_000_000)
        self.assertEqual(row["trace_complete"], 1)
        self.assertEqual(row["provider"], 40)
        self.assertEqual(row["requester_domain_egress_ns"], 1000)
        self.assertEqual(row["serving_domain_discovery_ns"], 2000)
        self.assertEqual(row["dci_roundtrip_residual_ns"], 4000)
        self.assertEqual(row["component_sum_ns"], row["end_to_end_ns"])

    def test_missing_border_event_rejected(self) -> None:
        request, traces, borders = self.fixture()
        row = decomposition.decompose(request, traces, borders[:-1], 1_000_000)
        self.assertEqual(row["trace_complete"], 0)
        self.assertEqual(row["trace_reason"], "missing_border_payload_event")

    def test_header_only_reverse_stream_is_not_selected_as_data(self) -> None:
        request, traces, borders = self.fixture()
        borders.insert(2, {
            "request_index": "1", "transport_payload_bytes": 12,
            "ip_total_bytes": 52, "ts_mono_ns": 5400,
            "role": "domain-b", "direction": "egress",
            "logical_packet_type": "DATA", "wire_stream": "control-copy",
        })
        selected = decomposition.border_packets(
            borders, "1", "domain-b", "egress", "DATA", 100
        )
        self.assertEqual([event["wire_stream"] for event in selected], ["data"])
        row = decomposition.decompose(request, traces, borders, 1_000_000)
        self.assertEqual(row["trace_complete"], 1)
        self.assertEqual(row["provider"], 40)

    def test_trace_and_border_indexes_preserve_request_identity(self) -> None:
        request, traces, borders = self.fixture()
        trace_lookup = decomposition.index_traces(traces, "nc")
        border_lookup = decomposition.index_borders(borders)
        row = decomposition.decompose(
            request,
            trace_lookup[(request["key"], request["packet_request_id"])],
            border_lookup[request["request_index"]],
            1_000_000,
        )
        self.assertEqual(row["trace_complete"], 1)

    def test_late_provider_branch_does_not_replace_delivered_chain(self) -> None:
        request, traces, borders = self.fixture()
        traces += [
            {"_domain": "B", "event": "provider_hit", "node": 41, "src": "41",
             "dest": "33", "key": "k", "request_id": "9", "ts_mono_ns": 6100},
            {"_domain": "B", "event": "data_send_start", "node": 41, "src": "41",
             "dest": "33", "key": "k", "request_id": "9", "ts_mono_ns": 6200},
        ]
        row = decomposition.decompose(request, traces, borders, 1_000_000)
        self.assertEqual(row["trace_complete"], 1)
        self.assertEqual(row["provider"], 40)

    def test_negative_component_rejected(self) -> None:
        request, traces, borders = self.fixture()
        borders[0]["ts_mono_ns"] = 0
        row = decomposition.decompose(request, traces, borders, 10)
        self.assertEqual(row["trace_complete"], 0)
        self.assertEqual(row["trace_reason"], "negative_component")
        self.assertEqual(row["end_to_end_ns"], 10000)
        self.assertEqual(row["dci_roundtrip_residual_ns"], "")

    def test_requester_cache_needs_no_dci_clock_or_packet(self) -> None:
        request, _, _ = self.fixture()
        request["packet_request_id"] = "0"
        traces = [{
            "_domain": "A", "event": "provider_hit", "node": 2, "key": "k",
            "request_id": "0", "ts_mono_ns": 5000,
        }]
        row = decomposition.decompose(request, traces, [], 1_000_000)
        self.assertEqual(row["trace_complete"], 1)
        self.assertEqual(row["service_class"], "requester_cache")
        self.assertEqual(row["dci_roundtrip_residual_ns"], 0)
        self.assertEqual(row["component_sum_ns"], row["end_to_end_ns"])


class GeneratorAndTopologyTests(unittest.TestCase):
    def test_domain_runner_installs_generated_startup_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lab_shared = Path(directory) / "shared"
            shared = lab_shared / domain_runner.SUITE
            shared.mkdir(parents=True)
            domain_runner.install_lab_support(shared)
            bootstrap = lab_shared / "start-node.sh"
            self.assertTrue(bootstrap.is_file())
            self.assertIn('startup_log="/shared/logs/', bootstrap.read_text())
            switch_bootstrap = lab_shared / "start-switch.sh"
            self.assertTrue(switch_bootstrap.is_file())
            self.assertIn("configured forwarding switch", switch_bootstrap.read_text())
            self.assertTrue((shared / "cache_probe.py").is_file())
            self.assertTrue((shared / "tcp_node.py").is_file())

    def test_only_non_evidence_smoke_has_extended_retry_budget(self) -> None:
        self.assertGreater(queue_runner.SMOKE_MAX_ATTEMPTS, spec.MAX_ATTEMPTS)
        source = Path(queue_runner.__file__).read_text(encoding="utf-8")
        self.assertIn('args.stage == "smoke"', source)

    def test_cross_host_nodes_are_controller_terminated(self) -> None:
        source = Path(domain_runner.__file__).read_text(encoding="utf-8")
        self.assertIn('"RSYNC_LOOP_ITERATIONS": "2147483647"', source)

    def test_generator_and_both_shards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workloads"
            generate_workloads.generate_one(root, 1, 4, "CALIBRATION")
            generate_workloads.validate_one(root, 1, 4)
            scenario = generate_workloads.scenario_name(1, 4)
            for shard, expected, forbidden in (("a", "node250", "node450"),
                                                ("b", "node450", "node250")):
                lab = Path(directory) / f"lab-{shard}"
                env = {
                    **__import__("os").environ,
                    "LAB_DIR": str(lab), "SCENARIOS_DIR": str(root / "scenarios"),
                    "KATHARA_FEDERATION_PHYSICAL_MODE": "bordered-cross-host",
                    "KATHARA_FEDERATION_SHARD": shard,
                    "RSYNC_FEDERATION_METHOD": "nc",
                    "RSYNC_FEDERATION_ACTIVATION_MODE": "logical-only",
                }
                completed = subprocess.run(
                    [str(REPO / "scripts/kathara/render_ruikekkk_scenario.sh"), scenario],
                    env=env, text=True, capture_output=True, check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                machines = [path.stem for path in lab.glob("node*.startup")]
                self.assertEqual(len(machines), 45)
                self.assertIn(expected, machines)
                self.assertNotIn(forbidden, machines)
                config = (lab / "lab.conf").read_text(encoding="utf-8")
                self.assertNotIn("link1_33", config)


class FormalAggregationTests(unittest.TestCase):
    def test_formal_aggregation_uses_ten_seed_means(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = []
            columns = decomposition.COLUMNS
            for slot in spec.formal_slots():
                path = root / f"{slot.ordinal}.csv"
                base = {column: "0" for column in columns}
                remote = {**base, "request_index": "1", "method": slot.method,
                          "phase": "post", "bin": "1", "request_class": "remote",
                          "end_to_end_ns": str((100 + slot.seed) * 1_000_000)}
                local = {**base, "request_index": "2", "method": slot.method,
                         "phase": "post", "bin": "1", "request_class": "local_control",
                         "end_to_end_ns": "50000000"}
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows([remote, local])
                selection.append({"method": slot.method, "seed": slot.seed,
                                  "repetition": slot.repetition,
                                  "decomposition_csv": str(path)})
            result = analyze_results.formal(selection, root / "formal.json")
            self.assertEqual(result["selected_runs"], 40)
            self.assertEqual(result["effects"]["nc"]["remote_ms"]["n_seed_means"], 10)
            self.assertEqual(result["batch_timeline"][0]["n_seed_means"], 10)


if __name__ == "__main__":
    unittest.main()
