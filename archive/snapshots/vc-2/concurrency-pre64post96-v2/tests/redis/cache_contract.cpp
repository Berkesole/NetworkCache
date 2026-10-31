#include "cache/redis_cache.hpp"
#include "test_harness.hpp"

#include <string>

namespace {

using redis_test::ScopedEnvVar;
using redis_test::TestCase;
using redis_test::make_unique_key;
using redis_test::make_value;
using redis_test::require;
using redis_test::require_contains;
using redis_test::require_env;
using redis_test::value_to_string;

std::string test_socket() {
    return require_env("TEST_REDIS_SOCKET");
}

std::string missing_socket_path() {
    return "/tmp/rsync-phase5d-missing-" + std::to_string(::getpid()) + ".sock";
}

void flush_isolated_db(RedisCache& cache) {
    ScopedEnvVar allow_flush("REDIS_CACHE_ALLOW_FLUSHDB");
    allow_flush.set("1");
    cache.flush();
}

void test_basic_interface_round_trip() {
    RedisCache cache(test_socket());
    const std::string key = make_unique_key("basic");

    flush_isolated_db(cache);

    cache.put(key, make_value("value-1"));
    require(cache.exists(key), "put() should make the key visible to exists()");

    const auto result = cache.get(key);
    require(result.has_value(), "get() should return the stored value");
    require(value_to_string(*result) == "value-1",
            "get() returned an unexpected value");

    cache.del(key);
    require(!cache.exists(key), "del() should remove the key");
    require(!cache.get(key).has_value(), "deleted key should read back as missing");
}

void test_overwrite_updates_existing_key() {
    RedisCache cache(test_socket());
    const std::string key = make_unique_key("overwrite");

    cache.put(key, make_value("before"));
    cache.put(key, make_value("after"));

    const auto result = cache.get(key);
    require(result.has_value(), "overwritten key should still exist");
    require(value_to_string(*result) == "after",
            "second put() should replace the old value");

    cache.del(key);
}

void test_flush_requires_explicit_opt_in() {
    RedisCache cache(test_socket());
    ScopedEnvVar allow_flush("REDIS_CACHE_ALLOW_FLUSHDB");
    allow_flush.unset();

    bool threw = false;
    std::string message;
    try {
        cache.flush();
    } catch (const std::runtime_error& e) {
        threw = true;
        message = e.what();
    }

    require(threw, "flush() should fail without REDIS_CACHE_ALLOW_FLUSHDB=1");
    require_contains(message,
                     "REDIS_CACHE_ALLOW_FLUSHDB",
                     "flush() refusal should name the required env var");
}

void test_flush_clears_database_when_enabled() {
    RedisCache cache(test_socket());
    const std::string key_a = make_unique_key("flush-a");
    const std::string key_b = make_unique_key("flush-b");

    cache.put(key_a, make_value("a"));
    cache.put(key_b, make_value("b"));
    require(cache.exists(key_a), "flush setup failed for first key");
    require(cache.exists(key_b), "flush setup failed for second key");

    flush_isolated_db(cache);

    require(!cache.exists(key_a), "flush() should clear the first key");
    require(!cache.exists(key_b), "flush() should clear the second key");
}

void test_default_constructor_uses_env_socket_path() {
    ScopedEnvVar socket_env("REDIS_SOCKET_PATH");
    socket_env.set(test_socket());

    RedisCache cache;
    const std::string key = make_unique_key("default-ctor");
    cache.put(key, make_value("env-socket"));

    const auto result = cache.get(key);
    require(result.has_value(), "default constructor should connect via REDIS_SOCKET_PATH");
    require(value_to_string(*result) == "env-socket",
            "default constructor returned an unexpected value");

    cache.del(key);
}

void test_explicit_socket_path_ignores_env_override() {
    ScopedEnvVar socket_env("REDIS_SOCKET_PATH");
    socket_env.set(missing_socket_path());

    RedisCache cache(test_socket());
    const std::string key = make_unique_key("explicit-path");
    cache.put(key, make_value("explicit-wins"));

    const auto result = cache.get(key);
    require(result.has_value(),
            "explicit socket path should work even when REDIS_SOCKET_PATH is invalid");
    require(value_to_string(*result) == "explicit-wins",
            "explicit socket path returned an unexpected value");

    cache.del(key);
}

void test_invalid_socket_paths_are_rejected() {
    bool empty_threw = false;
    try {
        RedisCache cache("");
    } catch (const std::invalid_argument& e) {
        empty_threw = true;
        require_contains(e.what(),
                         "cannot be empty",
                         "empty path should explain the validation failure");
    }
    require(empty_threw, "empty socket path should be rejected");

    bool relative_threw = false;
    try {
        RedisCache cache("redis.sock");
    } catch (const std::invalid_argument& e) {
        relative_threw = true;
        require_contains(e.what(),
                         "must be absolute",
                         "relative path should explain the validation failure");
    }
    require(relative_threw, "relative socket path should be rejected");

    const std::string too_long = "/tmp/" + std::string(110, 'x') + ".sock";
    bool long_threw = false;
    try {
        RedisCache cache(too_long);
    } catch (const std::invalid_argument& e) {
        long_threw = true;
        require_contains(e.what(),
                         "too long",
                         "oversized path should explain the validation failure");
    }
    require(long_threw, "oversized socket path should be rejected");
}

void test_connection_failure_is_explicit() {
    bool threw = false;
    std::string message;
    const std::string path = missing_socket_path();

    try {
        RedisCache cache(path);
    } catch (const std::runtime_error& e) {
        threw = true;
        message = e.what();
    }

    require(threw, "connecting to an unavailable Redis socket should throw");
    require_contains(message,
                     "RedisCache init failed",
                     "connection failure should identify RedisCache init");
    require_contains(message,
                     "UNIX(" + path + ")",
                     "connection failure should name the socket path");
}

}  // namespace

int main() {
    return redis_test::run_suite(
        "Redis Cache Contract Tests",
        {
            TestCase{
                "basic_interface_round_trip",
                "put/get/exists/del operate correctly against Redis",
                test_basic_interface_round_trip,
            },
            TestCase{
                "overwrite_updates_existing_key",
                "put overwrites an existing Redis key instead of silently keeping stale data",
                test_overwrite_updates_existing_key,
            },
            TestCase{
                "flush_requires_explicit_opt_in",
                "flush refuses to run without REDIS_CACHE_ALLOW_FLUSHDB=1",
                test_flush_requires_explicit_opt_in,
            },
            TestCase{
                "flush_clears_database_when_enabled",
                "flush clears the isolated Redis database when explicitly allowed",
                test_flush_clears_database_when_enabled,
            },
            TestCase{
                "default_constructor_uses_env_socket_path",
                "default RedisCache constructor honors REDIS_SOCKET_PATH",
                test_default_constructor_uses_env_socket_path,
            },
            TestCase{
                "explicit_socket_path_ignores_env_override",
                "explicit socket path is authoritative over REDIS_SOCKET_PATH",
                test_explicit_socket_path_ignores_env_override,
            },
            TestCase{
                "invalid_socket_paths_are_rejected",
                "configuration path validation rejects empty, relative, and oversized sockets",
                test_invalid_socket_paths_are_rejected,
            },
            TestCase{
                "connection_failure_is_explicit",
                "Redis unavailability surfaces an explicit connection error",
                test_connection_failure_is_explicit,
            },
        });
}
