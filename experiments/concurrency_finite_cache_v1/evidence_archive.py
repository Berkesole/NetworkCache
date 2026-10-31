#!/usr/bin/env python3
"""Shared evidence archive + independent verification.

Sealing order (strict): the caller must have finished every acceptance step and
written its final PASS/FAIL verdict and exit codes BEFORE calling `seal`. This
tool then:

  1. waits for no further writer (the caller is responsible for `wait`ing on its
     background processes first);
  2. lists every regular file under the evidence root, EXCLUDING the manifest
     itself, the verification receipt, and any temporary files, so the manifest
     never references itself;
  3. writes a sorted `<sha256>  <relpath>` manifest;
  4. independently re-reads every entry and recomputes its hash, and also checks
     that no non-excluded file is missing from the manifest;
  5. writes a receipt OUTSIDE the manifest's covered set with the final status
     and the manifest's own hash.

After `seal`, no covered file may be modified; the receipt is the only thing the
caller may still read/write. A failed verification exits non-zero so an outer
script can refuse to report overall success.

Usage:
  evidence_archive.py seal   <root> [--manifest NAME] [--receipt PATH]
  evidence_archive.py verify <root> [--manifest NAME]
  evidence_archive.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_MANIFEST = "evidence-manifest.txt"
DEFAULT_RECEIPT = "manifest-verification.txt"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _excluded(path: Path, root: Path, extra: set[Path]) -> bool:
    if path in extra:
        return True
    rel_parts = path.relative_to(root).parts
    if any(part == "tmp" or part.endswith(".tmp") for part in rel_parts):
        return True
    return path.name.endswith(".tmp")


def _collect(root: Path, excluded: set[Path]) -> list[Path]:
    return [p for p in sorted(root.rglob("*"))
            if p.is_file() and not _excluded(p, root, excluded)]


def _write_manifest(root: Path, manifest_path: Path, excluded: set[Path]) -> int:
    files = _collect(root, excluded | {manifest_path})
    lines = [f"{_sha256(p)}  {p.relative_to(root)}" for p in files]
    manifest_path.write_text("\n".join(lines) + ("\n" if lines else ""),
                             encoding="utf-8")
    return len(lines)


def verify(root: Path, manifest_path: Path,
           excluded: set[Path] | None = None) -> tuple[bool, list[str]]:
    # Always exclude the manifest and the designated receipt: neither is covered
    # by the manifest, so a standalone verify must not report them as unlisted.
    excluded = (excluded or set()) | {manifest_path, root / DEFAULT_RECEIPT}
    reasons: list[str] = []
    listed: dict[str, str] = {}
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, [f"manifest_unreadable:{exc}"]
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            reasons.append(f"manifest_bad_line:{lineno}")
            continue
        digest, rel = parts[0], parts[1]
        listed[rel] = digest
        path = root / rel
        if not path.is_file():
            reasons.append(f"registered_file_missing:{rel}")
            continue
        if _sha256(path) != digest:
            reasons.append(f"registered_hash_mismatch:{rel}")

    on_disk = {str(p.relative_to(root)) for p in _collect(root, excluded)}
    for rel in sorted(on_disk - set(listed)):
        reasons.append(f"unlisted_file:{rel}")
    return (not reasons), reasons


def seal(root: Path, manifest_name: str, receipt_path: Path) -> int:
    manifest_path = root / manifest_name
    excluded = {receipt_path}
    count = _write_manifest(root, manifest_path, excluded)
    ok, reasons = verify(root, manifest_path, excluded)
    receipt = {
        "status": "ok" if ok else "failed",
        "entries": count,
        "manifest": manifest_path.name,
        "manifest_sha256": _sha256(manifest_path),
        "verified_at_epoch_s": int(time.time()),
        "reasons": reasons,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if not ok:
        print("MANIFEST_VERIFY_FAILED: " + "; ".join(reasons), file=sys.stderr)
        return 1
    print(f"MANIFEST_VERIFIED entries={count} sha256={receipt['manifest_sha256']}")
    return 0


def _self_test() -> int:
    failures = 0

    def check(name: str, condition: bool) -> None:
        nonlocal failures
        print(f"{name}: {'PASS' if condition else 'FAIL'}")
        if not condition:
            failures += 1

    with tempfile.TemporaryDirectory(prefix="evidence-archive-") as tmp:
        root = Path(tmp) / "evidence"
        root.mkdir()
        (root / "summary.txt").write_text("PASS\n", encoding="utf-8")
        (root / "logs").mkdir()
        (root / "logs" / "run.log").write_text("line\n", encoding="utf-8")
        receipt = root / DEFAULT_RECEIPT

        rc = seal(root, DEFAULT_MANIFEST, receipt)
        check("seal_succeeds", rc == 0)
        manifest = root / DEFAULT_MANIFEST
        text = manifest.read_text(encoding="utf-8")
        check("manifest_excludes_itself", DEFAULT_MANIFEST not in text)
        check("manifest_excludes_receipt", DEFAULT_RECEIPT not in text)
        check("manifest_lists_covered_files",
              "summary.txt" in text and "logs/run.log" in text)
        ok, _ = verify(root, manifest, {receipt})
        check("verify_after_seal_ok", ok)

        # A file modified after sealing fails verification.
        (root / "summary.txt").write_text("PASS-but-tampered\n", encoding="utf-8")
        ok, reasons = verify(root, manifest, {receipt})
        check("modified_registered_file_fails",
              not ok and any(r.startswith("registered_hash_mismatch") for r in reasons))

        # A deleted registered file fails.
        (root / "logs" / "run.log").unlink()
        ok, reasons = verify(root, manifest, {receipt})
        check("deleted_registered_file_fails",
              not ok and any(r.startswith("registered_file_missing") for r in reasons))

        # A new unlisted file fails (post-seal write).
        (root / "summary.txt").write_text("PASS\n", encoding="utf-8")
        (root / "extra.log").write_text("late\n", encoding="utf-8")
        ok, reasons = verify(root, manifest, {receipt})
        check("unlisted_file_fails",
              not ok and any(r.startswith("unlisted_file") for r in reasons))

        # The final PASS line written before sealing is covered by the hash.
        (root / "extra.log").unlink()
        (root / "summary.txt").write_text("PASS\n", encoding="utf-8")
        rc = seal(root, DEFAULT_MANIFEST, receipt)
        ok, _ = verify(root, manifest, {receipt})
        check("rescal_after_final_verdict_ok", rc == 0 and ok)
        check("receipt_outside_manifest", DEFAULT_RECEIPT not in
              manifest.read_text(encoding="utf-8"))
        receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
        check("receipt_records_manifest_hash",
              receipt_data.get("status") == "ok" and
              len(receipt_data.get("manifest_sha256", "")) == 64)

    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", nargs="?", choices=["seal", "verify"])
    parser.add_argument("root", nargs="?")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--receipt", default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.action or not args.root:
        parser.error("action and root are required (or --self-test)")
    root = Path(args.root)
    if not root.is_dir():
        print(f"evidence root not found: {root}", file=sys.stderr)
        return 3

    if args.action == "verify":
        ok, reasons = verify(root, root / args.manifest)
        if ok:
            print("MANIFEST_VERIFY_OK")
            return 0
        print("MANIFEST_VERIFY_FAILED")
        for reason in reasons:
            print(f"  - {reason}")
        return 1

    receipt = Path(args.receipt) if args.receipt else root / DEFAULT_RECEIPT
    return seal(root, args.manifest, receipt)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))