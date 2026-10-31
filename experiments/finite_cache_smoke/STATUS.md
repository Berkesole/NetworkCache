# finite-cache six-slot smoke — progress log

## Round 5 (2026-09-20): corrected run `s2-e2155c84` — 6/6 PASS

The round-3/4 run `s1-109b933d` is KEPT UNCHANGED; its arrival-process and
conclusion corrections are in `acceptance/round5-CORRECTION.md`.

Defects fixed before this run:
1. **Arrival process**: the runner had passed a fixed `arrival=[j*1e6]` array, so
   every slot (including r128) used 1 ms spacing and the frozen generator was
   never called. `smoke_plan.py` now freezes the plan with
   `controller.offsets(count, model, rate, seed=9173)` before the run, and the
   runner loads/verifies it (wrong rate/seed/replaced array/tampered hash/
   non-monotonic refuse to start). Actual r32 `[6.3ms,35.7ms,65.8ms,143.0ms]` vs
   r128 `[1.6ms,8.9ms,16.4ms,35.7ms]`; same-rate methods share arrays.
2. **Container scope**: only containers mounting THIS lab's `shared/` are used,
   with protocol nodes exactly node1..16; the two-labs fixture proves the other
   lab is never selected.
3. **Acceptance**: `smoke_accept.py` replaces the simplified PASS — strict trace
   parsing, one cache_config per expected node, per-node replica replay, owner
   protection cross-check, real-clock multi-instance monitoring with preserved
   failures/exit codes, NC/TCP separation, required-gate presence.

Result (6/6 PASS, ~35-38 s per slot): r32/r128 x {NC bypass0, NC LRU 1 MiB,
TCP Direct}. NC slots have 15 gates, TCP slots 8. Success covers 64 NC
instance-slots. Evidence sealed (1887 entries, MANIFEST_VERIFY_OK) and
re-verified locally in `acceptance-s2-e2155c84/s2-e2155c84/`.

Self-tests: matrix 17/17, plan 12/12, accept 17/17, scope 7/7.

## Round 3 (2026-09-20): six-slot smoke COMPLETE

**Status: 6/6 slots PASS.** Run id `s1-109b933d`.

| slot | rate | method | status | wall(s) |
|---|---|---|---|---|
| r32 NC bypass0 | 32 | NC bypass, budget 0 | PASS | 36.8 |
| r32 NC lru1m | 32 | NC lru, 1 MiB | PASS | 35.0 |
| r32 TCP Direct | 32 | tcp-direct | PASS | 34.4 |
| r128 NC bypass0 | 128 | NC bypass, budget 0 | PASS | 36.5 |
| r128 NC lru1m | 128 | NC lru, 1 MiB | PASS | 35.3 |
| r128 TCP Direct | 128 | tcp-direct | PASS | 34.2 |

Sealed evidence (1668 entries, `MANIFEST_VERIFY_OK`) returned to
`experiments/finite_cache_smoke/acceptance-s1-109b933d/s1-109b933d/`.
`evidence_archive.py verify` re-passes locally. No containers were left running.

## Round 2 (2026-09-20): infrastructure built, slot 1 verified PASS

**Status at that time: 1/6 slots PASS.**

### Environment / identities
- local commit: `92d52a56` (runner + renderer cache-env patch + fixes)
- lab image: `rsync-router:concurrency-finite-cache-smoke-81c22fe9`
  `sha256:f418668fd2ef7e695a45db2673bdeff66a3d49f2d94ca3bbcc2409e910355d3d`
  built from the repo root `Dockerfile` with `ENABLE_REDIS=ON`
  (has `/app/build/rsync-node-dev`, `/etc/redis/redis.conf.template`, python3,
  redis-server). The acceptance image `-03caf15c` is NOT lab-capable (no redis
  template) and was NOT modified.
- smoke root (remote): `/home/simpleai/zhengzihao/networkcache-concurrency-finite-cache-v1/smoke/s1-109b933d/`
  - `source/` finite-cache tree, `bin/kathara` + `backend.json` + `kathara.conf`
    copied read-only from `networkcache-concurrency-slides-v5`, `inputs/` symlink
    to that root's frozen inputs, `lab/`, `raw/`.
- reused frozen building blocks: `experiments/concurrency_slides_v5/controller.py`
  and `network_backend.py` (unmodified), plus the frozen 6-key smoke request plan.
- fixed conditions: 16 hosts, 64 KiB, catalog 512, `poisson`, seed 1, rep 1,
  `arrival_seed=9173`, wire v3, Bloom 1024/3, staged fallback, loop_sleep 25 ms,
  link 2 ms / 1000 Mbit / queue 1000 / MTU 1500, Redis maxmemory 0 / noeviction /
  require-empty.

### Verified PASS slot
`finite-smoke-n16-r32-nc-lru1m`, attempt005 — `STATUS ok`, wall 35.0 s, all 10
acceptance gates qualified:
- `requests_complete` (32 setup + 16 measurement, exactly once, audit agrees)
- `exactly_once_delivery`, `content_hash` (frozen controller audit)
- `cache_config` 16/16 nodes; `cache_policy_effective` modes={lru} budgets={1048576}
- `resident_bytes_within_budget` peak=1048576 (= budget)
- `replica_evict_gt_0` evicts=**79** (real eviction, not an empty test)
- `owner_not_evicted`
- `redis_monitor_all_instances` instances=**16** samples=**480**
- `no_fatal`

Evidence: `.../smoke/s1-109b933d/raw/smoke/finite-smoke-n16-r32-nc-lru1m/attempt005/`
(`result.json`, `evidence/{control,monitor,node-logs,traces}`, `monitor/nodeN.txt`,
`schedule.json`, `environment.json`, `driver.log`).

### Failed attempts (kept, not hidden)
| attempt | phase | cause | fix |
|---|---|---|---|
| 001 | bringup | `controller` has no `out()` helper | local `out()` |
| 002 | run | `setup` read from `catalog.tsv` instead of `schedule.tsv` → empty list → `IndexError` in `controller.execute` | read setup from `schedule.tsv` |
| 003 | run | Redis INFO CRLF: `\r` survived `tr '\n' ';'` and `splitlines()` split each sample → 0 monitor records; completion gate counted setup rows | `tr -d '\r'`, split on `\n` only |
| 004 | run | completion gate compared against measurement indexes only | compare against setup ∪ measurement |

Attempt 001 also demonstrated the integration: 16/16 nodes ready with
`cache_config` `budget_mode=lru`, `budget_bytes=1048576`, owner/replica
namespaces, `redis_maxmemory=0`, `noeviction`, `require_empty=1`.

### Remaining work (next round)
1. Run the other 5 slots (r32 nc-bypass0, r32 tcp-direct, r128 nc-bypass0,
   r128 nc-lru1m, r128 tcp-direct) with the verified runner.
2. Add the run-before manifest generation (`smoke_matrix.py manifest`), per-slot
   provenance, and wire `evidence_archive.py` sealing + independent verification.
3. Return evidence locally and produce the final PASS/FAIL/NOT_RUN table.
4. TCP Direct slots must use transport evidence and must NOT require or fabricate
   cache/Redis evidence (already encoded in `smoke_matrix.METHOD_CONFIG`).