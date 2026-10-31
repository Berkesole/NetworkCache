#pragma once

#include <cstdint>
#include <string>

namespace rsync_cache {

// Snapshot of one Redis instance, read from a SEPARATE monitoring connection
// (never one query per DATA operation). The fields mirror what INFO/DBSIZE
// return, but the type is dependency-free so the acceptance rules below can be
// unit tested without a Redis server.
struct RedisInstanceInfo {
    std::string socket_path;       // the path actually connected to
    std::string identity;          // operator-provided instance identity/name
    std::string version;           // redis_version
    std::string run_id;            // INFO run_id; changes on restart
    long long uptime_seconds = -1;
    long long maxmemory = -1;      // bytes; 0 means unlimited
    std::string maxmemory_policy;  // e.g. noeviction
    long long db_keys = -1;        // DBSIZE of the experiment DB
    long long used_memory = -1;    // Redis TOTAL memory, NOT app replica bytes
    long long evicted_keys = -1;   // INFO stats evicted_keys
    bool info_readable = false;    // false when INFO could not be read at all
    // Empty when every monitored field parsed cleanly. Otherwise a
    // machine-readable reason (e.g. "invalid maxmemory=0garbage", "duplicate
    // evicted_keys", "negative used_memory"). Callers MUST reject when this is
    // non-empty instead of reading the default values.
    std::string parse_error;

    bool fieldsValid() const { return info_readable && parse_error.empty(); }
};

struct RedisAcceptance {
    bool accepted = false;
    std::string reason;  // empty when accepted

    explicit operator bool() const { return accepted; }
};

// Parses a raw INFO reply. Pure and dependency-free so it can be tested against
// a captured sample without a Redis server. `used_memory` is the Redis total and
// is never re-labelled as application replica bytes.
RedisInstanceInfo parseRedisInfo(const std::string& info_text,
                                 const std::string& socket_path,
                                 const std::string& identity);

// Runtime self-check that the owner and replica encodings cannot alias. It
// re-runs the injectivity/disjointness property over a spread of adversarial
// logical keys (empty, prefix-only, nested, "owner:"/"replica:"-prefixed), so a
// startup check fails closed if the key encoding ever regresses. Pure and
// dependency-free; owner/replica disjointness is a property of the code, not
// something INFO can report.
bool namespacesDisjoint();

// Startup rules for the experiment instance:
//   * INFO is readable and the version/run_id are recorded;
//   * the connected socket path equals the explicit path chosen for this run;
//   * the instance identity equals the expected identity;
//   * maxmemory == 0 and maxmemory-policy == noeviction (the application owns
//     the budget, so Redis must never evict on its own);
//   * the experiment DB is empty (DBSIZE == 0). A non-empty DB is REFUSED, not
//     cleared: this function never issues FLUSHDB;
//   * the owner/replica key encodings are disjoint (namespacesDisjoint()), so
//     the two key spaces of the shared instance can never alias.
RedisAcceptance verifyRedisStartup(const RedisInstanceInfo& info,
                                   const std::string& expected_socket_path,
                                   const std::string& expected_identity);

// Low-frequency runtime monitor. observe() is called by the launcher on its own
// interval over a dedicated connection; it detects application-invisible
// automatic eviction, counter regression/restart, and an unreadable INFO, and it
// never treats any of those as "no automatic eviction".
class RedisMonitor {
public:
    RedisMonitor(long long baseline_evicted_keys, std::string baseline_run_id,
                 std::string expected_identity, long long interval_ms = 1000);

    struct Sample {
        bool ok = false;
        std::string reason;  // empty when ok
        bool eviction_increase = false;
        bool counter_regressed = false;
        bool restarted = false;
        long long used_memory = -1;   // Redis TOTAL memory, not app replica bytes
        long long evicted_keys = -1;
    };

    Sample observe(const RedisInstanceInfo& info);

    // False when the monitor was constructed with an unusable baseline (missing
    // counter, empty run id/identity, non-positive interval). observe() then
    // always fails with the config error; it never silently samples.
    bool configValid() const { return config_error_.empty(); }
    const std::string& configError() const { return config_error_; }

    long long baselineEvictedKeys() const { return baseline_evicted_keys_; }
    long long intervalMs() const { return interval_ms_; }
    long long sampleCount() const { return sample_count_; }
    // Cost source, stated explicitly: one INFO per interval on a dedicated
    // monitoring connection; it adds no Redis round trip per DATA operation.
    std::string costSource() const {
        return "one INFO per " + std::to_string(interval_ms_) +
               " ms on a dedicated monitoring connection; no per-DATA query";
    }

private:
    long long baseline_evicted_keys_;
    long long last_evicted_keys_;
    std::string baseline_run_id_;
    std::string expected_identity_;
    long long interval_ms_;
    long long sample_count_ = 0;
    std::string config_error_;
};

}  // namespace rsync_cache