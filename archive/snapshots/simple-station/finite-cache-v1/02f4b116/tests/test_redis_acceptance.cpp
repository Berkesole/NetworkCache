// Deterministic tests for the Redis startup-acceptance and runtime-monitor
// interfaces. These need NO Redis server: they exercise the pure rules with
// captured INFO snapshots. The live-instance path (RedisCache::readInstanceInfo)
// is only compiled when Redis dependencies are present and is NOT covered here.

#include "cache_test_support.hpp"

#include "cache/redis_acceptance.hpp"

#include "cache/key_namespace.hpp"

#include <string>

using rsync_cache::RedisAcceptance;
using rsync_cache::RedisInstanceInfo;
using rsync_cache::RedisMonitor;

namespace {

const char* kRunId = "0123456789abcdef0123456789abcdef01234567";

const char* kInfoSample =
    "# Server\r\n"
    "redis_version:7.0.15\r\n"
    "run_id:0123456789abcdef0123456789abcdef01234567\r\n"
    "uptime_in_seconds:42\r\n"
    "# Memory\r\n"
    "used_memory:1048576\r\n"
    "used_memory_human:1.00M\r\n"
    "maxmemory:0\r\n"
    "maxmemory_policy:noeviction\r\n"
    "# Stats\r\n"
    "evicted_keys:0\r\n";

RedisInstanceInfo healthyInfo(const std::string& socket = "/tmp/rsync-redis-exp/redis.sock",
                              const std::string& identity = "finite-cache-exp") {
    RedisInstanceInfo info = rsync_cache::parseRedisInfo(kInfoSample, socket, identity);
    info.db_keys = 0;
    return info;
}

std::string reasonOf(const RedisAcceptance& verdict) { return verdict.reason; }

}  // namespace

CACHE_TEST(parse_reads_the_required_info_fields) {
    const auto info = rsync_cache::parseRedisInfo(
        kInfoSample, "/tmp/exp/redis.sock", "exp-1");
    CACHE_REQUIRE(info.info_readable, "a non-empty INFO reply must be readable");
    CACHE_REQUIRE(info.version == "7.0.15", "redis_version");
    CACHE_REQUIRE(info.run_id.size() == 40, "run_id");
    CACHE_REQUIRE(info.uptime_seconds == 42, "uptime_in_seconds");
    CACHE_REQUIRE(info.used_memory == 1048576, "used_memory");
    CACHE_REQUIRE(info.maxmemory == 0, "maxmemory");
    CACHE_REQUIRE(info.maxmemory_policy == "noeviction", "maxmemory_policy");
    CACHE_REQUIRE(info.evicted_keys == 0, "evicted_keys");
}

CACHE_TEST(owner_and_replica_encodings_are_disjoint) {
    // A startup self-check over adversarial logical keys: the shared Redis
    // instance must never let an owner key and a replica key alias, and distinct
    // logical keys must stay distinct inside a namespace.
    CACHE_REQUIRE(rsync_cache::namespacesDisjoint(),
                  "owner and replica key encodings must never alias");
    CACHE_REQUIRE(rsync_cache::physicalKey(rsync_cache::CacheNamespace::Owner, "k") ==
                      "owner:k",
                  "owner prefix");
    CACHE_REQUIRE(rsync_cache::physicalKey(rsync_cache::CacheNamespace::Replica, "k") ==
                      "replica:k",
                  "replica prefix");
    CACHE_REQUIRE(rsync_cache::physicalKey(rsync_cache::CacheNamespace::Owner,
                                           "replica:k") !=
                      rsync_cache::physicalKey(rsync_cache::CacheNamespace::Replica,
                                               "owner:k"),
                  "confusable logical keys must still land in different namespaces");
}

CACHE_TEST(startup_accepts_a_correct_isolated_instance) {
    const auto verdict = rsync_cache::verifyRedisStartup(
        healthyInfo(), "/tmp/rsync-redis-exp/redis.sock", "finite-cache-exp");
    CACHE_REQUIRE(verdict.accepted, "a correct isolated instance must be accepted: " +
                                        verdict.reason);
    CACHE_REQUIRE(verdict.reason.empty(), "accepted verdict has no reason");
}

