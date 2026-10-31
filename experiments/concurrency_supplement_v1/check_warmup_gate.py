#!/usr/bin/env python3
"""Deterministic unit test for pilot.py's warm-up gate (supplementary 4.4).

The functions are extracted from pilot.py by text so that importing the pilot does
not drag in module-level paths. Usage: test_warmup_gate.py [path/to/pilot.py]
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PILOT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "pilot.py"
src = PILOT.read_text()
start = src.index("_T95_DF2=")
end = src.index("def peak(rows,per_node=False):")
ns = {}
exec(src[start:end], ns)
warmup_gate, ols = ns["warmup_gate"], ns["_ols_slope_ci"]

def main():
    FAILS = 0


    def check(name, ok, detail=""):
        global FAILS
        print(f"{name}: {'PASS' if ok else 'FAIL'} {detail if not ok else ''}")
        if not ok:
            FAILS += 1


    def ledger(series_a, series_b):
        rows = []
        for lo, series in ((1, series_a), (33, series_b)):
            for b, val in enumerate(series, 1):
                for node in (lo, lo + 1):
                    rows.append({"phase": "pre", "bin": str(b), "requester": str(node),
                                 "status": "ok", "total_latency_ns": str(int(val))})
        return rows


    check("flat_plateau_passes", warmup_gate(ledger([200e6] * 32, [210e6] * 32))["passed"])
    r = warmup_gate(ledger([200e6] * 32, [210e6] * 32))
    check("plateau_recorded", abs(r["domains"]["A"]["plateau_ns"] - 200e6) < 1)
    check("dof_is_2", r["domains"]["A"]["dof"] == 2)

    r = warmup_gate(ledger([400e6 - 8e6 * i for i in range(32)], [420e6 - 8e6 * i for i in range(32)]))
    check("still_falling_fails", not r["passed"], str(r["reasons"]))
    check("reason_names_slope", any("slope_ci_excludes" in x for x in r["reasons"]))
    check("ci_reported", r["domains"]["A"]["ci_high_ns_per_bin"] < 0)

    r = warmup_gate(ledger([200e6] * 32, [420e6 - 8e6 * i for i in range(32)]))
    check("one_domain_only_fails", not r["passed"])
    check("passing_domain_marked", r["domains"]["A"]["contains_zero"] is True)
    check("failing_domain_marked", r["domains"]["B"]["contains_zero"] is False)

    r = warmup_gate(ledger([200e6] * 2, [200e6] * 2))
    check("insufficient_bins_fails", not r["passed"] and any("insufficient" in x for x in r["reasons"]))
    check("degenerate_returns_none", ols([1, 1, 1], [1.0, 2.0, 3.0]) is None)

    print("FAILURES:", FAILS)
    return 1 if FAILS else 0

if __name__ == "__main__":
    raise SystemExit(main())
