#!/usr/bin/env python3
"""Validate collected domain pairs and publish only complete-stage selections."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

import analyze_results
import spec
import validate_attempt


HERE = Path(__file__).resolve().parent
COLLECTED = HERE / "collected"
DERIVED = HERE / "derived"


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def validate_stage(stage: str) -> dict:
    collected = HERE / 'collected-pre-isolation-v2' if stage == 'formal' else COLLECTED
    roots = {
        role: collected / role / stage / role for role in ("domain-a", "domain-b")
    }
    slots = sorted({path.name for path in roots["domain-a"].glob("*") if path.is_dir()}
                   & {path.name for path in roots["domain-b"].glob("*") if path.is_dir()})
    selected = []
    rejected = []
    for slot in slots:
        attempts_a = {path.name: path for path in (roots["domain-a"] / slot).glob("attempt*")}
        attempts_b = {path.name: path for path in (roots["domain-b"] / slot).glob("attempt*")}
        for attempt in sorted(set(attempts_a) & set(attempts_b)):
            output = DERIVED / "validation" / validate_attempt.POLICY / ('formal-pre-isolation-v2' if stage == 'formal' else stage) / slot / attempt
            try:
                if not (output / "validation.json").is_file():
                    record = validate_attempt.validate(attempts_a[attempt], attempts_b[attempt], output)
                else:
                    record = json.loads((output / "validation.json").read_text(encoding="utf-8"))
                match = re.fullmatch(
                    r"(nc|tcp-direct)-seed(\d{3})(?:-rep([12])|-post(\d{3}))?", slot
                )
                if not match:
                    raise ValueError(f"invalid slot name {slot}")
                selected.append({
                    "stage": stage, "slot": slot, "method": match.group(1),
                    "seed": int(match.group(2)), "repetition": int(match.group(3) or 0),
                    "post_batches": int(match.group(4) or 0),
                    "attempt": attempt, "domain_a": str(attempts_a[attempt]),
                    "domain_b": str(attempts_b[attempt]),
                    "decomposition_csv": str(output / "request_decomposition.csv"),
                    "validation_sha256": hashlib.sha256(
                        (output / "validation.json").read_bytes()
                    ).hexdigest(),
                })
                break
            except Exception as exc:
                rejected.append({"slot": slot, "attempt": attempt, "reason": repr(exc)})
    selection_path = DERIVED / f"{stage}_selection.csv"
    if selected:
        write_csv(selection_path, selected)
    complete = False
    if stage == "formal":
        complete = len(selected) == 40 and len({row["slot"] for row in selected}) == 40
        if complete:
            analyze_results.formal(selected, DERIVED / "formal_results.json")
    elif stage == "calibration":
        by_seed = {}
        for row in selected:
            if row["seed"] not in by_seed or row["post_batches"] > by_seed[row["seed"]]["post_batches"]:
                by_seed[row["seed"]] = row
        selected = list(by_seed.values())
        complete = set(by_seed) == set(spec.CALIBRATION_SEEDS)
        for row in selected:
            source = json.loads((Path(row["domain_a"]) / "schedule_metadata.json").read_text())
            analysis_dir = DERIVED / "calibration" / f"seed{row['seed']:03d}"
            batches = analyze_results.batch_rows(analyze_results.read_csv(Path(row["decomposition_csv"])))
            report = analyze_results.convergence(row["seed"], batches, int(source["post_batches"]))
            analysis_dir.mkdir(parents=True, exist_ok=True)
            analyze_results.write_csv(analysis_dir / "batch_means.csv", batches)
            (analysis_dir / "convergence_report.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    else:
        complete = len(selected) == 1
    manifest = {
        "stage": stage, "status": "READY" if complete else "COLLECTING_NO_PARTIAL_STATS",
        "selected_count": len(selected), "rejected": rejected,
        "selection_sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest()
        if selection_path.is_file() else None,
    }
    path = DERIVED / f"{stage}_collection_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("smoke", "calibration", "formal"))
    args = parser.parse_args()
    print(json.dumps(validate_stage(args.stage), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
