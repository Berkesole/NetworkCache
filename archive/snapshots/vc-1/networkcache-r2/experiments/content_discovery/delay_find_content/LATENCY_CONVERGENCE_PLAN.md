# Latency Convergence Experiment Plan

## Summary

Add a `latency_convergence` experiment to answer a question that the current
distanceblock report does not cover: how quickly NetworkCache moves from cold
discovery into a stable warm/cache-hit state, and how latency decreases over
repeated requests.

The experiment keeps Kathara as the primary environment and uses the Kua-DC v3
pure spine/leaf topology. Hosts run NetworkCache; leaf/spine devices only do
L3 forwarding. The experiment adds workloads, analyzers, reports, and curated
assets only. It must not change the REQUEST/DATA wire format or the default
protocol semantics.

## Key Changes

- Add three deterministic convergence workloads:
  - `convergence-controlled-s2000-seed001`: primary, most interpretable; 15
    tracked keys, distance-aware requester/provider placement, 8 repeated
    requests per key.
  - `convergence-zipf-s2000-seed001`: realistic hotspot workload; 64 keys,
    Zipf alpha 1.1, 120 measurement requests.
  - `convergence-uniform-s2000-seed001`: weak-locality control; 64 keys,
    uniform key sampling, 120 measurement requests.
- Each run uses 16 warm-up requests and 120 measurement requests. Request
  spacing defaults to 2000 ms. If Kathara smoke is unstable, create isolated
  fallback scenarios with `s2500`; do not overwrite `s2000`.
- Controlled workload distance coverage:
  - 16-host: cold provider hop classes 2, 3, and 4, with 5 tracked keys per
    class.
  - 32-host: cold provider hop classes 2, 3, 4, 5, and 6, with 3 tracked keys
    per class.
- Repeated requests must rotate requester hosts so the experiment does not
  only measure same-requester local cache hits.
- Extend metadata with:
  - `round_index`
  - `key_occurrence_index`
  - `popularity_rank`
  - `distance_class`
  - `expected_cold_provider_hop`
  - `measurement_position`
  - `measurement_elapsed_ms`
  - `request_bin`
  - `workload_family=latency_convergence`
- Continue using `RSYNC_EVENT_TRACE` C++ internal trace as the latency source.
  Legacy stdout logs remain only for completion sanity and debugging.

## Implementation Changes

- Extend `generate_workloads_v3.py` to generate Kathara scenarios, Docker
  mirror configs, and metadata sidecars for:
  - `kua-dc-v3-16-convergence-controlled-s2000-seed001`
  - `kua-dc-v3-32-convergence-controlled-s2000-seed001`
  - `kua-dc-v3-16-convergence-zipf-s2000-seed001`
  - `kua-dc-v3-32-convergence-zipf-s2000-seed001`
  - `kua-dc-v3-16-convergence-uniform-s2000-seed001`
  - `kua-dc-v3-32-convergence-uniform-s2000-seed001`
- Extend trace analysis so the new metadata fields pass through to request
  summary and trace breakdown CSVs.
- Add a convergence aggregator that outputs:
  - `convergence_merged.csv`
  - `convergence_by_round.csv`
  - `convergence_by_bin.csv`
  - `convergence_by_occurrence.csv`
  - `convergence_summary.csv`
- Define convergence metrics:
  - `cold_start_mean`: mean miss latency for round 1.
  - `steady_state_mean`: mean miss latency for the last 20% of measurement
    requests.
  - `converged`: rolling-window mean stays below `steady_state_mean * 1.10`
    for 3 consecutive windows.
  - `requests_to_converge`: first measurement request index that satisfies
    the convergence rule.
  - `time_to_converge_ms`: elapsed measurement time at that request.
- Use rolling average with window size 10 for report figures.
- Create paper-facing assets under
  `experiments/content_discovery/delay_find_content/paper_assets/latency-convergence-v1/`.
- Generate reports:
  - `experiments/reports/overall/networkcache_latency_convergence_report.pdf`
  - `experiments/reports/overall/networkcache_latency_convergence_report_en.pdf`

## Execution Plan

1. Generate all convergence scenarios and metadata with the v3 workload
   generator.
2. Run Kathara smoke for 16-host and 32-host controlled, Zipf, and uniform
   workloads, one run each.
3. Require 120/120 complete measurement trace rows for each smoke run.
4. Collect Kathara `success20` for 16-host controlled first, then generate a
   16-host convergence report draft.
5. Collect Kathara `success20` for 32-host controlled.
6. After controlled passes, collect Zipf and Uniform `success20` for both
   topologies.
7. Run Docker mirror only after the corresponding Kathara workload is complete.
   Docker remains a host-overlay pipeline sanity check, not the primary report
   source.

## Test Plan

- Generator correctness:
  - Fixed seed generation is byte-identical across repeated runs.
  - Provider/requester nodes are hosts only; leaf/spine devices never appear in
    rsync workload data or requests.
  - 16-host controlled covers cold hop classes 2, 3, and 4.
  - 32-host controlled covers cold hop classes 2, 3, 4, 5, and 6.
  - Each convergence scenario has 16 warm-up requests and 120 measurement
    requests.
- Smoke acceptance:
  - Each Kathara smoke run generates `summary.csv`, `trace_summary.csv`, and
    `trace_breakdown.csv`.
  - Measurement trace completion is 120/120.
  - Latency fields are non-negative.
- Success20 acceptance:
  - Each topology/workload has 20 successful runs.
  - Each success20 batch has 2400 measurement rows.
  - `convergence_summary.csv` includes `cold_start_mean`,
    `steady_state_mean`, `requests_to_converge`, and
    `time_to_converge_ms`.
  - Controlled and Zipf should show latency decreasing with request index.
  - Uniform may show weaker warm benefit, but completion must be complete.
- Report acceptance:
  - PDF reports exist and are non-empty.
  - Curves show rolling averages rather than only two cold/warm bars.
  - Reports distinguish controlled, Zipf, and Uniform workloads.
  - Reports do not claim strong warm benefit for Uniform unless the data shows
    it.
- Static checks:
  - `python3 -m py_compile experiments/content_discovery/delay_find_content/scripts/*.py experiments/reports/*.py`
  - `bash -n` for Kathara/Docker runner scripts.
  - `git diff --check`

## Assumptions

- This experiment studies warm-up and convergence dynamics. It complements the
  current distanceblock cold-vs-warm mechanism validation; it does not replace
  it.
- Kathara is the primary result environment.
- Default request spacing is `s2000`; fallback `s2500` is added only if smoke
  is incomplete.
- Results use `success20`: analyze 20 successful runs, not necessarily 20
  consecutive successful runs.
- Rolling window size is 10 measurement requests.
- Convergence threshold is 110% of steady-state mean for 3 consecutive
  windows.
- No protocol semantic changes are allowed.
