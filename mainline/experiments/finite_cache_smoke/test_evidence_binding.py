"""Deterministic acceptance boundary tests; only temporary evidence is mutated."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

import offline_accept as offline
import monitor_evidence


class EvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="smoke-binding-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n")
        return path

    def seal(self):
        with contextlib.redirect_stdout(io.StringIO()):
            offline.evidence_archive.seal(self.root, "evidence-manifest.txt",
                                          self.root / "manifest-verification.txt")

    def selection(self):
        rows = []
        for name in ("slot-a", "slot-b"):
            identity = {"slot_id": name, "transport": "tcp-direct", "rate": 32}
            self.write(f"slots/{name}/result.json", identity)
            self.write(f"slots/{name}/plan.json", dict(identity, plan_sha256="a" * 64))
            rows.append({"slot": name, "run": "run1", "attempt": "attempt001",
                         "transport": "tcp-direct", "rate": 32, "plan_sha256": "a" * 64})
        self.write("selection.json", {"slots": rows})
        self.seal()
        return rows

    def test_selection_positive(self):
        self.selection()
        self.assertEqual(offline.verify_selection(self.root)[1], [])

    def test_deleted_registered_file(self):
        self.selection()
        (self.root / "slots/slot-b/result.json").unlink()
        self.assertTrue(offline.verify_selection(self.root)[1])

    def test_mutated_covered_file(self):
        self.selection()
        self.write("slots/slot-a/result.json", {})
        self.assertTrue(offline.verify_selection(self.root)[1])

    def test_missing_slot_even_with_new_manifest(self):
        self.selection()
        (self.root / "slots/slot-b").rename(self.root / "removed-slot")
        self.seal()
        self.assertTrue(offline.verify_selection(self.root)[1])

    def test_extra_slot_even_with_new_manifest(self):
        self.selection()
        self.write("slots/extra/result.json", {})
        self.seal()
        self.assertTrue(offline.verify_selection(self.root)[1])

    def test_duplicate_registration(self):
        rows = self.selection()
        self.write("selection.json", {"slots": rows + [rows[0]]})
        self.seal()
        self.assertTrue(offline.verify_selection(self.root)[1])

    def test_identity_mismatch(self):
        self.selection()
        self.write("slots/slot-a/result.json", {"slot_id": "other", "transport": "tcp-direct", "rate": 32})
        self.seal()
        self.assertTrue(offline.verify_selection(self.root)[1])

    def monitor(self):
        row = {"status": "ok", "run_id": "redis1", "ts_mono_ns": 1,
               "ts_epoch_ns": 1, "used_memory": "100", "evicted_keys": "0"}
        self.write("monitor/node1.jsonl", row)
        normalized = dict(row, node=1, instance_identity="node1", redis_run_id="redis1")
        identity = {"run_id": "slot", "slot_id": "slot", "attempt": "003", "transport": "nc"}
        record = dict(identity, schema="finite-cache-monitor-acceptance", version=1,
                      expected_instances=[1], baseline={"1": normalized}, exit_codes={"1": 0},
                      instances={"1": {"instance_identity": "node1", "redis_run_id": "redis1",
                                        "raw_file": "monitor/node1.jsonl", "baseline": normalized,
                                        "first_sample": normalized, "last_sample": normalized}})
        return record, identity

    def test_monitor_positive(self):
        record, identity = self.monitor()
        self.assertEqual(monitor_evidence.load(self.root, record, identity)[1], [])

    def test_monitor_reference_and_identity_negatives(self):
        record, identity = self.monitor()
        mutations = [lambda r:r["baseline"].update({"1": None}),
                     lambda r:r["instances"]["1"].update(raw_file="missing.jsonl"),
                     lambda r:r["instances"]["1"].update(raw_file="../outside.jsonl"),
                     lambda r:r.update(attempt="002"),
                     lambda r:r.update(slot_id="other")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                bad = copy.deepcopy(record)
                mutate(bad)
                self.assertTrue(monitor_evidence.load(self.root, bad, identity)[1])

    def test_acceptor_monitor_negative_regressions(self):
        # Capture the production self-test's positive fixture, then mutate only
        # the boundary being tested. Never substitute a mocked acceptance result.
        s = offline.smoke_accept
        original, captured = s.accept_slot, []
        def capture(**kw):
            if not captured:
                captured.append(copy.deepcopy(kw))
            return original(**kw)
        s.accept_slot = capture
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(s._self_test(), 0)
        finally:
            s.accept_slot = original
        for change in (
            lambda k:k["monitor_acceptance"]["baseline"].update({"1": None}),
            lambda k:k["monitor_acceptance"]["instances"]["1"].update(redis_run_id="other"),
            lambda k:[r.pop("redis_run_id", None) for r in k["monitor_series"][1]],
        ):
            k = copy.deepcopy(captured[0]); change(k)
            self.assertEqual(original(**k)["verdict"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