CACHE_TEST(startup_rejects_each_configuration_violation) {
    const std::string path = "/tmp/rsync-redis-exp/redis.sock";

    auto unreadable = healthyInfo();
    unreadable.info_readable = false;
    CACHE_REQUIRE(reasonOf(rsync_cache::verifyRedisStartup(unreadable, path, "finite-cache-exp")) ==
                      "info_unreadable",
                  "unreadable INFO must be refused");

    // An implicit/empty path is not an explicitly chosen experimental instance.
    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(healthyInfo(), "", "finite-cache-exp")) ==
            "expected_socket_path_not_explicit",
        "an empty expected path must be refused");
    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(healthyInfo(), "tmp/relative.sock",
                                                 "finite-cache-exp")) ==
            "expected_socket_path_not_explicit",
        "a non-absolute expected path must be refused");

    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(healthyInfo("/other/redis.sock"), path,
                                                 "finite-cache-exp")) ==
            "socket_path_mismatch",
        "connecting to another instance must be refused");

    auto wrong_identity = healthyInfo();
    wrong_identity.identity = "some-other-instance";
    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(wrong_identity, path, "finite-cache-exp")) ==
            "identity_mismatch",
        "identity mismatch must be refused");

    auto no_identity = healthyInfo();
    no_identity.identity.clear();
    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(no_identity, path, "finite-cache-exp")) ==
            "identity_not_recorded",
        "an unrecorded identity must be refused");

    auto no_version = healthyInfo();
    no_version.version.clear();
    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(no_version, path, "finite-cache-exp")) ==
            "version_not_recorded",
        "an unrecorded version must be refused");

    auto no_run_id = healthyInfo();
    no_run_id.run_id.clear();
    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(no_run_id, path, "finite-cache-exp")) ==
            "run_id_not_recorded",
        "an unrecorded run_id must be refused");

    auto with_memory = healthyInfo();
    with_memory.maxmemory = 268435456;
    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(with_memory, path, "finite-cache-exp")) ==
            "maxmemory_not_zero",
        "a Redis-side memory limit must be refused");

    auto evicting = healthyInfo();
    evicting.maxmemory_policy = "allkeys-lru";
    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(evicting, path, "finite-cache-exp")) ==
            "maxmemory_policy_not_noeviction",
        "a Redis-side eviction policy must be refused");

    auto non_empty = healthyInfo();
    non_empty.db_keys = 3;
    CACHE_REQUIRE(
        reasonOf(rsync_cache::verifyRedisStartup(non_empty, path, "finite-cache-exp")) ==
            "db_not_empty",
        "a non-empty DB must be refused (never flushed)");
}

CACHE_TEST(monitor_reports_no_eviction_for_a_healthy_prefix) {
    RedisMonitor monitor(0, kRunId, "finite-cache-exp", 1000);
    const auto sample = monitor.observe(healthyInfo());
    CACHE_REQUIRE(sample.ok, "a healthy prefix sample must be ok: " + sample.reason);
    CACHE_REQUIRE(!sample.eviction_increase && !sample.restarted && !sample.counter_regressed,
                  "no failure flags on a healthy sample");
    CACHE_REQUIRE(sample.used_memory == 1048576,
                  "used_memory is exposed as the Redis total");
    CACHE_REQUIRE(monitor.sampleCount() == 1, "sample count");
}

CACHE_TEST(monitor_detects_automatic_eviction) {
    RedisMonitor monitor(0, kRunId, "finite-cache-exp", 1000);
    auto evicted = healthyInfo();
    evicted.evicted_keys = 1;
    const auto sample = monitor.observe(evicted);
    CACHE_REQUIRE(!sample.ok, "an eviction increase must fail the prefix");
    CACHE_REQUIRE(sample.eviction_increase, "eviction_increase flag");
    CACHE_REQUIRE(sample.reason == "automatic_eviction_detected", "reason");
}

