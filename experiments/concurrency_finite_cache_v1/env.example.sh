# concurrency-finite-cache-v1 -- cache policy environment (example)
#
# Source this file (or pass the same variables through the runner) when
# launching a finite-cache run. RSYNC_CACHE_POLICY=legacy is the default and
# keeps the original Node's normal behaviour and backend-exception propagation
# (flat key space, no budget, no LRU, no cache events); it is NOT a byte-for-byte
# compatibility promise -- the new trace schema, fatal diagnostics and the
# owner seed-once/seal validation are outside it. Every non-legacy policy turns
# on the owner/replica namespaces, the cache event contract and (for lru) the
# application-level byte budget.
#
# Rules enforced at startup (an invalid combination fails the run, it never
# falls back to a silent default):
#   * legacy must not carry any RSYNC_REPLICA_BUDGET_* variable;
#   * lru requires a budget, given either as bytes or as an object count;
#   * unlimited is a separate value and is never gated by a budget;
#   * set at most one of RSYNC_REPLICA_BUDGET_BYTES / RSYNC_REPLICA_BUDGET_OBJECTS.

# ---- one of the four policies ---------------------------------------------
# RSYNC_CACHE_POLICY=legacy      # default: flat key space, no budget, no events
# RSYNC_CACHE_POLICY=bypass      # never admit a replica; forward/deliver anyway
# RSYNC_CACHE_POLICY=lru         # byte-budget LRU (requires a budget below)
# RSYNC_CACHE_POLICY=unlimited   # same interfaces/accounting, no budget

# ---- budget (lru only) ----------------------------------------------------
# First-round candidates: 0 | 262144 | 524288 | 1048576 | 2097152 | 4194304 |
# 16777216 bytes, plus unlimited. 33554432 (32 MiB) is a sufficiency control
# only, never a first-round treatment arm.
# RSYNC_REPLICA_BUDGET_BYTES=1048576          # 1 MiB = 16 objects of 64 KiB
# RSYNC_REPLICA_BUDGET_OBJECTS=16             # equivalent object-count form
# RSYNC_REPLICA_BUDGET_OBJECT_BYTES=65536     # optional; default 65536

# ---- observability / integrity --------------------------------------------
# Exact content verification is mandatory in lru/bypass/unlimited and cannot be
# turned off (legacy keeps the historical meaning of this variable). Setting it
# to 0 in a finite-cache mode fails at startup.
# RSYNC_CACHE_GAUGE_INTERVAL_MS=100           # periodic cache_gauge; 0 = off
# RSYNC_EVENT_TRACE_SCHEMA_VERSION=2

# ---- Redis instance acceptance / monitoring --------------------------------
# The launch script must point at an EXPLICITLY chosen, isolated experiment
# instance and must NOT use a default Redis address/DB. verifyRedisStartup()
# refuses (never FLUSHDBs) unless: explicit absolute socket path, identity and
# version/run_id recorded, maxmemory=0, maxmemory-policy=noeviction, DBSIZE=0.
# A dedicated monitoring connection samples INFO (used_memory, evicted_keys,
# run_id) on the interval below; an eviction-count increase, a counter rollback,
# a run_id change or an unreadable INFO fails the run. used_memory is Redis
# total memory and is NOT the application replica value bytes.
# REDIS_SOCKET_PATH=/tmp/rsync-redis-exp/redis.sock
# RSYNC_REDIS_INSTANCE_ID=finite-cache-exp
# RSYNC_CACHE_MONITOR_INTERVAL_MS=1000
# Notebook: the Redis path is NOT compiled or measured on a Redis backend in
# this round (no hiredis/redis++/redis-server locally), and a Redis timeout does
# not prove the command was not applied.
# RSYNC_REDIS_MAXMEMORY=0
# RSYNC_REDIS_MAXMEMORY_POLICY=noeviction
# RSYNC_REDIS_REQUIRE_EMPTY=1