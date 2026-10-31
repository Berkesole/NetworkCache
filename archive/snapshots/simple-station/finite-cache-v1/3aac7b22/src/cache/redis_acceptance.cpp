#include "redis_acceptance.hpp"

#include "key_namespace.hpp"

#include <cstdlib>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

namespace rsync_cache {
namespace {

enum class NumberStatus { Missing, Ok, Invalid };

// Parses into a temporary and only writes `out` on a fully consumed legal
// integer. The previous version wrote `out` first and then returned the
// completeness verdict, so a caller that ignored the verdict accepted
// "0garbage" as zero.
NumberStatus parseNumber(const std::string& text, long long& out) {
    if (text.empty()) return NumberStatus::Missing;
    long long parsed = 0;
    std::size_t consumed = 0;
    try {
        parsed = std::stoll(text, &consumed, 10);
    } catch (const std::exception&) {
        return NumberStatus::Invalid;  // overflow / non-numeric
    }
    if (consumed != text.size()) return NumberStatus::Invalid;  // illegal suffix
    out = parsed;
    return NumberStatus::Ok;
}

// Trims trailing CR/whitespace so both \n and \r\n INFO replies parse.
std::string trim(const std::string& value) {
    std::size_t end = value.size();
    while (end > 0 && (value[end - 1] == '\r' || value[end - 1] == '\n' ||
                       value[end - 1] == ' ' || value[end - 1] == '\t')) {
        --end;
    }
    return value.substr(0, end);
}

// Records the first problem without overwriting an earlier one.
void noteError(RedisInstanceInfo& info, const std::string& reason) {
    if (info.parse_error.empty()) info.parse_error = reason;
}

}  // namespace

RedisInstanceInfo parseRedisInfo(const std::string& info_text,
                                 const std::string& socket_path,
                                 const std::string& identity) {
    RedisInstanceInfo info;
    info.socket_path = socket_path;
    info.identity = identity;
    if (info_text.empty()) {
        return info;  // info_readable stays false
    }
    info.info_readable = true;

    std::unordered_set<std::string> seen;
    std::istringstream stream(info_text);
    std::string line;
    while (std::getline(stream, line)) {
        line = trim(line);
        if (line.empty() || line[0] == '#') continue;
        const auto colon = line.find(':');
        if (colon == std::string::npos) continue;
        const std::string key = line.substr(0, colon);
        const std::string value = line.substr(colon + 1);

        const bool monitored = key == "redis_version" || key == "run_id" ||
                               key == "uptime_in_seconds" || key == "maxmemory" ||
                               key == "maxmemory_policy" || key == "used_memory" ||
                               key == "evicted_keys";
        if (!monitored) continue;
        // A repeated monitored field is ambiguous: refuse rather than keep one.
        if (!seen.insert(key).second) {
            noteError(info, "duplicate " + key);
            continue;
        }

        if (key == "redis_version") {
            info.version = value;
            continue;
        }
        if (key == "run_id") {
            info.run_id = value;
            continue;
        }
        if (key == "maxmemory_policy") {
            info.maxmemory_policy = value;
            continue;
        }

        // Numeric fields: missing / legal zero / illegal stay distinct.
        long long parsed = 0;
        const auto status = parseNumber(value, parsed);
        if (status != NumberStatus::Ok) {
            noteError(info, "invalid " + key + "=" + value);
            continue;
        }
        if (parsed < 0) {
            noteError(info, "negative " + key + "=" + value);
            continue;
        }
        if (key == "uptime_in_seconds") {
            info.uptime_seconds = parsed;
        } else if (key == "maxmemory") {
            info.maxmemory = parsed;
        } else if (key == "used_memory") {
            info.used_memory = parsed;
        } else if (key == "evicted_keys") {
            info.evicted_keys = parsed;
        }
    }
    return info;
}

bool namespacesDisjoint() {
    const std::vector<std::string> samples = {
        "", "k", "owner:", "replica:", "owner:k", "replica:k",
        "owner:replica:k", "replica:owner:k", "a", "aa", "a:b",
        ":owner:", ":replica:", "owner::", "replica::", "k1", "k2",
    };
    std::unordered_set<std::string> owner_keys;
    std::unordered_set<std::string> replica_keys;
    for (const auto& logical : samples) {
        owner_keys.insert(physicalKey(CacheNamespace::Owner, logical));
        replica_keys.insert(physicalKey(CacheNamespace::Replica, logical));
    }
    // Cross-namespace disjointness over the sample.
    for (const auto& owner_key : owner_keys) {
        if (replica_keys.count(owner_key) != 0) return false;
    }
    // Injectivity inside the owner namespace over the sample: distinct logical
    // keys must not encode to the same physical key.
    for (std::size_t i = 0; i < samples.size(); ++i) {
        for (std::size_t j = i + 1; j < samples.size(); ++j) {
            if (samples[i] == samples[j]) continue;
            if (physicalKey(CacheNamespace::Owner, samples[i]) ==
                physicalKey(CacheNamespace::Owner, samples[j])) {
                return false;
            }
        }
    }
    return true;
}

RedisAcceptance verifyRedisStartup(const RedisInstanceInfo& info,
                                   const std::string& expected_socket_path,
                                   const std::string& expected_identity) {
    if (!namespacesDisjoint()) return {false, "namespaces_not_disjoint"};
    if (!info.info_readable) return {false, "info_unreadable"};
    // A malformed/duplicated monitored field must be refused before any value is
    // read as a default.
    if (!info.parse_error.empty()) return {false, info.parse_error};
    if (expected_socket_path.empty() || expected_socket_path[0] != '/') {
        return {false, "expected_socket_path_not_explicit"};
    }
    if (info.socket_path != expected_socket_path) {
        return {false, "socket_path_mismatch"};
    }
    if (info.identity.empty() || expected_identity.empty()) {
        return {false, "identity_not_recorded"};
    }
    if (info.identity != expected_identity) {
        return {false, "identity_mismatch"};
    }
    if (info.version.empty()) return {false, "version_not_recorded"};
    if (info.run_id.empty()) return {false, "run_id_not_recorded"};
    // The monitor needs a real baseline; a missing evicted_keys (default -1)
    // must not silently become 0.
    if (info.evicted_keys < 0) return {false, "evicted_keys_missing"};
    if (info.used_memory < 0) return {false, "used_memory_missing"};
    if (info.maxmemory != 0) return {false, "maxmemory_not_zero"};
    if (info.maxmemory_policy != "noeviction") {
        return {false, "maxmemory_policy_not_noeviction"};
    }
    if (info.db_keys != 0) return {false, "db_not_empty"};
    return {true, ""};
}

RedisMonitor::RedisMonitor(long long baseline_evicted_keys, std::string baseline_run_id,
                           std::string expected_identity, long long interval_ms)
    : baseline_evicted_keys_(baseline_evicted_keys),
      last_evicted_keys_(baseline_evicted_keys),
      baseline_run_id_(std::move(baseline_run_id)),
      expected_identity_(std::move(expected_identity)),
      interval_ms_(interval_ms) {
    if (baseline_evicted_keys < 0) {
        config_error_ = "baseline_evicted_keys_missing";
    } else if (baseline_run_id_.empty()) {
        config_error_ = "baseline_run_id_missing";
    } else if (expected_identity_.empty()) {
        config_error_ = "expected_identity_missing";
    } else if (interval_ms_ <= 0) {
        config_error_ = "invalid_interval_ms";
    }
}

RedisMonitor::Sample RedisMonitor::observe(const RedisInstanceInfo& info) {
    ++sample_count_;
    Sample sample;
    sample.used_memory = info.used_memory;
    sample.evicted_keys = info.evicted_keys;

    if (!config_error_.empty()) {
        sample.reason = config_error_;  // unusable monitor never samples
        return sample;
    }
    if (!info.info_readable) {
        sample.reason = "info_unreadable";
        return sample;  // never silently "no eviction"
    }
    if (!info.parse_error.empty()) {
        sample.reason = info.parse_error;  // malformed field, not healthy
        return sample;
    }
    if (!expected_identity_.empty() && info.identity != expected_identity_) {
        sample.reason = "identity_mismatch";
        return sample;
    }
    if (info.evicted_keys < 0) {
        sample.reason = "evicted_keys_missing";
        return sample;
    }
    if (info.used_memory < 0) {
        sample.reason = "used_memory_missing";
        return sample;
    }
    if (!baseline_run_id_.empty() && info.run_id != baseline_run_id_) {
        sample.restarted = true;
        sample.reason = "instance_restarted";
        return sample;
    }
    if (info.evicted_keys < last_evicted_keys_) {
        sample.counter_regressed = true;
        sample.reason = "evicted_keys_regressed";
        return sample;
    }
    if (info.evicted_keys > baseline_evicted_keys_) {
        sample.eviction_increase = true;
        sample.reason = "automatic_eviction_detected";
        return sample;
    }
    last_evicted_keys_ = info.evicted_keys;
    sample.ok = true;
    return sample;
}

}  // namespace rsync_cache