CACHE_TEST(monitor_detects_restart_and_counter_regression) {
    RedisMonitor monitor(5, kRunId, "finite-cache-exp", 1000);

    auto restarted = healthyInfo();
    restarted.run_id = "a-different-run-id";
    restarted.evicted_keys = 5;
    const auto restart_sample = monitor.observe(restarted);
    CACHE_REQUIRE(!restart_sample.ok && restart_sample.restarted, "restart detection");
    CACHE_REQUIRE(restart_sample.reason == "instance_restarted", "restart reason");

    RedisMonitor regressing(10, kRunId, "finite-cache-exp", 1000);
    auto baseline = healthyInfo();
    baseline.evicted_keys = 10;
    CACHE_REQUIRE(regressing.observe(baseline).ok, "baseline sample ok");
    auto rolled_back = healthyInfo();
    rolled_back.evicted_keys = 2;
    const auto regressed = regressing.observe(rolled_back);
    CACHE_REQUIRE(!regressed.ok && regressed.counter_regressed, "counter regression detection");
    CACHE_REQUIRE(regressed.reason == "evicted_keys_regressed", "regression reason");
}

CACHE_TEST(monitor_unreadable_info_is_never_no_eviction) {
    RedisMonitor monitor(0, kRunId, "finite-cache-exp", 1000);
    RedisInstanceInfo unreadable;
    unreadable.socket_path = "/tmp/rsync-redis-exp/redis.sock";
    unreadable.identity = "finite-cache-exp";
    const auto sample = monitor.observe(unreadable);
    CACHE_REQUIRE(!sample.ok, "an unreadable INFO must not be treated as healthy");
    CACHE_REQUIRE(sample.reason == "info_unreadable", "unreadable reason");

    auto missing_counter = healthyInfo();
    missing_counter.evicted_keys = -1;
    const auto missing = monitor.observe(missing_counter);
    CACHE_REQUIRE(!missing.ok && missing.reason == "evicted_keys_missing",
                  "a missing evicted_keys must be refused, not assumed zero");
}

CACHE_TEST(monitor_states_its_cost_source) {
    RedisMonitor monitor(0, kRunId, "finite-cache-exp", 2000);
    const std::string cost = monitor.costSource();
    CACHE_REQUIRE(cost.find("one INFO per 2000 ms") != std::string::npos,
                  "the sampling interval must be stated");
    CACHE_REQUIRE(cost.find("no per-DATA query") != std::string::npos,
                  "the monitor must not add a query per DATA operation");
    CACHE_REQUIRE(monitor.intervalMs() == 2000, "interval accessor");
}

CACHE_TEST(parse_rejects_illegal_numeric_values) {
    // "0garbage" used to be written to the output before the completeness check
    // and then read as zero by callers that ignored the return value.
    const std::string body =
        "redis_version:7.0.15\r\nrun_id:abc\r\nuptime_in_seconds:1\r\n"
        "used_memory:1024\r\nmaxmemory:0garbage\r\n"
        "maxmemory_policy:noeviction\r\nevicted_keys:0\r\n";
    const auto info = rsync_cache::parseRedisInfo(body, "/tmp/x.sock", "id");
    CACHE_REQUIRE(!info.parse_error.empty(), "an illegal suffix must be recorded as an error");
    CACHE_REQUIRE(info.maxmemory != 0, "the illegal value must not be installed as zero");
    CACHE_REQUIRE(!rsync_cache::verifyRedisStartup(info, "/tmp/x.sock", "id").accepted,
                  "startup must reject an illegal numeric field");

    const std::string evicted_bad =
        "redis_version:7.0.15\r\nrun_id:abc\r\nuptime_in_seconds:1\r\n"
        "used_memory:1024\r\nmaxmemory:0\r\n"
        "maxmemory_policy:noeviction\r\nevicted_keys:0garbage\r\n";
    const auto bad_keys = rsync_cache::parseRedisInfo(evicted_bad, "/tmp/x.sock", "id");
    CACHE_REQUIRE(!bad_keys.parse_error.empty(), "evicted_keys suffix must be rejected");
    RedisMonitor monitor(0, kRunId, "id", 1000);
    const auto sample = monitor.observe(bad_keys);
    CACHE_REQUIRE(!sample.ok, "a malformed sample must not be healthy");
    CACHE_REQUIRE(sample.reason.find("evicted_keys") != std::string::npos,
                  "the monitor must name the malformed field");

    // Missing is distinct from a legal zero: a present 0 is fine, a missing
    // field keeps its sentinel and is rejected by consumers.
    const std::string zero_body =
        "redis_version:7.0.15\r\nrun_id:abc\r\nuptime_in_seconds:1\r\n"
        "used_memory:0\r\nmaxmemory:0\r\n"
        "maxmemory_policy:noeviction\r\nevicted_keys:0\r\n";
    const auto zero = rsync_cache::parseRedisInfo(zero_body, "/tmp/x.sock", "id");
    CACHE_REQUIRE(zero.parse_error.empty() && zero.evicted_keys == 0,
                  "a legal zero must parse as zero");
    auto missing = zero;
    missing.evicted_keys = -1;
    CACHE_REQUIRE(
        rsync_cache::verifyRedisStartup(missing, "/tmp/x.sock", "id").reason ==
            "evicted_keys_missing",
        "a missing evicted_keys must not be accepted as a baseline");
}

