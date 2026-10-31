// Structured-trace schema tests.
//
// The event writer historically emitted a fixed 16-field whitelist, so any new
// field name was dropped silently. These tests drive the real writer to a real
// file and assert that
//   * every record carries schema_version,
//   * the legacy field order and field set are preserved for existing events,
//   * the cache contract's extra fields survive the writer, and
//   * a caller-supplied value for a legacy field still wins.
//
// The recorder reads its environment once, when it is first used, and keeps one
// open stream. Both tests therefore share a single trace file and locate their
// own records by a unique key instead of assuming a line count.

#include "cache_test_support.hpp"

#include "cache/node_cache.hpp"
#include "cache/trace_event_sink.hpp"
#include "event_trace.hpp"

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>
#include <unistd.h>
#include <vector>

using cache_test::makeValueOfSize;
using rsync_cache::CachePolicy;
using rsync_cache::CachePolicyConfig;
using rsync_cache::NodeCache;

namespace {

const std::string& traceFilePath() {
    static const std::string path =
        (std::filesystem::temp_directory_path() /
         ("rsync-cache-trace-schema-" + std::to_string(::getpid()) + ".jsonl"))
            .string();
    return path;
}

std::vector<std::string> legacyFieldNames() {
    return {"request_id", "key", "src", "dest", "packet_type", "hop_count", "via",
            "tick", "value_bytes", "phase", "reason", "neighbors",
            "signature_count", "deadline_ms", "frame_bytes", "fallback"};
}

std::vector<std::string> readLines() {
    std::vector<std::string> lines;
    std::ifstream input(traceFilePath());
    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty()) lines.push_back(line);
    }
    return lines;
}

std::string readAll() {
    std::string joined;
    for (const auto& line : readLines()) joined += line + "\n";
    return joined;
}

// Returns the first record containing the given key, or an empty string.
std::string recordFor(const std::string& key) {
    const std::string needle = "\"key\":\"" + key + "\"";
    for (const auto& line : readLines()) {
        if (line.find(needle) != std::string::npos) return line;
    }
    return std::string();
}

bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

}  // namespace

CACHE_TEST(writer_emits_schema_version_and_keeps_legacy_fields) {
    // A legacy event with a caller-supplied override for a legacy field.
    rsync_trace::record(3, "provider_hit",
                        {{"key", "schema-legacy-1"}, {"reason", "custom-reason"}});
    rsync_trace::flush();

    const std::string line = recordFor("schema-legacy-1");
    CACHE_REQUIRE(!line.empty(), "the legacy record was not written");
    CACHE_REQUIRE(contains(line, "\"schema_version\":\"2\""), "schema_version missing");
    CACHE_REQUIRE(contains(line, "\"event\":\"provider_hit\""), "event name missing");
    CACHE_REQUIRE(contains(line, "\"reason\":\"custom-reason\""),
                  "caller override for a legacy field was not preserved");
    for (const auto& field : legacyFieldNames()) {
        CACHE_REQUIRE(contains(line, "\"" + field + "\":"),
                      "legacy field '" + field + "' is missing from the record");
    }
}

CACHE_TEST(cache_contract_fields_survive_the_writer) {
    CachePolicyConfig config;
    config.policy = CachePolicy::Lru;
    config.budget_bytes = 65536;
    config.gauge_interval_ms = 0;

    auto sink = rsync_cache::makeTraceCacheEventSink(11);
    NodeCache cache(std::make_unique<cache_test::FaultyCache>(0), config, sink.get());
    cache.seedOwner("trace-owned", makeValueOfSize(65536));
    cache.emitConfigEvent();
    cache.admitReplica("trace-rep1", makeValueOfSize(65536));
    cache.admitReplica("trace-rep2", makeValueOfSize(65536));  // evicts trace-rep1
    cache.admitReplica("trace-rep1", makeValueOfSize(65536));  // re-admit after eviction
    cache.lookupForService("trace-owned");
    cache.lookupForService("trace-rep2");
    cache.emitGaugeEvent();
    rsync_trace::record(11, "provider_hit",
                        {{"key", "trace-tier"}, {"tier", "replica"}, {"lru_touched", "1"}});
    rsync_trace::flush();

    const std::string all = readAll();
    for (const char* name : {"cache_config", "replica_admit", "replica_evict",
                             "cache_gauge"}) {
        CACHE_REQUIRE(contains(all, std::string("\"event\":\"") + name + "\""),
                      std::string("event ") + name + " was not written");
    }
    CACHE_REQUIRE(contains(all, "\"resident_bytes\":\""), "resident_bytes field dropped");
    CACHE_REQUIRE(contains(all, "\"budget_mode\":\"lru\""), "budget_mode field dropped");
    CACHE_REQUIRE(contains(all, "\"idle_age_ns\":\""), "idle_age_ns field dropped");
    CACHE_REQUIRE(contains(all, "\"evicted_mono_ns\":\""), "evicted_mono_ns field dropped");
    CACHE_REQUIRE(contains(all, "\"cache_contract_revision\":\"4\""),
                  "cache_contract_revision field dropped");
    CACHE_REQUIRE(contains(all, "\"insert_seq\":\""), "insert_seq field dropped");
    CACHE_REQUIRE(contains(all, "\"touch_seq\":\""), "touch_seq field dropped");
    CACHE_REQUIRE(contains(all, "\"cause_key\":\"trace-rep2\""), "cause_key field dropped");
    CACHE_REQUIRE(contains(all, "\"owner_objects\":\"1\""), "gauge owner_objects dropped");
    CACHE_REQUIRE(contains(all, "\"cache_policy_version\":\"finite-cache-v1\""),
                  "cache_policy_version field dropped");
    CACHE_REQUIRE(contains(all, "\"namespace_owner\":\"owner\""), "namespace_owner dropped");
    CACHE_REQUIRE(contains(all, "\"value_granularity\":\"value_bytes_only\""),
                  "value_granularity dropped");
    // tier / lru_touched are produced by Node, not the cache layer; the writer
    // must still pass them through unchanged.
    CACHE_REQUIRE(contains(all, "\"tier\":\"replica\""), "tier field dropped");
    CACHE_REQUIRE(contains(all, "\"lru_touched\":\"1\""), "lru_touched field dropped");
    // Reuse distance is deliberately not collected; only idle age is offered.
    CACHE_REQUIRE(!contains(all, "reuse_distance"),
                  "idle age must never be labelled reuse distance");
    // Every record must still carry the full legacy field set.
    for (const auto& line : readLines()) {
        for (const auto& field : legacyFieldNames()) {
            CACHE_REQUIRE(contains(line, "\"" + field + "\":"),
                          "legacy field '" + field + "' missing from a record");
        }
    }
}

int main() {
    // The trace recorder reads its environment once, on first use: set it up
    // before any test runs.
    std::filesystem::remove(traceFilePath());
    setenv("RSYNC_EVENT_TRACE", "1", 1);
    setenv("RSYNC_EVENT_TRACE_PATH", traceFilePath().c_str(), 1);
    setenv("RSYNC_EVENT_TRACE_RUN_ID", "cache-trace-schema-test", 1);
    setenv("RSYNC_EVENT_TRACE_SCHEMA_VERSION", "2", 1);
    const int result = cache_test::runAll("cache_trace_schema");
    std::filesystem::remove(traceFilePath());
    return result;
}