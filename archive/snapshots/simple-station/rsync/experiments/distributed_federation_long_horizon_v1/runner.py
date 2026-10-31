#!/usr/bin/env python3
"""Freeze, run, resume, validate, and inspect the long-horizon formal matrix."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("NETWORKCACHE_REPO", str(HERE.parents[1]))).resolve()
SCRIPTS = REPO / "experiments/content_discovery/delay_find_content/scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_federation_long_horizon_v1 as gen  # noqa: E402


CAMPAIGN_ID = "networkcache-single-host-logical-federation-long-horizon-v4"
POLICY_ID = "single-host-long-horizon-validator-v4-tcp-control-initialization"
COMPATIBLE_PRIOR_NC = {
    "c42fb25e229996f81f74f81333bf80676c9fe85425ac54c8b74420c144210192":
        "05d1896e492d24844087a7e6e2f0c6dcb02973569859f8905680b9194f27440c",
}
EXPECTED_IMAGE = "sha256:bce2e56d82225efd51335a3af959ec0c0fc199782ab68281fd16f1ff16c13a74"
UNIT = "networkcache-federation-long-horizon-v1.service"
RESULTS = gen.EXP / "results" / gen.SUITE
CAMPAIGN = HERE / "campaign.json"
ASSIGNMENT = HERE / "assignment.json"
SOURCE_MANIFEST = HERE / "source_manifest.json"
WORKLOAD_MANIFEST = HERE / "workload_manifest.json"
PILOT_ATTEMPT = (
    gen.EXP / "results/federation-convergence-pilot/attempts/"
    "catalog512-zipf-nc-seed001-attempt004"
)
PILOT_REPORT_SHA256 = "160fd827ff76ed377db64c347e8eb4f1c764b393019210757c86e103d5fa69a0"
PILOT_WINDOWS_SHA256 = "d158edc0952ab59e85b3a603c32c1bcfb4c74cd887586077c1aec456cac1b741"
TARGET_REPETITIONS = 2
MAX_ATTEMPTS = 4
MIN_AVAILABLE_MEMORY = 32 * 1024**3
MIN_AVAILABLE_DISK = 100 * 1024**3

SOURCE_PATHS = (
    "scripts/kathara/render_ruikekkk_scenario.sh",
    "scripts/kathara/run_ruikekkk_scenario.sh",
    "scripts/kathara/down_ruikekkk_scenario.sh",
    "experiments/content_discovery/delay_find_content/scripts/"
    "run_federation_v3_bf1024_h3_fallback_once.sh",
    "experiments/content_discovery/delay_find_content/scripts/federation_v2_controller.py",
    "experiments/content_discovery/delay_find_content/scripts/federation_v3_controller.py",
    "experiments/content_discovery/delay_find_content/scripts/federation_v3_cache_probe.py",
    "experiments/content_discovery/delay_find_content/scripts/latency_v3_tcp_node.py",
    "experiments/content_discovery/delay_find_content/scripts/latency_v3_clock_probe.py",
    "experiments/content_discovery/delay_find_content/scripts/federation_v2_inspect_network.sh",
    "experiments/content_discovery/delay_find_content/scripts/"
    "analyze_federation_v3_bf1024_h3_fallback_trace.py",
)


@dataclass(frozen=True)
class Config:
    method: str

    @property
    def slug(self) -> str:
        return f"catalog512-zipf-{self.method}"


CONFIGS = (Config("nc"), Config("tcp-direct"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(payload: dict, field: str) -> str:
    material = dict(payload)
    material.pop(field, None)
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def records(paths: list[Path]) -> list[dict[str, object]]:
    return [
        {
            "path": str(path.relative_to(REPO)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(paths)
    ]


def manifest_digest(rows: list[dict[str, object]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source_records() -> list[dict[str, object]]:
    paths = [REPO / name for name in SOURCE_PATHS]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"missing frozen execution source: {missing}")
    return records(paths)


def workload_records() -> list[dict[str, object]]:
    paths = [path for path in gen.DEFAULT_ROOT.rglob("*") if path.is_file()]
    if not paths:
        raise RuntimeError("generated long-horizon workloads are missing")
    return records(paths)


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def atomic_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def expected_assignment() -> dict:
    payload = {
        "schema_version": 1,
        "campaign": CAMPAIGN_ID,
        "suite": gen.SUITE,
        "server": "simple-station",
        "seeds": list(gen.SEEDS),
        "methods": [config.method for config in CONFIGS],
        "repetitions": TARGET_REPETITIONS,
        "max_attempts_per_slot": MAX_ATTEMPTS,
        "selected_slots": len(gen.SEEDS) * len(CONFIGS) * TARGET_REPETITIONS,
        "queue_policy": "seed-paired-balanced-method-order",
    }
    payload["assignment_sha256"] = canonical_digest(payload, "assignment_sha256")
    return payload


def freeze() -> dict:
    gen.validate(gen.DEFAULT_ROOT)
    assignment = expected_assignment()
    sources = source_records()
    workloads = workload_records()
    campaign = {
        "schema_version": 1,
        "campaign": CAMPAIGN_ID,
        "suite": gen.SUITE,
        "evidence_level": "FORMAL_ON_40_OF_40_READY",
        "runtime_image_id": EXPECTED_IMAGE,
        "methods": [config.method for config in CONFIGS],
        "seeds": list(gen.SEEDS),
        "repetitions": TARGET_REPETITIONS,
        "selected_runs": 40,
        "requests_per_run": gen.EXPECTED_REQUESTS,
        "pre_batches": 8,
        "post_batches": gen.POST_BINS,
        "catalog_size_per_cluster": gen.CATALOG_SIZE,
        "distribution": "zipf",
        "zipf_alpha": 1.1,
        "value_size_bytes": 65536,
        "wire_version": 3,
        "bloom_bits": 1024,
        "bloom_hash_count": 3,
        "staged_fallback": True,
        "fallback_delay_ms": 1500,
        "query_table_timeout_ms": 7000,
        "controller_timeout_ms": 8000,
        "max_hop_count": 12,
        "execution": "global_closed_loop_one_request_in_flight",
        "validator_policy": POLICY_ID,
        "assignment_sha256": assignment["assignment_sha256"],
        "source_manifest_sha256": manifest_digest(sources),
        "workload_manifest_sha256": manifest_digest(workloads),
        "horizon_decision": {
            "accepted_post_batches": 64,
            "accepted_by_user_on": "2026-09-09",
            "basis": "late remote latency plateau accepted; mechanism drift retained as diagnostic",
            "pilot_attempt": "catalog512-zipf-nc-seed001-attempt004",
            "pilot_requests": 11264,
            "pilot_report_sha256": PILOT_REPORT_SHA256,
            "pilot_windows_sha256": PILOT_WINDOWS_SHA256,
            "last_window_remote_end_to_end_ms": 50.07429037304688,
            "last_window_local_control_ms": 53.0176727890625,
        },
        "compatible_prior_nc_campaigns": [
            {"campaign_fingerprint": fingerprint, "assignment_sha256": assignment_sha}
            for fingerprint, assignment_sha in sorted(COMPATIBLE_PRIOR_NC.items())
        ],
    }
    campaign["campaign_fingerprint"] = canonical_digest(
        campaign, "campaign_fingerprint"
    )
    atomic_json(ASSIGNMENT, assignment)
    atomic_json(SOURCE_MANIFEST, sources)
    atomic_json(WORKLOAD_MANIFEST, workloads)
    atomic_json(CAMPAIGN, campaign)
    return campaign


def load_assignment() -> dict:
    payload = json.loads(ASSIGNMENT.read_text(encoding="utf-8"))
    if payload != expected_assignment():
        raise RuntimeError("assignment is not the frozen 40-slot matrix")
    return payload


def load_campaign(*, verify_files: bool = True) -> dict:
    payload = json.loads(CAMPAIGN.read_text(encoding="utf-8"))
    if payload.get("campaign_fingerprint") != canonical_digest(
        payload, "campaign_fingerprint"
    ):
        raise RuntimeError("campaign fingerprint mismatch")
    assignment = load_assignment()
    if payload.get("assignment_sha256") != assignment["assignment_sha256"]:
        raise RuntimeError("campaign assignment fingerprint mismatch")
    if payload.get("runtime_image_id") != EXPECTED_IMAGE:
        raise RuntimeError("campaign runtime image changed")
    if payload.get("selected_runs") != 40 or payload.get("requests_per_run") != 11264:
        raise RuntimeError("campaign matrix shape changed")
    if verify_files:
        frozen_sources = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
        frozen_workloads = json.loads(WORKLOAD_MANIFEST.read_text(encoding="utf-8"))
        current_sources = source_records()
        current_workloads = workload_records()
        if current_sources != frozen_sources or manifest_digest(current_sources) != payload.get(
            "source_manifest_sha256"
        ):
            raise RuntimeError("frozen execution source fingerprint mismatch")
        if current_workloads != frozen_workloads or manifest_digest(current_workloads) != payload.get(
            "workload_manifest_sha256"
        ):
            raise RuntimeError("frozen workload fingerprint mismatch")
    return payload


def read_csv(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def service_state(unit: str) -> str:
    return run_command(["systemctl", "is-active", unit], check=False).stdout.strip() or "unknown"


def pilot_gate(campaign: dict) -> dict:
    report_path = PILOT_ATTEMPT / "convergence_analysis/convergence_report.json"
    windows_path = PILOT_ATTEMPT / "convergence_analysis/window_means.csv"
    if not report_path.is_file() or not windows_path.is_file():
        raise RuntimeError("accepted simple-station pilot evidence is missing")
    decision = campaign["horizon_decision"]
    if sha256_file(report_path) != decision["pilot_report_sha256"]:
        raise RuntimeError("pilot convergence report fingerprint mismatch")
    if sha256_file(windows_path) != decision["pilot_windows_sha256"]:
        raise RuntimeError("pilot window table fingerprint mismatch")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    windows = read_csv(windows_path)
    if report.get("post_batches_observed") != 64 or not windows:
        raise RuntimeError("pilot did not observe the accepted 64 POST batches")
    last = windows[-1]
    remote = float(last["remote_end_to_end_latency_ms"])
    local = float(last["local_end_to_end_latency_ms"])
    if not math.isclose(remote, decision["last_window_remote_end_to_end_ms"], abs_tol=1e-9):
        raise RuntimeError("pilot last-window remote latency changed")
    if not math.isclose(local, decision["last_window_local_control_ms"], abs_tol=1e-9):
        raise RuntimeError("pilot last-window local latency changed")
    return {
        "attempt": decision["pilot_attempt"],
        "observed_post_batches": 64,
        "analyzer_decision": report["decision"],
        "accepted_post_batches": decision["accepted_post_batches"],
        "last_window_remote_end_to_end_ms": remote,
        "last_window_local_control_ms": local,
    }


def available_memory() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is missing")


def preflight(*, allow_active_self: bool = False) -> dict:
    gen.validate(gen.DEFAULT_ROOT)
    campaign = load_campaign()
    if not allow_active_self and service_state(UNIT) == "active":
        raise RuntimeError(f"formal unit is already active: {UNIT}")
    # simple-station's Docker daemon uses the host containerd service.  It is
    # therefore not a conflicting workload by itself; kubelet and any active
    # Kathara lab remain hard blockers.
    for conflicting in (
        "networkcache-federation-convergence-pilot.service",
        "kubelet.service",
    ):
        if service_state(conflicting) == "active":
            raise RuntimeError(f"conflicting unit is active: {conflicting}")
    image = run_command(
        ["docker", "image", "inspect", "rsync-router:ruikekkk-kathara", "--format", "{{.Id}}"]
    ).stdout.strip()
    if image != EXPECTED_IMAGE:
        raise RuntimeError(f"frozen runtime image mismatch: {image}")
    plugin = run_command(
        ["docker", "plugin", "inspect", "kathara/katharanp_vde:amd64", "--format", "{{.Enabled}}"]
    ).stdout.strip()
    if plugin != "true":
        raise RuntimeError("Kathara VDE plugin is not enabled")
    containers = run_command(["docker", "ps", "--format", "{{.Names}}"], check=False).stdout
    active = [name for name in containers.splitlines() if "kathara" in name.lower()]
    if active:
        raise RuntimeError(f"another Kathara lab is active: {active[:3]}")
    memory = available_memory()
    disk = shutil.disk_usage(RESULTS.parent).free
    if memory < MIN_AVAILABLE_MEMORY:
        raise RuntimeError(f"insufficient available memory: {memory}")
    if disk < MIN_AVAILABLE_DISK:
        raise RuntimeError(f"insufficient available disk: {disk}")
    return {
        "status": "READY",
        "campaign": campaign["campaign_fingerprint"],
        "assignment": campaign["assignment_sha256"],
        "validator_policy": POLICY_ID,
        "runtime_image_id": image,
        "selected_slots": 40,
        "requests_per_run": 11264,
        "available_memory_bytes": memory,
        "available_disk_bytes": disk,
        "active_kathara_containers": 0,
        "pilot_gate": pilot_gate(campaign),
    }


def attempts(config: Config, seed: int) -> list[Path]:
    root = RESULTS / "attempts" / config.slug / f"seed{seed:03d}"
    return sorted(root.glob("attempt[0-9][0-9][0-9]")) if root.exists() else []


def campaign_compatible(config: Config, attached: dict, assignment: dict,
                        current: dict) -> bool:
    if attached == current and assignment == load_assignment():
        return True
    if config.method != "nc":
        return False
    fingerprint = str(attached.get("campaign_fingerprint", ""))
    expected_assignment = COMPATIBLE_PRIOR_NC.get(fingerprint)
    return (
        expected_assignment is not None
        and assignment.get("assignment_sha256") == expected_assignment
        and attached.get("assignment_sha256") == expected_assignment
        and attached.get("runtime_image_id") == current.get("runtime_image_id")
        and attached.get("requests_per_run") == current.get("requests_per_run")
        and attached.get("post_batches") == current.get("post_batches")
    )


def non_evidence_reason(config: Config, path: Path) -> str | None:
    """Classify immutable diagnostics that never entered an experiment.

    Superseded campaign attempts and current-campaign prelaunch failures remain
    in raw storage for provenance, but do not consume the v2 campaign's four
    evidence attempts per slot.
    """
    try:
        attached = json.loads((path / "campaign_lock.json").read_text(encoding="utf-8"))
        assignment = json.loads((path / "assignment.json").read_text(encoding="utf-8"))
        current = load_campaign(verify_files=False)
    except Exception:
        return None
    if not campaign_compatible(config, attached, assignment, current):
        if config.method != "tcp-direct":
            return "prior_campaign"
        try:
            completion = json.loads(
                (path / "completion_summary.json").read_text(encoding="utf-8")
            )
        except Exception:
            completion = {}
        if completion.get("issued") == 0 and completion.get("completed") == 0:
            return "prior_campaign_tcp_helper_startup_race"
        return "prior_campaign"
    if (
        not (path / "runtime_image_id.before_launch").exists()
    ):
        if (
            not (path / "metadata.json").exists()
            and not (path / "driver.stdout.log").exists()
            and not (path / "driver.stderr.log").exists()
        ):
            return "infrastructure_prelaunch_before_runtime_snapshot"
        try:
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            diagnostic = json.loads(
                (path / "failure_diagnostic.json").read_text(encoding="utf-8")
            )
            stderr = (path / "driver.stderr.log").read_text(encoding="utf-8")
        except Exception:
            return None
        completion = diagnostic.get("completion", {})
        if (
            metadata.get("status") == "driver_failed"
            and metadata.get("runtime_image_id") == "unavailable"
            and metadata.get("controller_status") == 99
            and completion.get("issued") == 0
            and completion.get("completed") == 0
            and set(stderr.splitlines()) == {"FAIL: missing required command: kathara"}
        ):
            return "infrastructure_prelaunch_kathara_path"
    return None


def evidence_attempts(config: Config, seed: int) -> list[Path]:
    return [path for path in attempts(config, seed) if non_evidence_reason(config, path) is None]


def next_attempt(config: Config, seed: int) -> Path:
    existing = attempts(config, seed)
    if len(evidence_attempts(config, seed)) >= MAX_ATTEMPTS:
        raise RuntimeError(f"attempt budget exhausted: {config.slug} seed{seed:03d}")
    next_index = max((int(path.name.removeprefix("attempt")) for path in existing), default=0) + 1
    return existing[0].parent / f"attempt{next_index:03d}" if existing else (
        RESULTS / "attempts" / config.slug / f"seed{seed:03d}" / "attempt001"
    )


def network_ok(path: Path) -> bool:
    if not path.is_file():
        return False
    sections = path.read_text(encoding="utf-8", errors="replace").split("## node")[1:]
    return len(sections) == 88 and all(
        "mtu 1500" in section and "qdisc netem" in section and "delay 2ms" in section
        and ("rate 1Gbit" in section or "rate 1000Mbit" in section)
        for section in sections
    )


def clock_ok(path: Path) -> bool:
    rows = read_csv(path)
    try:
        nodes = [int(row["node"]) for row in rows]
        values = [int(row["monotonic_ns"]) for row in rows]
        signatures = {row["timens_offsets_sha256"] for row in rows}
    except (KeyError, TypeError, ValueError):
        return False
    return (
        sorted(nodes) == list(range(1, 65))
        and len(set(nodes)) == 64
        and all(value > 0 for value in values)
        and len(signatures) == 1
        and all(len(signature) == 64 for signature in signatures)
    )


def raw_expiry_events(path: Path) -> list[dict]:
    output = []
    for trace in sorted((path / "traces").glob("*.jsonl")):
        for line_number, line in enumerate(
            trace.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if "query_expired_unsatisfied" not in line:
                continue
            event = json.loads(line)
            if event.get("event") != "query_expired_unsatisfied":
                continue
            output.append({**event, "_trace_file": trace.name, "_trace_line": line_number})
    return output


def isolation_ok(config: Config, path: Path, trace: list[dict[str, str]]) -> tuple[bool, str]:
    rows = read_csv(path / "isolation_results.csv")
    identities = [
        ("1", "A_to_B", "2", "40", "federation_v4h3_probe_B"),
        ("2", "B_to_A", "34", "8", "federation_v4h3_probe_A"),
    ]
    actual = [
        tuple(row.get(field, "") for field in ("probe_index", "direction", "requester", "owner", "key"))
        for row in rows
    ]
    if actual != identities:
        return False, "isolation_probe_identity"
    completion = "1" if config.method == "tcp-direct" else "0"
    if any(
        row.get("status") != "ok"
        or row.get("completion_present") != completion
        or row.get("quiescence_ack_count") != "64"
        or row.get("timeout_ms") != "8000"
        for row in rows
    ):
        return False, "isolation_probe_semantics"
    try:
        events = raw_expiry_events(path)
    except Exception:
        return False, "raw_expiry_parse"
    if config.method == "tcp-direct":
        return (not events, "ok" if not events else "tcp_direct_raw_query_expiry")
    try:
        pre_end = max(
            int(row["app_receive_mono_ns"]) for row in trace if row.get("phase") == "pre"
        )
        post_start = min(
            int(row["request_start_mono_ns"]) for row in trace if row.get("phase") == "post"
        )
    except (KeyError, TypeError, ValueError):
        return False, "isolation_window"
    counts: dict[str, int] = {}
    for event in events:
        node = str(event.get("node", ""))
        counts[node] = counts.get(node, 0) + 1
        if node not in {"2", "34"} or counts[node] > 1:
            return False, "isolation_expiry_requester"
        if event.get("_trace_file") != f"node{node}.jsonl":
            return False, "isolation_expiry_trace"
        if (
            event.get("phase") != "fallback"
            or event.get("reason") != "query-lifetime-expired"
            or str(event.get("src", "")) != node
            or str(event.get("request_id", ""))
            or str(event.get("key", ""))
        ):
            return False, "isolation_expiry_format"
        try:
            timestamp = int(event["ts_mono_ns"])
        except (KeyError, TypeError, ValueError):
            return False, "isolation_expiry_timestamp"
        if not pre_end < timestamp < post_start:
            return False, "isolation_expiry_outside_window"
    return True, "ok_expected_isolation_expiries" if events else "ok"


def snapshots_ok(path: Path) -> bool:
    occupancy = read_csv(path / "cache_occupancy.csv")
    bloom = read_csv(path / "sync_bloom_state.csv")
    post = {1, 2, 4, 6, 8, 10, 12, 14, 16} | set(range(20, 65, 4))
    snapshots = {
        "ready", "pre-bin02", "pre-bin04", "pre-bin06", "pre-bin08",
        "pre-t0", "post-t0", "final", *(f"post-bin{batch:02d}" for batch in post),
    }
    if len(occupancy) != len(snapshots) * 64:
        return False
    if {row.get("snapshot_id") for row in occupancy} != snapshots:
        return False
    if not bloom or {row.get("snapshot_id") for row in bloom} != snapshots:
        return False
    try:
        return all(
            int(row["bit_count"]) == 1024 and 0 <= float(row["occupancy"]) <= 1
            for row in bloom
        )
    except (KeyError, TypeError, ValueError):
        return False


def validate_attempt(config: Config, seed: int, path: Path) -> tuple[bool, str]:
    try:
        campaign = load_campaign(verify_files=False)
        attached_campaign = json.loads((path / "campaign_lock.json").read_text(encoding="utf-8"))
        assignment = json.loads((path / "assignment.json").read_text(encoding="utf-8"))
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        source = json.loads((path / "schedule_metadata.json").read_text(encoding="utf-8"))
        completion = json.loads((path / "completion_summary.json").read_text(encoding="utf-8"))
        activation = json.loads((path / "activation.json").read_text(encoding="utf-8"))
        schedule = read_csv(path / "schedule.tsv", "\t")
        requests = read_csv(path / "request_results.csv")
    except Exception:
        return False, "missing_or_invalid_files"
    if not campaign_compatible(config, attached_campaign, assignment, campaign):
        return False, "campaign_or_assignment"
    if metadata.get("status") != "ok" or metadata.get("suite") != gen.SUITE:
        return False, "driver_or_suite"
    if metadata.get("method") != config.method or source.get("seed") != seed:
        return False, "slot_identity"
    required = {
        "wire_version": 3,
        "bloom_bits": 1024,
        "bloom_hash_count": 3,
        "staged_fallback": True,
        "fallback_delay_ms": 1500,
        "query_table_timeout_ms": 7000,
        "controller_timeout_ms": 8000,
        "max_hop_count": 12,
    }
    if any(metadata.get(key) != value for key, value in required.items()):
        return False, "runtime_configuration"
    if any(source.get(key) != value for key, value in required.items()):
        return False, "workload_configuration"
    if (
        source.get("post_bins") != 64
        or source.get("catalog_size_per_cluster") != 512
        or source.get("distribution") != "zipf"
        or source.get("measurement_requests") != 11264
    ):
        return False, "workload_shape"
    image_path = path / "runtime_image_id.before_launch"
    if not image_path.is_file() or image_path.read_text(encoding="utf-8").strip() != EXPECTED_IMAGE:
        return False, "runtime_image_snapshot"
    if metadata.get("runtime_image_id") != EXPECTED_IMAGE:
        return False, "runtime_image_metadata"
    if len(schedule) != 11264 or len(requests) != 11264:
        return False, "request_row_count"
    if any(completion.get(field) != 11264 for field in ("scheduled", "issued", "completed")):
        return False, "completion_summary"
    if {row.get("schedule_sha256") for row in schedule} != {source.get("schedule_sha256")}:
        return False, "schedule_fingerprint"
    expected = {row["request_index"]: row for row in schedule}
    intervals = []
    for row in requests:
        scheduled = expected.get(row.get("request_index", ""))
        if scheduled is None or row.get("status") != "ok":
            return False, "request_status"
        if (
            row.get("actual_value_bytes") != scheduled.get("value_size_bytes")
            or row.get("actual_fnv1a64") != scheduled.get("expected_fnv1a64")
            or row.get("quiescence_ack_count") != "64"
        ):
            return False, "value_or_quiescence"
        try:
            start = int(row["request_start_mono_ns"])
            end = int(row["app_receive_mono_ns"])
        except (KeyError, TypeError, ValueError):
            return False, "latency"
        if end < start:
            return False, "clock_order"
        intervals.append((start, end))
    intervals.sort()
    if any(right[0] < left[1] for left, right in zip(intervals, intervals[1:])):
        return False, "request_overlap"
    if not activation.get("federation_ready_ns") or activation.get(
        "activation_apply_latency_ns", -1
    ) < 0:
        return False, "activation"
    if {file.stem for file in (path / "activation_acks").glob("*.tsv")} != {"node1", "node33"}:
        return False, "activation_acks"
    if not clock_ok(path / "clock_state.tsv") or not network_ok(path / "network_state.txt"):
        return False, "clock_or_network"

    trace = read_csv(path / "trace_breakdown.csv") if config.method == "nc" else []
    isolated, reason = isolation_ok(config, path, trace)
    if not isolated:
        return False, reason
    if config.method == "tcp-direct":
        return True, "ok_expected_tcp_direct_failed_completions"
    logs = "\n".join(
        file.read_text(encoding="utf-8", errors="replace")
        for file in (path / "logs").glob("node*.log")
    )
    if logs.count("cache backend: redis") < 64:
        return False, "redis_not_ready"
    if logs.count("wire configuration version=3") < 64 or logs.count(
        "bloom configuration bits=1024 hashes=3 staged_fallback=enabled"
    ) < 64:
        return False, "node_protocol_gate"
    if len(trace) != 11264:
        return False, "trace_row_count"
    if any(row.get("application_delivery_events") != "1" for row in trace):
        return False, "application_delivery_count"
    if sum(int(row.get("query_expired_unsatisfied_events") or 0) for row in trace):
        return False, "formal_query_expiry"
    if sum(row.get("trace_complete") == "1" for row in trace) < math.ceil(11264 * 0.99):
        return False, "trace_coverage"
    for batch in range(1, 65):
        remote = [
            row for row in trace
            if row.get("phase") == "post"
            and row.get("request_class") == "remote"
            and row.get("bin") == str(batch)
        ]
        local = [
            row for row in trace
            if row.get("phase") == "post"
            and row.get("request_class") == "local_control"
            and row.get("bin") == str(batch)
        ]
        if sum(row.get("trace_complete") == "1" for row in remote) < 127:
            return False, "remote_batch_trace_coverage"
        if sum(row.get("trace_complete") == "1" for row in local) < 31:
            return False, "local_batch_trace_coverage"
    if not snapshots_ok(path):
        return False, "snapshot_invalid"
    return True, reason


def candidates(config: Config, seed: int) -> list[tuple[Path, bool, str]]:
    output = []
    for path in attempts(config, seed):
        non_evidence = non_evidence_reason(config, path)
        output.append(
            (path, False, non_evidence)
            if non_evidence else (path, *validate_attempt(config, seed, path))
        )
    return output


def selected(config: Config, seed: int) -> list[Path]:
    return [path for path, valid, _ in candidates(config, seed) if valid][
        :TARGET_REPETITIONS
    ]


def planned_slots() -> list[tuple[int, int, str]]:
    output = []
    for repetition in range(1, TARGET_REPETITIONS + 1):
        for seed in gen.SEEDS:
            order = list(CONFIGS)
            if (seed + repetition) % 2 == 1:
                order.reverse()
            output.extend((repetition, seed, config.method) for config in order)
    return output


def status_payload() -> dict:
    assignment = load_assignment()
    rows = []
    qualified = 0
    attempt_count = 0
    for config in CONFIGS:
        counts = [len(selected(config, seed)) for seed in gen.SEEDS]
        config_attempts = sum(len(attempts(config, seed)) for seed in gen.SEEDS)
        config_evidence_attempts = sum(
            len(evidence_attempts(config, seed)) for seed in gen.SEEDS
        )
        qualified += sum(counts)
        attempt_count += config_attempts
        rows.append(
            {
                "config": config.slug,
                "qualified": sum(counts),
                "target": 20,
                "attempts": config_attempts,
                "evidence_attempts": config_evidence_attempts,
                "seed_counts": {f"seed{seed:03d}": count for seed, count in zip(gen.SEEDS, counts)},
            }
        )
    return {
        "campaign": load_campaign(verify_files=False)["campaign_fingerprint"],
        "assignment": assignment["assignment_sha256"],
        "status": "READY" if qualified == 40 else "COLLECTING",
        "evidence_level": "FORMAL" if qualified == 40 else "COLLECTING_NO_PARTIAL_STATS",
        "qualified": qualified,
        "target": 40,
        "attempts": attempt_count,
        "unit": service_state(UNIT),
        "configs": rows,
    }


def write_manifests() -> None:
    attempt_rows = []
    selection_rows = []
    for config in CONFIGS:
        for seed in gen.SEEDS:
            chosen = set(selected(config, seed))
            for index, (path, valid, reason) in enumerate(candidates(config, seed), 1):
                attempt_rows.append(
                    {
                        "config": config.slug,
                        "method": config.method,
                        "seed": seed,
                        "attempt_index": index,
                        "run_dir": str(path.resolve()),
                        "quality_status": (
                            "QUALIFIED" if valid else
                            "NON_EVIDENCE" if reason and (
                                reason.startswith("prior_campaign")
                                or reason.startswith("infrastructure_prelaunch")
                            )
                            else "REJECTED"
                        ),
                        "reason": reason,
                        "selected": int(path in chosen),
                    }
                )
            for repetition, path in enumerate(selected(config, seed), 1):
                selection_rows.append(
                    {
                        "config": config.slug,
                        "method": config.method,
                        "seed": seed,
                        "repetition": repetition,
                        "run_dir": str(path.resolve()),
                    }
                )
    atomic_csv(
        RESULTS / "attempt_manifest.csv",
        attempt_rows,
        [
            "config", "method", "seed", "attempt_index", "run_dir",
            "quality_status", "reason", "selected",
        ],
    )
    atomic_csv(
        RESULTS / "formal_selection.csv",
        selection_rows,
        ["config", "method", "seed", "repetition", "run_dir"],
    )
    atomic_json(RESULTS / "run_status.json", status_payload())


def validation_record(config: Config, seed: int, path: Path, valid: bool, reason: str) -> None:
    output = RESULTS / "validation" / config.slug / f"seed{seed:03d}" / f"{path.name}.json"
    campaign = load_campaign(verify_files=False)
    atomic_json(
        output,
        {
            "schema_version": 1,
            "validator_policy": POLICY_ID,
            "campaign": campaign["campaign_fingerprint"],
            "assignment": campaign["assignment_sha256"],
            "config": config.slug,
            "seed": seed,
            "run_dir": str(path.resolve()),
            "status": "READY" if valid else "REJECTED",
            "reason": reason,
        },
    )


def lock_execution():
    path = RESULTS / "execution.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("long-horizon formal execution lock is held") from exc
    return handle


def run_one(config: Config, seed: int, output: Path) -> tuple[bool, str]:
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "FEDERATION_FALLBACK_SUITE": gen.SUITE,
            "FEDERATION_V3_FALLBACK_WORKLOAD_ROOT": str(gen.DEFAULT_ROOT),
            "FEDERATION_FALLBACK_DATASET_LABEL": "formal 64-POST single-host logical federation",
            "FEDERATION_V3_FALLBACK_BUILD_NO_CACHE": "0",
            "SKIP_BUILD": "1",
            "RSYNC_LOOP_ITERATIONS": "2147483647",
            "FEDERATION_CAMPAIGN_LOCK_FILE": str(CAMPAIGN),
            "FEDERATION_ASSIGNMENT_FILE": str(ASSIGNMENT),
        }
    )
    completed = subprocess.run(
        [
            str(SCRIPTS / "run_federation_v3_bf1024_h3_fallback_once.sh"),
            gen.scenario_name(seed),
            config.method,
            str(output),
        ],
        cwd=REPO,
        env=env,
        check=False,
    )
    if completed.returncode == 3:
        raise RuntimeError("Kathara lab lock is held; no attempt was started")
    valid, reason = validate_attempt(config, seed, output)
    validation_record(config, seed, output, valid, reason)
    write_manifests()
    return valid, reason


def execute() -> int:
    preflight(allow_active_self=True)
    lock = lock_execution()
    try:
        for repetition, seed, method in planned_slots():
            config = next(item for item in CONFIGS if item.method == method)
            if len(selected(config, seed)) >= repetition:
                continue
            while len(selected(config, seed)) < repetition:
                if len(evidence_attempts(config, seed)) >= MAX_ATTEMPTS:
                    write_manifests()
                    raise RuntimeError(
                        f"attempt budget exhausted before repetition {repetition}: "
                        f"{config.slug} seed{seed:03d}"
                    )
                output = next_attempt(config, seed)
                valid, reason = run_one(config, seed, output)
                print(
                    f"repetition={repetition} seed={seed:03d} method={method} "
                    f"result={'QUALIFIED' if valid else 'REJECTED'} reason={reason}",
                    flush=True,
                )
        write_manifests()
        current = status_payload()
        print(json.dumps(current, sort_keys=True), flush=True)
        return 0 if current["qualified"] == current["target"] else 2
    finally:
        lock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("--write", action="store_true")
    preflight_parser = sub.add_parser("preflight")
    preflight_parser.add_argument("--inside-unit", action="store_true")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--execute", action="store_true")
    run_parser.add_argument("--inside-unit", action="store_true")
    sub.add_parser("status")
    validate_parser = sub.add_parser("validate-run")
    validate_parser.add_argument("--method", choices=("nc", "tcp-direct"), required=True)
    validate_parser.add_argument("--seed", type=int, choices=gen.SEEDS, required=True)
    validate_parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "freeze":
        if not args.write:
            raise SystemExit("freeze requires --write")
        gen.validate(gen.DEFAULT_ROOT)
        print(json.dumps(freeze(), indent=2, sort_keys=True))
        return 0
    if args.command == "preflight":
        print(json.dumps(preflight(allow_active_self=args.inside_unit), indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps(status_payload(), indent=2, sort_keys=True))
        return 0
    if args.command == "validate-run":
        valid, reason = validate_attempt(Config(args.method), args.seed, args.run_dir)
        print(f"{'READY' if valid else 'REJECTED'} reason={reason} run={args.run_dir}")
        return 0 if valid else 2
    if not args.execute:
        print(json.dumps({"dry_run": True, "slots": planned_slots()}, indent=2))
        return 0
    return execute()


if __name__ == "__main__":
    raise SystemExit(main())