CACHE_TEST(parse_rejects_duplicate_negative_and_overflow) {
    const std::string dup =
        "redis_version:7.0.15\r\nrun_id:abc\r\nuptime_in_seconds:1\r\n"
        "used_memory:1024\r\nmaxmemory:0\r\nmaxmemory:0\r\n"
        "maxmemory_policy:noeviction\r\nevicted_keys:0\r\n";
    const auto dup_info = rsync_cache::parseRedisInfo(dup, "/tmp/x.sock", "id");
    CACHE_REQUIRE(dup_info.parse_error == "duplicate maxmemory",
                  "a duplicated monitored field must be refused");

    const std::string negative =
        "redis_version:7.0.15\r\nrun_id:abc\r\nuptime_in_seconds:1\r\n"
        "used_memory:1024\r\nmaxmemory:0\r\n"
        "maxmemory_policy:noeviction\r\nevicted_keys:-1\r\n";
    const auto neg = rsync_cache::parseRedisInfo(negative, "/tmp/x.sock", "id");
    CACHE_REQUIRE(neg.parse_error.find("negative") != std::string::npos,
                  "a negative counter must be refused");
    CACHE_REQUIRE(neg.evicted_keys == -1, "the negative value must not be installed");

    const std::string overflow =
        "redis_version:7.0.15\r\nrun_id:abc\r\nuptime_in_seconds:1\r\n"
        "used_memory:99999999999999999999999999\r\nmaxmemory:0\r\n"
        "maxmemory_policy:noeviction\r\nevicted_keys:0\r\n";
    const auto big = rsync_cache::parseRedisInfo(overflow, "/tmp/x.sock", "id");
    CACHE_REQUIRE(!big.parse_error.empty(), "overflow must be refused");
    CACHE_REQUIRE(big.used_memory == -1, "the overflowed value must not be installed");
}

CACHE_TEST(monitor_rejects_an_invalid_configuration) {
    RedisMonitor missing_baseline(-1, kRunId, "finite-cache-exp", 1000);
    CACHE_REQUIRE(!missing_baseline.configValid(), "a missing baseline is unusable");
    CACHE_REQUIRE(!missing_baseline.observe(healthyInfo()).ok,
                  "an unusable monitor must never report a healthy sample");

    RedisMonitor no_run(0, "", "finite-cache-exp", 1000);
    CACHE_REQUIRE(!no_run.configValid(), "an empty baseline run id is unusable");

    RedisMonitor no_identity(0, kRunId, "", 1000);
    CACHE_REQUIRE(!no_identity.configValid(), "an empty identity is unusable");

    RedisMonitor bad_interval(0, kRunId, "finite-cache-exp", 0);
    CACHE_REQUIRE(!bad_interval.configValid(), "a non-positive interval is unusable");

    RedisMonitor ok(0, kRunId, "finite-cache-exp", 1000);
    CACHE_REQUIRE(ok.configValid(), "a fully specified monitor must be usable");
    CACHE_REQUIRE(ok.observe(healthyInfo()).ok, "and it samples a healthy instance");
}

int main() { return cache_test::runAll("redis_acceptance"); }