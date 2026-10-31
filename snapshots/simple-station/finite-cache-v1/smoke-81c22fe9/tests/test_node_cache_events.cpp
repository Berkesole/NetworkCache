// Event-contract tests for the finite replica cache.
//
// These assert the properties the experiment analysis depends on:
//   * admission outcomes are mutually exclusive and explainable;
//   * budget 0 produces bypasses and never an eviction;
//   * event counts close against the final resident state;
//   * a backend failure never emits a success event;
//   * tier / lru_touched are correct, and owner reads do not touch replica LRU;
//   * a missing event is never silently reported as a zero (counters and
//     gauges are read from the store, not inferred from the trace);
//   * legacy rethrows backend failures instead of absorbing them, and uses bare
//     keys with no cache events;
//   * an indeterminate backend result ("applied, then response lost") is not a
//     success, does not claim the opposite state, preserves already-confirmed
//     operations, and poisons the store so later calls refuse to continue.

#include "cache_test_support.hpp"

#include "cache/node_cache.hpp"

#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

using cache_test::makeValueOfSize;
using cache_test::RecordingCacheEventSink;
using rsync_cache::AdmitStatus;
using rsync_cache::CacheEventContext;
using rsync_cache::CachePolicy;
using rsync_cache::CachePolicyConfig;
using rsync_cache::CacheTier;
using rsync_cache::NodeCache;

namespace {

CachePolicyConfig policyConfig(CachePolicy policy, std::size_t budget) {
    CachePolicyConfig config;
    config.policy = policy;
    config.budget_bytes = budget;
    config.gauge_interval_ms = 0;  // deterministic: no timer-driven gauge
    return config;
}

struct Fixture {
    RecordingCacheEventSink sink;
    std::unique_ptr<cache_test::FaultyCache> backend_owner;
    cache_test::FaultyCache* backend = nullptr;
    std::unique_ptr<NodeCache> cache;

    Fixture(CachePolicy policy, std::size_t budget) {
        backend_owner = std::make_unique<cache_test::FaultyCache>(0);
        backend = backend_owner.get();
        cache = std::make_unique<NodeCache>(std::move(backend_owner),
                                            policyConfig(policy, budget), &sink);
    }
};

CacheEventContext context(const std::string& request_id, const std::string& phase) {
    CacheEventContext ctx;
    ctx.request_id = request_id;
    ctx.phase = phase;
    ctx.source_node = "7";
    ctx.origin_query_uid = request_id;
    return ctx;
}

// Exactly one terminal outcome event per admission attempt.
std::size_t outcomeCount(const RecordingCacheEventSink& sink) {
    return sink.count("replica_admit") + sink.count("replica_bypass") +
           sink.count("data_admit_skipped") + sink.count("cache_key_conflict") +
           sink.count("cache_backend_error");
}

}  // namespace

CACHE_TEST(admitted_event_carries_required_fields) {
    Fixture fixture(CachePolicy::Lru, 4 * 65536);
    const auto result = fixture.cache->admitReplica("k1", makeValueOfSize(65536),
                                                    context("42", "preferred"));
    CACHE_REQUIRE(result.inserted(), "k1 must be admitted");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 1, "expected one replica_admit");
    const auto event = fixture.sink.named("replica_admit").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "key") == "k1", "key field");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "value_bytes") == "65536", "value_bytes");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "resident_bytes") == "65536", "resident_bytes");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "resident_objects") == "1", "resident_objects");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "evicted_count") == "0", "evicted_count");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "evicted_bytes") == "0", "evicted_bytes");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "budget_mode") == "lru", "budget_mode");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "budget_bytes") == "262144", "budget_bytes");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "request_id") == "42", "request_id");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "phase") == "preferred", "phase");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "source_node") == "7", "source_node");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "reason") == "admitted", "reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "cache_policy_version") ==
                      "finite-cache-v1",
                  "cache_policy_version");
}

CACHE_TEST(eviction_events_are_paired_with_the_admission) {
    Fixture fixture(CachePolicy::Lru, 65536);
    fixture.cache->admitReplica("old", makeValueOfSize(65536), context("1", "preferred"));
    fixture.sink.clear();
    const auto result = fixture.cache->admitReplica("new", makeValueOfSize(65536),
                                                    context("2", "preferred"));
    CACHE_REQUIRE(result.inserted(), "new must be admitted");
    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 1, "expected one replica_evict");
    CACHE_REQUIRE(outcomeCount(fixture.sink) == 1, "exactly one outcome event");
    const auto evict = fixture.sink.named("replica_evict").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(evict, "key") == "old", "evicted key");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evict, "cause_key") == "new", "cause_key");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evict, "reason") == "budget", "evict reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evict, "value_bytes") == "65536", "victim bytes");
    // Contract revision 2: resident_* is the snapshot taken after THIS victim
    // was deleted and before the new object was inserted, so evicting the only
    // resident object leaves 0 bytes / 0 objects (it is not the pre-deletion
    // value, and not the final state after "new" was admitted).
    CACHE_REQUIRE(RecordingCacheEventSink::field(evict, "resident_bytes") == "0",
                  "resident_bytes must be the post-deletion snapshot");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evict, "resident_objects") == "0",
                  "resident_objects must be the post-deletion snapshot");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evict, "cache_contract_revision") == "4",
                  "replica_evict must declare the contract revision of its resident fields");
    CACHE_REQUIRE(RecordingCacheEventSink::number(evict, "insert_seq") >= 1, "insert_seq");
    CACHE_REQUIRE(RecordingCacheEventSink::number(evict, "touch_seq") >= 1, "touch_seq");
    CACHE_REQUIRE(RecordingCacheEventSink::number(evict, "inserted_mono_ns") > 0,
                  "inserted_mono_ns must be a real timestamp");
    CACHE_REQUIRE(RecordingCacheEventSink::number(evict, "last_touch_mono_ns") > 0,
                  "last_touch_mono_ns must be a real timestamp");
    // The contract calls the gap from last touch "idle age", never "reuse
    // distance": real reuse distance needs an ordered access stream and a
    // distinct-key count, which this version does not collect.
    CACHE_REQUIRE(RecordingCacheEventSink::has(evict, "idle_age_ns"), "idle_age_ns missing");
    CACHE_REQUIRE(!RecordingCacheEventSink::has(evict, "reuse_distance"),
                  "idle age must not be named reuse distance");
}

CACHE_TEST(budget_zero_emits_bypass_and_never_evicts) {
    Fixture fixture(CachePolicy::Lru, 0);
    for (int i = 0; i < 3; ++i) {
        fixture.cache->admitReplica("k" + std::to_string(i), makeValueOfSize(65536),
                                    context("1", "preferred"));
    }
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0, "budget 0 must not admit");
    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 0, "budget 0 must never evict");
    CACHE_REQUIRE(fixture.sink.count("replica_bypass") == 3, "expected three bypasses");
    for (const auto& event : fixture.sink.named("replica_bypass")) {
        CACHE_REQUIRE(RecordingCacheEventSink::field(event, "reason") == "budget_zero",
                      "budget 0 bypass reason");
        CACHE_REQUIRE(RecordingCacheEventSink::field(event, "budget_bytes") == "0",
                      "budget 0 must report budget_bytes=0");
    }
    CACHE_REQUIRE(fixture.cache->gauge().replica_objects == 0, "budget 0 must stay empty");
}

CACHE_TEST(policy_bypass_and_oversized_use_distinct_reasons) {
    Fixture bypass(CachePolicy::Bypass, 0);
    bypass.cache->admitReplica("k", makeValueOfSize(10), context("1", "preferred"));
    CACHE_REQUIRE(bypass.sink.count("replica_bypass") == 1, "bypass event");
    CACHE_REQUIRE(RecordingCacheEventSink::field(bypass.sink.named("replica_bypass").at(0), "reason") ==
                      "policy_bypass",
                  "policy bypass must be distinguishable from budget 0");

    Fixture oversized(CachePolicy::Lru, 100);
    oversized.cache->admitReplica("k", makeValueOfSize(200), context("1", "preferred"));
    CACHE_REQUIRE(RecordingCacheEventSink::field(oversized.sink.named("replica_bypass").at(0), "reason") ==
                      "object_larger_than_budget",
                  "oversized bypass reason");
}

CACHE_TEST(already_resident_and_owner_resident_are_distinct_skips) {
    Fixture fixture(CachePolicy::Lru, 4 * 65536);
    // Owners are sealed at the first serving operation, so seed before serving.
    fixture.cache->seedOwner("owned", makeValueOfSize(65536));
    fixture.cache->admitReplica("k", makeValueOfSize(65536), context("1", "preferred"));
    fixture.sink.clear();
    fixture.cache->admitReplica("k", makeValueOfSize(65536), context("2", "fallback"));
    CACHE_REQUIRE(fixture.sink.count("data_admit_skipped") == 1, "expected one skip");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0, "duplicate must not re-admit");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      fixture.sink.named("data_admit_skipped").at(0), "reason") == "already_cached",
                  "already-cached reason");

    fixture.sink.clear();
    fixture.cache->admitReplica("owned", makeValueOfSize(65536), context("3", "preferred"));
    CACHE_REQUIRE(fixture.sink.count("data_admit_skipped") == 1, "owner skip event");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      fixture.sink.named("data_admit_skipped").at(0), "reason") == "owner_resident",
                  "owner-resident reason");
    CACHE_REQUIRE(fixture.cache->gauge().replica_objects == 1,
                  "an owner key must not create a replica");
    CACHE_REQUIRE(!fixture.backend->hasPhysical("replica:owned"),
                  "an owner key must not be written to the replica namespace");
}

CACHE_TEST(conflict_and_backend_error_have_their_own_events) {
    Fixture fixture(CachePolicy::Unlimited, 0);
    fixture.cache->admitReplica("k", makeValueOfSize(10, 'a'), context("1", "preferred"));
    fixture.sink.clear();
    fixture.cache->admitReplica("k", makeValueOfSize(11, 'a'), context("2", "preferred"));
    CACHE_REQUIRE(fixture.sink.count("cache_key_conflict") == 1, "conflict event");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      fixture.sink.named("cache_key_conflict").at(0), "reason") ==
                      "size_mismatch",
                  "size conflict reason");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0, "conflict must not admit");

    fixture.sink.clear();
    fixture.backend->failPuts();
    const auto result = fixture.cache->admitReplica("fresh", makeValueOfSize(10),
                                                    context("3", "preferred"));
    CACHE_REQUIRE(result.backend_error(), "expected a backend error");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "backend error event");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0,
                  "a backend failure must not emit a success event");
    const auto error = fixture.sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "reason") == "replica_put_failed",
                  "backend error reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "fatal") == "0", "non-fatal backend error");
}

CACHE_TEST(admission_outcomes_are_mutually_exclusive) {
    Fixture fixture(CachePolicy::Lru, 65536);
    const std::vector<std::size_t> sizes = {65536, 65536, 10, 200000, 65536, 65536};
    for (std::size_t i = 0; i < sizes.size(); ++i) {
        fixture.sink.clear();
        fixture.cache->admitReplica("k" + std::to_string(i), makeValueOfSize(sizes[i]),
                                    context(std::to_string(i), "preferred"));
        CACHE_REQUIRE(outcomeCount(fixture.sink) == 1,
                      "each admission attempt must produce exactly one outcome event");
    }
}

CACHE_TEST(event_counts_close_against_resident_state) {
    Fixture fixture(CachePolicy::Lru, 3 * 65536);
    for (int i = 0; i < 12; ++i) {
        fixture.cache->admitReplica("k" + std::to_string(i % 7), makeValueOfSize(65536),
                                    context(std::to_string(i), "preferred"));
    }
    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.admits - gauge.evictions == gauge.replica_objects,
                  "admits - evictions must equal the resident object count");
    CACHE_REQUIRE(gauge.replica_bytes == gauge.replica_objects * 65536,
                  "resident bytes must equal resident objects times the object size");
    CACHE_REQUIRE(gauge.evicted_bytes == gauge.evictions * 65536,
                  "evicted bytes must close against the eviction count");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == gauge.admits,
                  "replica_admit events must match the admit counter");
    CACHE_REQUIRE(fixture.sink.count("replica_evict") == gauge.evictions,
                  "replica_evict events must match the eviction counter");
    CACHE_REQUIRE(gauge.replica_bytes <= gauge.budget_bytes,
                  "resident bytes must never exceed the budget");
}

CACHE_TEST(owner_reads_do_not_touch_replica_lru) {
    Fixture fixture(CachePolicy::Lru, 2 * 65536);
    fixture.cache->seedOwner("owned", makeValueOfSize(65536));
    fixture.cache->admitReplica("a", makeValueOfSize(65536), context("1", "preferred"));
    fixture.cache->admitReplica("b", makeValueOfSize(65536), context("2", "preferred"));

    const auto owner = fixture.cache->lookupForService("owned");
    CACHE_REQUIRE(owner.value.has_value(), "owner not served");
    CACHE_REQUIRE(owner.tier == CacheTier::Owner, "owner tier wrong");
    CACHE_REQUIRE(!owner.lru_touched, "owner reads must not touch replica LRU");
    CACHE_REQUIRE(fixture.cache->gauge().touches == 0, "owner read counted a touch");
    CACHE_REQUIRE(fixture.cache->gauge().serves == 0, "owner read counted a replica serve");

    const auto replica = fixture.cache->lookupForService("a");
    CACHE_REQUIRE(replica.tier == CacheTier::Replica, "replica tier wrong");
    CACHE_REQUIRE(replica.lru_touched, "a replica serve must refresh recency");
    CACHE_REQUIRE(fixture.cache->gauge().touches == 1, "replica serve must count one touch");

    // a is now MRU, so admitting a third object evicts b.
    const auto third = fixture.cache->admitReplica("c", makeValueOfSize(65536),
                                                   context("3", "preferred"));
    CACHE_REQUIRE(third.victims.size() == 1 && third.victims.at(0).key == "b",
                  "the served replica must have been promoted to MRU");
}

CACHE_TEST(tier_of_and_exists_do_not_touch) {
    Fixture fixture(CachePolicy::Lru, 2 * 65536);
    fixture.cache->seedOwner("owned", makeValueOfSize(65536));
    fixture.cache->admitReplica("rep", makeValueOfSize(65536), context("1", "preferred"));
    for (int i = 0; i < 5; ++i) {
        CACHE_REQUIRE(fixture.cache->tierOf("owned") == CacheTier::Owner, "owner tier");
        CACHE_REQUIRE(fixture.cache->tierOf("rep") == CacheTier::Replica, "replica tier");
        CACHE_REQUIRE(fixture.cache->tierOf("missing") == CacheTier::Miss, "miss tier");
        CACHE_REQUIRE(fixture.cache->exists("rep"), "exists");
    }
    CACHE_REQUIRE(fixture.cache->gauge().touches == 0, "probes must not touch");
}

CACHE_TEST(replica_divergence_is_reported_and_repaired) {
    Fixture fixture(CachePolicy::Lru, 4 * 65536);
    fixture.cache->admitReplica("k", makeValueOfSize(65536), context("1", "preferred"));
    fixture.backend->eraseDirect("replica:k");
    fixture.sink.clear();

    const auto lookup = fixture.cache->lookupForService("k", context("2", "preferred"));
    CACHE_REQUIRE(!lookup.value.has_value(), "an unbacked replica must not be served");
    CACHE_REQUIRE(lookup.tier == CacheTier::Miss, "tier must be miss");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "divergence event");
    const auto error = fixture.sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "reason") == "replica_kv_missing",
                  "divergence reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "fatal") == "0",
                  "a replica divergence is repairable, not fatal");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("k") == 1,
                  "directory repair is deferred to maintain()");
    fixture.cache->maintain();
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("k") == 0,
                  "maintain() must drop the unservable entry");
    CACHE_REQUIRE(fixture.cache->gauge().replica_bytes == 0, "bytes must return to zero");
}

CACHE_TEST(owner_kv_loss_is_fatal_and_reported) {
    Fixture fixture(CachePolicy::Lru, 4 * 65536);
    fixture.cache->seedOwner("owned", makeValueOfSize(65536));
    fixture.backend->eraseDirect("owner:owned");
    fixture.sink.clear();

    bool threw = false;
    try {
        fixture.cache->lookupForService("owned", context("1", "preferred"));
    } catch (const std::runtime_error&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "a missing owner object must fail the run rather than serve nothing");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "owner loss event");
    const auto error = fixture.sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "reason") == "owner_kv_missing",
                  "owner loss reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "fatal") == "1",
                  "owner loss must be marked fatal");
}

CACHE_TEST(config_and_gauge_events_describe_the_run) {
    Fixture fixture(CachePolicy::Lru, 262144);
    fixture.cache->seedOwner("owned", makeValueOfSize(65536));
    fixture.cache->admitReplica("rep", makeValueOfSize(65536), context("1", "preferred"));
    fixture.sink.clear();

    fixture.cache->emitConfigEvent();
    CACHE_REQUIRE(fixture.sink.count("cache_config") == 1, "one cache_config per call");
    const auto config = fixture.sink.named("cache_config").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(config, "budget_mode") == "lru", "config policy");
    CACHE_REQUIRE(RecordingCacheEventSink::field(config, "budget_bytes") == "262144", "config budget");
    CACHE_REQUIRE(RecordingCacheEventSink::field(config, "namespace_owner") == "owner", "owner ns");
    CACHE_REQUIRE(RecordingCacheEventSink::field(config, "namespace_replica") == "replica", "replica ns");
    CACHE_REQUIRE(RecordingCacheEventSink::field(config, "value_granularity") == "value_bytes_only",
                  "granularity");
    CACHE_REQUIRE(RecordingCacheEventSink::field(config, "gauge_interval_ms") == "0", "gauge interval");
    CACHE_REQUIRE(RecordingCacheEventSink::has(config, "redis_maxmemory"), "redis maxmemory recorded");

    fixture.cache->emitGaugeEvent();
    CACHE_REQUIRE(fixture.sink.count("cache_gauge") == 1, "one cache_gauge per call");
    const auto gauge = fixture.sink.named("cache_gauge").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(gauge, "owner_objects") == "1", "gauge owner count");
    CACHE_REQUIRE(RecordingCacheEventSink::field(gauge, "replica_objects") == "1", "gauge replica count");
    CACHE_REQUIRE(RecordingCacheEventSink::field(gauge, "owner_bytes") == "65536", "gauge owner bytes");
    CACHE_REQUIRE(RecordingCacheEventSink::field(gauge, "resident_bytes") == "65536", "gauge resident");
    CACHE_REQUIRE(RecordingCacheEventSink::field(gauge, "admits") == "1", "gauge admits");
    CACHE_REQUIRE(RecordingCacheEventSink::field(gauge, "serves") == "0", "gauge serves");
    CACHE_REQUIRE(RecordingCacheEventSink::field(gauge, "reason") == "periodic", "gauge reason");
}

CACHE_TEST(legacy_policy_emits_no_events_at_all) {
    Fixture fixture(CachePolicy::Legacy, 0);
    fixture.cache->seedOwner("owned", makeValueOfSize(65536));
    fixture.cache->admitReplica("rep", makeValueOfSize(65536), context("1", "preferred"));
    fixture.cache->emitConfigEvent();
    fixture.cache->emitGaugeEvent();
    CACHE_REQUIRE(fixture.sink.events().empty(),
                  "legacy mode must not add cache events to frozen trace semantics");
    CACHE_REQUIRE(fixture.cache->tierOf("rep") == CacheTier::Unknown,
                  "legacy mode cannot attribute a tier and must say unknown, not guess");
    // Legacy keeps the flat key space.
    CACHE_REQUIRE(fixture.backend->hasPhysical("rep"),
                  "legacy mode must write bare logical keys");
}

// ---------------------------------------------------------------------------
// Legacy backend-error propagation
//
// The original Node (baseline 0feb4b22) called cache_->get()/put()/exists()
// directly and let a backend failure propagate; it never turned one into a
// cache miss, an AlreadyResident skip or a non-fatal BackendError. The facade
// must keep that for legacy. FaultyCache throws a typed BackendFailure before
// any side effect; it is explicitly NOT a model of a Redis timeout where the
// command may already have been applied.
// ---------------------------------------------------------------------------

CACHE_TEST(legacy_lookup_get_failure_propagates_without_a_cache_event) {
    Fixture fixture(CachePolicy::Legacy, 0);
    fixture.cache->seedOwner("legacy-get", makeValueOfSize(10, 'a'));
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("legacy-get") == 1,
                  "the owner entry must be in the directory");
    fixture.sink.clear();
    fixture.backend->failGets();

    bool propagated = false;
    std::string message;
    try {
        (void)fixture.cache->lookupForService("legacy-get");
    } catch (const cache_test::BackendFailure& e) {
        propagated = true;
        message = e.what();
    }
    CACHE_REQUIRE(propagated, "a legacy local read failure must propagate, not become a miss");
    CACHE_REQUIRE(message.find("legacy-get") != std::string::npos,
                  "the original failure must be rethrown with its key intact");
    CACHE_REQUIRE(fixture.sink.events().empty(), "legacy must not emit cache events");
    CACHE_REQUIRE(fixture.backend->countCalls("get:legacy-get") == 1,
                  "exactly one read is attempted");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("legacy-get") == 1,
                  "a failed read must not change the directory");
    CACHE_REQUIRE(fixture.backend->hasPhysical("legacy-get"),
                  "a failed read must not change the stored value");
    CACHE_REQUIRE(!fixture.backend->hasPhysical("replica:legacy-get"),
                  "legacy must never use a replica: prefix");
}

CACHE_TEST(legacy_seed_put_failure_propagates_and_leaves_no_state) {
    Fixture fixture(CachePolicy::Legacy, 0);
    fixture.backend->failPuts();

    bool propagated = false;
    std::string message;
    try {
        fixture.cache->seedOwner("legacy-put", makeValueOfSize(10, 'a'));
    } catch (const cache_test::BackendFailure& e) {
        propagated = true;
        message = e.what();
    }
    CACHE_REQUIRE(propagated, "a legacy owner write failure must propagate unchanged");
    CACHE_REQUIRE(message.find("legacy-put") != std::string::npos,
                  "the original failure must be rethrown with its key intact");
    CACHE_REQUIRE(!fixture.backend->hasPhysical("legacy-put"),
                  "a failed write must have no side effect");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("legacy-put") == 0,
                  "a failed write must not add a directory entry");
    CACHE_REQUIRE(fixture.cache->tierOf("legacy-put") == CacheTier::Unknown,
                  "a failed write must not change residency");
    CACHE_REQUIRE(fixture.backend->countCalls("put:legacy-put") == 1,
                  "the write is attempted exactly once");
    CACHE_REQUIRE(!fixture.backend->sawCall("put:owner:legacy-put"),
                  "legacy must never use an owner: prefix");
    CACHE_REQUIRE(fixture.sink.events().empty(), "legacy must not emit cache events");
}

CACHE_TEST(legacy_admit_put_failure_propagates_and_leaves_no_directory_entry) {
    Fixture fixture(CachePolicy::Legacy, 0);
    fixture.backend->failPuts();

    bool propagated = false;
    try {
        (void)fixture.cache->admitReplica("legacy-admit", makeValueOfSize(10, 'a'),
                                          context("1", "preferred"));
    } catch (const cache_test::BackendFailure&) {
        propagated = true;
    }
    CACHE_REQUIRE(propagated, "legacy admission must propagate a backend write failure");
    CACHE_REQUIRE(fixture.backend->countCalls("exists:legacy-admit") == 1,
                  "exactly one existence probe, as the original Node made");
    CACHE_REQUIRE(fixture.backend->countCalls("put:legacy-admit") == 1,
                  "the write is attempted exactly once");
    CACHE_REQUIRE(!fixture.backend->sawCall("put:replica:legacy-admit"),
                  "legacy must never use a replica: prefix");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("legacy-admit") == 0,
                  "a failed write must not enter the directory");
    CACHE_REQUIRE(fixture.cache->tierOf("legacy-admit") == CacheTier::Unknown,
                  "a failed write must not change residency");
    CACHE_REQUIRE(fixture.sink.events().empty(), "legacy must not emit cache events");
}

CACHE_TEST(legacy_exists_failure_propagates_and_no_write_is_attempted) {
    Fixture fixture(CachePolicy::Legacy, 0);
    fixture.backend->failExists();

    bool propagated = false;
    std::string message;
    try {
        (void)fixture.cache->exists("legacy-exists");
    } catch (const cache_test::BackendFailure& e) {
        propagated = true;
        message = e.what();
    }
    CACHE_REQUIRE(propagated, "a legacy exists() failure must propagate unchanged");
    CACHE_REQUIRE(message.find("legacy-exists") != std::string::npos,
                  "the original failure must be rethrown with its key intact");

    bool admitted = false;
    try {
        (void)fixture.cache->admitReplica("legacy-exists", makeValueOfSize(10, 'a'));
    } catch (const cache_test::BackendFailure&) {
        admitted = true;
    }
    CACHE_REQUIRE(admitted, "admission must propagate an exists() failure");
    CACHE_REQUIRE(fixture.backend->countCalls("exists:legacy-exists") == 2,
                  "both the direct probe and the admission probe reach the backend");
    CACHE_REQUIRE(fixture.backend->countCalls("put:legacy-exists") == 0,
                  "no write may be attempted after a failed existence probe");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("legacy-exists") == 0,
                  "a failed existence probe must not enter the directory");
    CACHE_REQUIRE(fixture.sink.events().empty(), "legacy must not emit cache events");
}

CACHE_TEST(legacy_normal_paths_use_bare_keys_and_stay_silent) {
    Fixture fixture(CachePolicy::Legacy, 0);
    fixture.cache->seedOwner("legacy-bare", makeValueOfSize(10, 'a'));
    const auto admit = fixture.cache->admitReplica("legacy-bare-rep", makeValueOfSize(20, 'b'));
    CACHE_REQUIRE(admit.inserted(), "legacy DATA admission still stores");

    CACHE_REQUIRE(fixture.backend->hasPhysical("legacy-bare"), "owner written bare");
    CACHE_REQUIRE(fixture.backend->hasPhysical("legacy-bare-rep"), "replica written bare");
    CACHE_REQUIRE(!fixture.backend->hasPhysical("owner:legacy-bare"), "no owner: prefix");
    CACHE_REQUIRE(!fixture.backend->hasPhysical("replica:legacy-bare-rep"), "no replica: prefix");
    CACHE_REQUIRE(!fixture.backend->sawCall("put:owner:legacy-bare"), "no prefixed owner put");
    CACHE_REQUIRE(!fixture.backend->sawCall("put:replica:legacy-bare-rep"),
                  "no prefixed replica put");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("legacy-bare") == 1,
                  "owner directory entry");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("legacy-bare-rep") == 1,
                  "replica directory entry");
    CACHE_REQUIRE(fixture.sink.events().empty(), "legacy stays silent");
}

// ---------------------------------------------------------------------------
// Indeterminate backend results ("applied, then response lost")
//
// FaultyCache::applyThenThrow* applies the side effect and then throws
// BackendIndeterminate. These tests reproduce the OLD assumption errors: a
// failed delete does not prove the victim is still resident, and a failed write
// does not prove the object is absent. A confirmed operation before the
// uncertain one must still be accounted for, and the store must refuse to
// continue once its state is unknown.
// ---------------------------------------------------------------------------

CACHE_TEST(indeterminate_replica_put_poisons_the_facade_and_is_not_a_success) {
    Fixture fixture(CachePolicy::Lru, 4 * 65536);
    fixture.backend->applyThenThrowPuts();

    bool threw = false;
    try {
        (void)fixture.cache->admitReplica("ind-put", makeValueOfSize(100, 'a'),
                                          context("1", "preferred"));
    } catch (const rsync_cache::BackendIndeterminate&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "an indeterminate write must throw, not return an admission");
    // The SET was applied before the response was lost: "put threw => absent"
    // is exactly the assumption being tested against.
    CACHE_REQUIRE(fixture.backend->hasPhysical("replica:ind-put"),
                  "the value may already be stored even though the call threw");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0,
                  "an unconfirmed result must not emit a success event");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "one fatal backend error");
    const auto error = fixture.sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "reason") ==
                      "replica_put_indeterminate",
                  "reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "category") == "indeterminate",
                  "category must say the result is unknown");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "fatal") == "1", "must be fatal");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "cache_contract_revision") == "4",
                  "error events must declare the revision-4 category semantics");
    CACHE_REQUIRE(fixture.cache->gauge().admits == 0, "no admit may be counted");
    CACHE_REQUIRE(fixture.cache->gauge().replica_objects == 0,
                  "no index entry may claim residency");
    CACHE_REQUIRE(!fixture.cache->available(), "the facade must be poisoned");

    bool unavailable = false;
    try {
        (void)fixture.cache->admitReplica("ind-put-2", makeValueOfSize(10),
                                          context("2", "preferred"));
    } catch (const rsync_cache::CacheUnavailable&) {
        unavailable = true;
    }
    CACHE_REQUIRE(unavailable, "a poisoned facade must refuse further admission");
}

CACHE_TEST(indeterminate_victim_delete_keeps_confirmed_victim_and_stops) {
    // budget 100: A(40) then B(40) -> 80 resident, LRU order [B, A] so A is the
    // true LRU victim. Admitting E(90) evicts A (confirmed) then B (indeterminate).
    Fixture fixture(CachePolicy::Lru, 100);
    fixture.cache->admitReplica("A", makeValueOfSize(40, 'a'), context("0", "preferred"));
    fixture.cache->admitReplica("B", makeValueOfSize(40, 'b'), context("0", "preferred"));
    const auto before = fixture.cache->gauge();
    fixture.sink.clear();
    // The first delete (A) is confirmed; the second (B) applies then loses its
    // response. del_calls_ is 0 here, so relative offset 1 is the second delete.
    fixture.backend->applyThenThrowDeleteAtRelative(1);

    bool threw = false;
    try {
        (void)fixture.cache->admitReplica("E", makeValueOfSize(90, 'e'),
                                          context("1", "preferred"));
    } catch (const rsync_cache::IndeterminateAdmission&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "an indeterminate victim delete must stop the admission");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0,
                  "the new object must not be admitted");
    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 1,
                  "only the confirmed victim may produce an eviction event");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      fixture.sink.named("replica_evict").at(0), "key") == "A",
                  "the confirmed victim is A");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "one fatal backend error");
    const auto error = fixture.sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "reason") ==
                      "replica_evict_indeterminate",
                  "reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "category") == "indeterminate",
                  "category");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "fatal") == "1", "fatal");

    // Confirmed A is really gone and counted; B is neither counted as evicted
    // nor advertised as resident, even though the side effect removed it.
    CACHE_REQUIRE(!fixture.backend->hasPhysical("replica:A"), "confirmed delete applied");
    CACHE_REQUIRE(!fixture.backend->hasPhysical("replica:B"),
                  "the indeterminate delete was applied before the response was lost");
    CACHE_REQUIRE(!fixture.backend->hasPhysical("replica:E"), "E was never stored");
    CACHE_REQUIRE(fixture.cache->gauge().evictions == before.evictions + 1,
                  "exactly one confirmed eviction");
    CACHE_REQUIRE(fixture.cache->gauge().evicted_bytes == before.evicted_bytes + 40,
                  "only A's bytes are counted as evicted");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("B") == 0,
                  "B must not be advertised as still resident");
    CACHE_REQUIRE(fixture.cache->tierOf("B") == CacheTier::Miss,
                  "B must not be classified as a resident replica");
    CACHE_REQUIRE(!fixture.cache->available(), "the facade must be poisoned");
}

CACHE_TEST(pre_side_effect_failure_stays_distinct_from_indeterminate) {
    Fixture fixture(CachePolicy::Lru, 4 * 65536);
    fixture.backend->failPuts();
    const auto failed = fixture.cache->admitReplica("pre", makeValueOfSize(10, 'a'),
                                                    context("1", "preferred"));
    CACHE_REQUIRE(failed.backend_error(),
                  "a proved pre-side-effect failure is an ordinary backend error");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "one backend error event");
    const auto error = fixture.sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "category") == "pre_side_effect",
                  "category must say the state is known unchanged");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "fatal") == "0",
                  "a pre-side-effect failure is not fatal by itself");
    CACHE_REQUIRE(fixture.cache->available(),
                  "a pre-side-effect failure must not poison the store");
    fixture.backend->clearFaults();
    const auto recovered = fixture.cache->admitReplica("pre", makeValueOfSize(10, 'a'),
                                                       context("2", "preferred"));
    CACHE_REQUIRE(recovered.inserted(), "the store can still be used after a known failure");
}

CACHE_TEST(indeterminate_read_is_not_a_miss_and_poisons_the_facade) {
    Fixture fixture(CachePolicy::Lru, 4 * 65536);
    fixture.cache->admitReplica("read", makeValueOfSize(100, 'a'), context("0", "preferred"));
    fixture.sink.clear();
    fixture.backend->indeterminateGets();

    bool threw = false;
    try {
        (void)fixture.cache->lookupForService("read");
    } catch (const rsync_cache::BackendIndeterminate&) {
        threw = true;
    }
    CACHE_REQUIRE(threw,
                  "an unconfirmable read must throw, not become a cache miss + remote fetch");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "one fatal backend error");
    const auto error = fixture.sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "reason") ==
                      "replica_get_indeterminate",
                  "reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "category") == "indeterminate",
                  "category");
    CACHE_REQUIRE(!fixture.cache->available(), "the facade must be poisoned");

    bool unavailable = false;
    try {
        (void)fixture.cache->lookupForService("read");
    } catch (const rsync_cache::CacheUnavailable&) {
        unavailable = true;
    }
    CACHE_REQUIRE(unavailable, "later serving must be refused, not silently continued");
}

CACHE_TEST(indeterminate_owner_seed_poisons_and_does_not_claim_success) {
    Fixture fixture(CachePolicy::Lru, 4 * 65536);
    fixture.backend->applyThenThrowPuts();
    bool threw = false;
    try {
        fixture.cache->seedOwner("owner-ind", makeValueOfSize(100, 'a'));
    } catch (const rsync_cache::BackendIndeterminate&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "an indeterminate owner write must throw");
    CACHE_REQUIRE(fixture.backend->hasPhysical("owner:owner-ind"),
                  "the owner SET may already have been applied");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("owner-ind") == 0,
                  "a failed owner seed must not enter the directory");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "one fatal backend error");
    const auto error = fixture.sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "reason") == "owner_put_indeterminate",
                  "reason");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "category") == "indeterminate",
                  "category");
    CACHE_REQUIRE(!fixture.cache->available(), "the facade must be poisoned");
}

// The plain (non-IndeterminateAdmission) BackendIndeterminate path: a duplicate
// admission re-reads the already-resident replica when no integrity receipt is
// available (production fromEnv() sets verify_replica_content=true). The facade
// must be poisoned even though there is no confirmed victim to finalize, and it
// must be poisoned only once.
CACHE_TEST(indeterminate_duplicate_content_read_poisons_the_whole_facade) {
    RecordingCacheEventSink sink;
    auto backend_owner = std::make_unique<cache_test::FaultyCache>(0);
    auto* backend = backend_owner.get();
    auto config = policyConfig(CachePolicy::Lru, 4 * 65536);
    config.verify_replica_content = true;  // production: fromEnv() forces this
    NodeCache cache(std::move(backend_owner), config, &sink);

    cache.seedOwner("owned", makeValueOfSize(50, 'o'));
    cache.admitReplica("dup", makeValueOfSize(100, 'a'), context("1", "preferred"));
    CACHE_REQUIRE(cache.gauge().replica_objects == 1, "the replica must be resident");
    sink.clear();

    backend->indeterminateGets();
    bool threw = false;
    try {
        // No receipt: ReplicaCache::admit() re-reads the resident replica and
        // throws a plain BackendIndeterminate.
        (void)cache.admitReplica("dup", makeValueOfSize(100, 'a'), context("2", "preferred"));
    } catch (const rsync_cache::BackendIndeterminate&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "an indeterminate duplicate content read must throw");
    CACHE_REQUIRE(!cache.available(), "the whole facade must be poisoned");
    CACHE_REQUIRE(sink.count("cache_backend_error") == 1,
                  "exactly one error event for one failure, got " +
                      std::to_string(sink.count("cache_backend_error")));
    const auto error = sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "reason") == "replica_get_indeterminate",
                  "reason must name the replica read");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "category") == "indeterminate", "category");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "fatal") == "1", "fatal");
    CACHE_REQUIRE(sink.count("replica_evict") == 0,
                  "a read-only uncertainty has no victim to finalize");

    // After the failure, serving an OWNER object must be refused, not silently
    // served from the still-readable in-memory owner index.
    bool serve_refused = false;
    try {
        (void)cache.lookupForService("owned");
    } catch (const rsync_cache::CacheUnavailable&) {
        serve_refused = true;
    }
    CACHE_REQUIRE(serve_refused, "owner service must be refused while the facade is unavailable");

    bool admit_refused = false;
    try {
        (void)cache.admitReplica("dup2", makeValueOfSize(10), context("3", "preferred"));
    } catch (const rsync_cache::CacheUnavailable&) {
        admit_refused = true;
    }
    CACHE_REQUIRE(admit_refused, "a later admission must be refused");
    CACHE_REQUIRE(sink.count("cache_backend_error") == 1,
                  "refusing later calls must not add more error events");
}

CACHE_TEST(indeterminate_legacy_lookup_poisons_the_facade) {
    Fixture fixture(CachePolicy::Legacy, 0);
    fixture.cache->seedOwner("legacy-ind", makeValueOfSize(50, 'a'));
    fixture.backend->indeterminateGets();
    bool threw = false;
    try {
        (void)fixture.cache->lookupForService("legacy-ind");
    } catch (const rsync_cache::BackendIndeterminate&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "legacy must still propagate an indeterminate read");
    CACHE_REQUIRE(!fixture.cache->available(), "legacy must poison the facade too");
    CACHE_REQUIRE(fixture.sink.events().empty(), "legacy emits no cache events by contract");
}

CACHE_TEST(indeterminate_legacy_exists_poisons_the_facade) {
    Fixture fixture(CachePolicy::Legacy, 0);
    fixture.backend->applyThenThrowExists();
    bool threw = false;
    try {
        (void)fixture.cache->exists("legacy-exists");
    } catch (const rsync_cache::BackendIndeterminate&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "legacy must still propagate an indeterminate exists()");
    CACHE_REQUIRE(!fixture.cache->available(), "legacy exists() must poison the facade");
}

CACHE_TEST(indeterminate_owner_reseed_read_poisons_once) {
    Fixture fixture(CachePolicy::Lru, 4 * 65536);
    fixture.cache->seedOwner("own-reseed", makeValueOfSize(50, 'a'));
    fixture.sink.clear();
    // A same-size re-seed compares content, reading the backend.
    fixture.backend->indeterminateGets();
    bool threw = false;
    try {
        fixture.cache->seedOwner("own-reseed", makeValueOfSize(50, 'a'));
    } catch (const rsync_cache::BackendIndeterminate&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "an indeterminate owner re-seed comparison must throw");
    CACHE_REQUIRE(!fixture.cache->available(), "the facade must be poisoned");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "exactly one error event");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      fixture.sink.named("cache_backend_error").at(0), "reason") ==
                      "owner_get_indeterminate",
                  "reason must name the owner read");
}

namespace {

void clearCacheEnv() {
    for (const char* name : {"RSYNC_CACHE_POLICY", "RSYNC_REPLICA_BUDGET_BYTES",
                             "RSYNC_REPLICA_BUDGET_OBJECTS",
                             "RSYNC_REPLICA_BUDGET_OBJECT_BYTES",
                             "RSYNC_CACHE_VERIFY_REPLICA_CONTENT",
                             "RSYNC_CACHE_GAUGE_INTERVAL_MS"}) {
        unsetenv(name);
    }
}

bool throwsOnFromEnv() {
    try {
        (void)CachePolicyConfig::fromEnv();
        return false;
    } catch (const std::runtime_error&) {
        return true;
    }
}

}  // namespace

CACHE_TEST(policy_config_from_env_validates_and_converts) {
    clearCacheEnv();
    auto config = CachePolicyConfig::fromEnv();
    CACHE_REQUIRE(config.policy == CachePolicy::Legacy, "default policy must stay legacy");
    CACHE_REQUIRE(config.budget_bytes == 0, "legacy has no budget");

    setenv("RSYNC_CACHE_POLICY", "unlimited", 1);
    config = CachePolicyConfig::fromEnv();
    CACHE_REQUIRE(config.policy == CachePolicy::Unlimited, "unlimited policy");
    CACHE_REQUIRE(config.budgetUnlimited(), "unlimited must be an explicit, separate value");
    CACHE_REQUIRE(config.budget_bytes == 0, "unlimited keeps budget_bytes at 0 internally");

    setenv("RSYNC_CACHE_POLICY", "bypass", 1);
    CACHE_REQUIRE(CachePolicyConfig::fromEnv().policy == CachePolicy::Bypass, "bypass policy");

    // lru needs a budget; the object form is converted with the object size.
    setenv("RSYNC_CACHE_POLICY", "lru", 1);
    CACHE_REQUIRE(throwsOnFromEnv(), "lru without a budget must fail loudly");

    setenv("RSYNC_REPLICA_BUDGET_BYTES", "262144", 1);
    config = CachePolicyConfig::fromEnv();
    CACHE_REQUIRE(config.policy == CachePolicy::Lru, "lru policy");
    CACHE_REQUIRE(config.budget_bytes == 262144, "byte budget");
    unsetenv("RSYNC_REPLICA_BUDGET_BYTES");

    setenv("RSYNC_REPLICA_BUDGET_OBJECTS", "4", 1);
    CACHE_REQUIRE(CachePolicyConfig::fromEnv().budget_bytes == 262144,
                  "4 objects of 64 KiB must convert to 262144 bytes");
    setenv("RSYNC_REPLICA_BUDGET_OBJECT_BYTES", "1024", 1);
    CACHE_REQUIRE(CachePolicyConfig::fromEnv().budget_bytes == 4096,
                  "object size must be configurable");
    unsetenv("RSYNC_REPLICA_BUDGET_OBJECT_BYTES");

    setenv("RSYNC_REPLICA_BUDGET_BYTES", "262144", 1);
    CACHE_REQUIRE(throwsOnFromEnv(),
                  "setting both byte and object budgets must be rejected");
    unsetenv("RSYNC_REPLICA_BUDGET_OBJECTS");  // keep only the byte budget

    setenv("RSYNC_CACHE_VERIFY_REPLICA_CONTENT", "1", 1);
    CACHE_REQUIRE(CachePolicyConfig::fromEnv().verify_replica_content,
                  "content verification flag");
    setenv("RSYNC_CACHE_GAUGE_INTERVAL_MS", "0", 1);
    CACHE_REQUIRE(CachePolicyConfig::fromEnv().gauge_interval_ms == 0,
                  "gauge interval 0 disables the periodic gauge");

    // A budget is meaningless in legacy mode: reject rather than ignore.
    setenv("RSYNC_CACHE_POLICY", "legacy", 1);
    CACHE_REQUIRE(throwsOnFromEnv(), "legacy with a budget must be rejected");

    unsetenv("RSYNC_REPLICA_BUDGET_BYTES");
    setenv("RSYNC_CACHE_POLICY", "not-a-policy", 1);
    CACHE_REQUIRE(throwsOnFromEnv(), "an unknown policy must be rejected");
    clearCacheEnv();
}

// ---------------------------------------------------------------------------
// Partial-success eviction: ReplicaCache deletes LRU victims before it tries to
// store the new object, so an admission can fail after some victims were really
// deleted. These tests pin the facade-level contract for that case.
// ---------------------------------------------------------------------------
namespace {

struct EvictFixture {
    RecordingCacheEventSink sink;
    std::unique_ptr<cache_test::FaultyCache> backend_owner;
    cache_test::FaultyCache* backend = nullptr;
    std::unique_ptr<NodeCache> cache;

    explicit EvictFixture(std::size_t budget) {
        backend_owner = std::make_unique<cache_test::FaultyCache>(0);
        backend = backend_owner.get();
        cache = std::make_unique<NodeCache>(std::move(backend_owner),
                                            policyConfig(CachePolicy::Lru, budget), &sink);
    }

    void admit(const std::string& key, std::size_t bytes) {
        const auto result = cache->admitReplica(key, makeValueOfSize(bytes),
                                                context("0", "preferred"));
        CACHE_REQUIRE(result.inserted(), "setup admission of " + key + " must succeed");
    }
};

std::size_t eventIndex(const RecordingCacheEventSink& sink, const std::string& name) {
    const auto names = sink.names();
    for (std::size_t i = 0; i < names.size(); ++i) {
        if (names[i] == name) return i;
    }
    return names.size();  // "absent" sorts after every present event
}

// Not observable anywhere: absent from the backing KV, from the replica index
// (tierOf consults it) and from the serviceable directory.
void requireAbsent(NodeCache& cache, cache_test::FaultyCache& backend,
                   const std::string& key, const std::string& where) {
    CACHE_REQUIRE(!backend.hasPhysical("replica:" + key),
                  where + ": '" + key + "' is still in the backing KV");
    CACHE_REQUIRE(cache.tierOf(key) == CacheTier::Miss,
                  where + ": '" + key + "' is still in the replica/owner index");
    CACHE_REQUIRE(cache.serviceableKeys().count(key) == 0,
                  where + ": '" + key + "' is still advertised as serviceable");
}

void requireResident(NodeCache& cache, cache_test::FaultyCache& backend,
                     const std::string& key, const std::string& where) {
    CACHE_REQUIRE(backend.hasPhysical("replica:" + key),
                  where + ": '" + key + "' is missing from the backing KV");
    CACHE_REQUIRE(cache.tierOf(key) == CacheTier::Replica,
                  where + ": '" + key + "' is missing from the replica index");
    CACHE_REQUIRE(cache.serviceableKeys().count(key) == 1,
                  where + ": '" + key + "' is missing from the serviceable directory");
}

}  // namespace

CACHE_TEST(eviction_succeeded_then_put_failed_still_cleans_up_and_records_eviction) {
    EvictFixture fixture(4096);
    fixture.cache->seedOwner("owned", makeValueOfSize(1024));
    fixture.admit("A", 4096);
    CACHE_REQUIRE(fixture.cache->gauge().admits == 1, "setup admit must be counted");

    fixture.sink.clear();
    // The next put (B's) throws before touching storage.
    fixture.backend->failPutAtRelative(0);
    const auto result = fixture.cache->admitReplica("B", makeValueOfSize(4096),
                                                    context("7", "preferred"));
    CACHE_REQUIRE(result.backend_error(), "the put failure must surface as a backend error");
    CACHE_REQUIRE(result.victims.size() == 1, "A must be reported as an already-deleted victim");
    CACHE_REQUIRE(result.victims.at(0).key == "A", "the reported victim must be A");

    // A was really deleted before the put failed: it must be gone everywhere.
    requireAbsent(*fixture.cache, *fixture.backend, "A", "after the failed put");
    // B was never stored.
    requireAbsent(*fixture.cache, *fixture.backend, "B", "rejected admission");
    // The owner is untouched and still readable.
    CACHE_REQUIRE(fixture.cache->tierOf("owned") == CacheTier::Owner, "owner tier lost");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("owned") == 1,
                  "owner directory entry must survive eviction cleanup");
    CACHE_REQUIRE(fixture.cache->lookupForService("owned").value.has_value(),
                  "owner must still be readable");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.evictions == 1, "exactly one successful eviction");
    CACHE_REQUIRE(gauge.admits == 1, "a failed admission must not count as an admit");
    CACHE_REQUIRE(gauge.replica_objects == 0, "no replica may remain resident");
    CACHE_REQUIRE(gauge.replica_bytes == 0, "no replica bytes may remain");

    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 1, "exactly one replica_evict");
    const auto evict = fixture.sink.named("replica_evict").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(evict, "key") == "A", "eviction key");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evict, "value_bytes") == "4096",
                  "eviction value_bytes");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "exactly one backend error");
    const auto error = fixture.sink.named("cache_backend_error").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "key") == "B", "error key");
    CACHE_REQUIRE(RecordingCacheEventSink::field(error, "reason") == "replica_put_failed",
                  "error reason");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0,
                  "a failed admission must not emit replica_admit");
    CACHE_REQUIRE(eventIndex(fixture.sink, "replica_evict") <
                      eventIndex(fixture.sink, "cache_backend_error"),
                  "the eviction must be recorded before the final error event");
    CACHE_REQUIRE(outcomeCount(fixture.sink) == 1, "exactly one final outcome event");
}

CACHE_TEST(second_victim_delete_failure_keeps_it_resident_and_records_only_the_first) {
    EvictFixture fixture(100);
    fixture.admit("A", 40);
    fixture.admit("B", 40);  // LRU order [B, A]: A is evicted first
    CACHE_REQUIRE(fixture.cache->gauge().replica_bytes == 80, "setup resident bytes");

    fixture.sink.clear();
    // A's delete succeeds; the following delete (B's) throws before its side
    // effect, so B stays in storage.
    fixture.backend->failDeleteAtRelative(1);
    const auto result = fixture.cache->admitReplica("D", makeValueOfSize(90),
                                                    context("8", "preferred"));
    CACHE_REQUIRE(result.backend_error(), "the failed delete must surface as a backend error");
    CACHE_REQUIRE(result.failing_op == "evict_delete", "failing op must be the eviction delete");
    CACHE_REQUIRE(result.victims.size() == 1, "only the successful victim may be reported");
    CACHE_REQUIRE(result.victims.at(0).key == "A", "the successful victim must be A");

    requireAbsent(*fixture.cache, *fixture.backend, "A", "successfully deleted victim");
    requireResident(*fixture.cache, *fixture.backend, "B", "undeleted victim");
    requireAbsent(*fixture.cache, *fixture.backend, "D", "rejected admission");
    CACHE_REQUIRE(fixture.cache->lookupForService("B").value.has_value(),
                  "the undeleted victim must still be readable");

    // The new object must never have been written.
    CACHE_REQUIRE(!fixture.backend->sawCall("put:replica:D"),
                  "no put may be issued after a failed eviction");
    CACHE_REQUIRE(fixture.backend->sawCall("del:replica:A"), "A's delete must be attempted");
    CACHE_REQUIRE(fixture.backend->sawCall("del:replica:B"), "B's delete must be attempted");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.evictions == 1, "only the successful eviction may be counted");
    CACHE_REQUIRE(gauge.admits == 2, "a failed admission must not count as an admit");
    CACHE_REQUIRE(gauge.replica_objects == 1, "only B may remain resident");
    CACHE_REQUIRE(gauge.replica_bytes == 40, "resident bytes must be B's size");

    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 1, "exactly one replica_evict");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      fixture.sink.named("replica_evict").at(0), "key") == "A",
                  "only A may be recorded as evicted; B must not be");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "exactly one final error");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      fixture.sink.named("cache_backend_error").at(0), "reason") ==
                      "replica_evict_failed",
                  "error reason must identify the failed delete");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0, "no admit event on failure");
    CACHE_REQUIRE(outcomeCount(fixture.sink) == 1, "exactly one final outcome event");
}

CACHE_TEST(successful_admission_with_multiple_victims_finalizes_each_exactly_once) {
    EvictFixture fixture(100);
    fixture.cache->seedOwner("owned", makeValueOfSize(10));
    fixture.admit("A", 40);
    fixture.admit("B", 40);

    fixture.sink.clear();
    const auto result = fixture.cache->admitReplica("D", makeValueOfSize(90),
                                                    context("9", "preferred"));
    CACHE_REQUIRE(result.inserted(), "D must be admitted");
    CACHE_REQUIRE(result.victims.size() == 2, "two victims expected");

    const auto evictions = fixture.sink.named("replica_evict");
    CACHE_REQUIRE(evictions.size() == 2, "each victim must be recorded exactly once");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evictions.at(0), "key") == "A",
                  "eviction order must follow the deletion order (A first)");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evictions.at(1), "key") == "B",
                  "eviction order must follow the deletion order (B second)");
    for (const auto& event : evictions) {
        CACHE_REQUIRE(RecordingCacheEventSink::field(event, "value_bytes") == "40",
                      "victim size must be reported");
    }
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 1, "exactly one admit event");

    requireAbsent(*fixture.cache, *fixture.backend, "A", "evicted victim");
    requireAbsent(*fixture.cache, *fixture.backend, "B", "evicted victim");
    requireResident(*fixture.cache, *fixture.backend, "D", "new object");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("owned") == 1,
                  "the owner entry must survive the common eviction cleanup");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.replica_objects == 1, "only D may remain resident");
    CACHE_REQUIRE(gauge.replica_bytes == 90, "resident bytes must be D's size");
    CACHE_REQUIRE(gauge.admits == 3, "three successful admissions");
    CACHE_REQUIRE(gauge.evictions == 2, "two evictions");
    // Scoped to this test: the replica cache started empty, and no divergence
    // repair or external delete happened.
    CACHE_REQUIRE(gauge.admits - gauge.evictions == gauge.replica_objects,
                  "admits - evictions must equal the resident replica count");
    CACHE_REQUIRE(outcomeCount(fixture.sink) == 1, "exactly one final outcome event");
}

CACHE_TEST(first_victim_delete_failure_leaves_state_unchanged) {
    EvictFixture fixture(100);
    fixture.admit("A", 40);
    fixture.admit("B", 40);
    const auto before = fixture.cache->gauge();

    fixture.sink.clear();
    // A's delete (the first victim) throws before its side effect.
    fixture.backend->failDeleteAtRelative(0);
    const auto result = fixture.cache->admitReplica("D", makeValueOfSize(90),
                                                    context("10", "preferred"));
    CACHE_REQUIRE(result.backend_error(), "expected a backend error");
    CACHE_REQUIRE(result.victims.empty(),
                  "no victim may be reported when the first delete failed");

    requireResident(*fixture.cache, *fixture.backend, "A", "undeleted victim");
    requireResident(*fixture.cache, *fixture.backend, "B", "untouched resident");
    requireAbsent(*fixture.cache, *fixture.backend, "D", "rejected admission");
    CACHE_REQUIRE(!fixture.backend->sawCall("put:replica:D"),
                  "no put may be attempted after the first delete failed");

    const auto after = fixture.cache->gauge();
    CACHE_REQUIRE(after.replica_objects == before.replica_objects, "object count changed");
    CACHE_REQUIRE(after.replica_bytes == before.replica_bytes, "byte count changed");
    CACHE_REQUIRE(after.evictions == before.evictions, "an eviction was counted");
    CACHE_REQUIRE(after.admits == before.admits, "an admit was counted");
    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 0, "no eviction event may be emitted");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0, "no admit event may be emitted");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "one clear backend error");
    CACHE_REQUIRE(outcomeCount(fixture.sink) == 1, "exactly one final outcome event");
}

CACHE_TEST(state_stays_consistent_when_a_failed_admission_is_followed_by_a_good_one) {
    // Component-level state consistency only. A production run must not treat a
    // backend failure as something to silently retry.
    EvictFixture fixture(100);
    fixture.admit("A", 40);
    fixture.admit("B", 40);

    fixture.backend->failDeleteAtRelative(0);
    const auto failed = fixture.cache->admitReplica("D", makeValueOfSize(90),
                                                    context("11", "preferred"));
    CACHE_REQUIRE(failed.backend_error(), "the first attempt must fail");

    fixture.backend->clearFaults();
    fixture.sink.clear();
    const auto recovered = fixture.cache->admitReplica("D", makeValueOfSize(90),
                                                       context("12", "preferred"));
    CACHE_REQUIRE(recovered.inserted(), "the retry must succeed once the fault is gone");
    CACHE_REQUIRE(recovered.victims.size() == 2, "the retry must evict A and B");

    const auto evictions = fixture.sink.named("replica_evict");
    CACHE_REQUIRE(evictions.size() == 2, "no ghost or duplicated victim event");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evictions.at(0), "key") == "A", "first victim");
    CACHE_REQUIRE(RecordingCacheEventSink::field(evictions.at(1), "key") == "B", "second victim");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 1, "exactly one admit event");

    requireAbsent(*fixture.cache, *fixture.backend, "A", "evicted");
    requireAbsent(*fixture.cache, *fixture.backend, "B", "evicted");
    requireResident(*fixture.cache, *fixture.backend, "D", "admitted");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().size() == 1,
                  "only D may remain serviceable: no ghost directory entries");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.replica_objects == 1, "one resident replica");
    CACHE_REQUIRE(gauge.replica_bytes == 90, "resident bytes must be D's size");
    CACHE_REQUIRE(gauge.admits == 3, "two setup admits plus the successful retry");
    CACHE_REQUIRE(gauge.evictions == 2, "only the two successful evictions");
    CACHE_REQUIRE(gauge.admits - gauge.evictions == gauge.replica_objects, "capacity closure");
}

// ---------------------------------------------------------------------------
// Per-event resident state on replica_evict (contract revision 2): the values
// are the snapshot taken after that victim's successful delete and before the
// new object is inserted -- not the pre-deletion value, and not the final state.
// ---------------------------------------------------------------------------
namespace {

std::size_t numberField(const RecordingCacheEventSink::Event& event,
                        const std::string& name) {
    CACHE_REQUIRE(RecordingCacheEventSink::has(event, name),
                  "event '" + event.name + "' is missing field '" + name + "'");
    return static_cast<std::size_t>(std::stoull(
        RecordingCacheEventSink::field(event, name)));
}

void requireEvictSnapshot(const RecordingCacheEventSink::Event& event,
                          const std::string& key, std::size_t value_bytes,
                          std::size_t resident_bytes, std::size_t resident_objects) {
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "key") == key,
                  "eviction key: expected '" + key + "', got '" +
                      RecordingCacheEventSink::field(event, "key") + "'");
    CACHE_REQUIRE(numberField(event, "value_bytes") == value_bytes,
                  "evict " + key + ": value_bytes must be the victim's own size");
    CACHE_REQUIRE(numberField(event, "resident_bytes") == resident_bytes,
                  "evict " + key + ": resident_bytes must be the post-deletion snapshot");
    CACHE_REQUIRE(numberField(event, "resident_objects") == resident_objects,
                  "evict " + key + ": resident_objects must be the post-deletion snapshot");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "cache_contract_revision") == "4",
                  "evict " + key + ": the resident semantics must be declared");
}

}  // namespace

CACHE_TEST(evict_snapshot_single_victim_success) {
    EvictFixture fixture(100);
    fixture.admit("A", 60);
    fixture.sink.clear();

    const auto result = fixture.cache->admitReplica("B", makeValueOfSize(70),
                                                    context("1", "preferred"));
    CACHE_REQUIRE(result.inserted(), "B must be admitted");
    CACHE_REQUIRE(result.victims.size() == 1, "one victim expected");

    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 1, "one evict event");
    requireEvictSnapshot(fixture.sink.named("replica_evict").at(0), "A", 60, 0, 0);
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 1, "one admit event");
    const auto admit = fixture.sink.named("replica_admit").at(0);
    CACHE_REQUIRE(numberField(admit, "value_bytes") == 70, "admit value_bytes");
    CACHE_REQUIRE(numberField(admit, "resident_bytes") == 70, "admit resident_bytes after insert");
    CACHE_REQUIRE(numberField(admit, "resident_objects") == 1, "admit resident_objects after insert");
    CACHE_REQUIRE(eventIndex(fixture.sink, "replica_evict") <
                      eventIndex(fixture.sink, "replica_admit"),
                  "evict A must precede admit B");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.replica_bytes == 70, "final bytes");
    CACHE_REQUIRE(gauge.replica_objects == 1, "final objects");
}

CACHE_TEST(evict_snapshot_multiple_victims_of_different_sizes) {
    EvictFixture fixture(100);
    // The owner is large on purpose: if it leaked into the replica numbers the
    // assertions below would be obviously wrong.
    fixture.cache->seedOwner("owned", makeValueOfSize(4096));
    fixture.admit("A", 20);
    fixture.admit("B", 30);
    fixture.admit("C", 40);  // 90 bytes / 3 objects, no touches -> LRU order [C, B, A]
    fixture.sink.clear();

    const auto result = fixture.cache->admitReplica("D", makeValueOfSize(80),
                                                    context("2", "preferred"));
    CACHE_REQUIRE(result.inserted(), "D must be admitted");
    CACHE_REQUIRE(result.victims.size() == 3, "three victims expected");

    const auto evictions = fixture.sink.named("replica_evict");
    CACHE_REQUIRE(evictions.size() == 3, "one event per victim");
    requireEvictSnapshot(evictions.at(0), "A", 20, 70, 2);
    requireEvictSnapshot(evictions.at(1), "B", 30, 40, 1);
    requireEvictSnapshot(evictions.at(2), "C", 40, 0, 0);

    const auto admit = fixture.sink.named("replica_admit").at(0);
    CACHE_REQUIRE(numberField(admit, "resident_bytes") == 80, "admit bytes after insert");
    CACHE_REQUIRE(numberField(admit, "resident_objects") == 1, "admit objects after insert");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.replica_bytes == 80, "final bytes");
    CACHE_REQUIRE(gauge.replica_objects == 1, "final objects");
    CACHE_REQUIRE(gauge.owner_bytes == 4096, "owner bytes are accounted separately");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("owned") == 1, "owner entry survives");
}

CACHE_TEST(evict_snapshots_stay_correct_when_the_followup_put_fails) {
    EvictFixture fixture(100);
    fixture.admit("A", 20);
    fixture.admit("B", 30);
    fixture.admit("C", 40);

    fixture.sink.clear();
    fixture.backend->failPutAtRelative(0);  // D's put throws before touching storage
    const auto result = fixture.cache->admitReplica("D", makeValueOfSize(80),
                                                    context("3", "preferred"));
    CACHE_REQUIRE(result.backend_error(), "the put failure must surface");
    CACHE_REQUIRE(result.victims.size() == 3, "all three victims were really deleted");

    const auto evictions = fixture.sink.named("replica_evict");
    CACHE_REQUIRE(evictions.size() == 3, "all three evictions must still be recorded");
    requireEvictSnapshot(evictions.at(0), "A", 20, 70, 2);
    requireEvictSnapshot(evictions.at(1), "B", 30, 40, 1);
    requireEvictSnapshot(evictions.at(2), "C", 40, 0, 0);
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0, "no admit event on failure");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "exactly one final error");
    CACHE_REQUIRE(eventIndex(fixture.sink, "cache_backend_error") >
                      eventIndex(fixture.sink, "replica_evict"),
                  "the final error must come after the evictions");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.replica_bytes == 0, "final bytes");
    CACHE_REQUIRE(gauge.replica_objects == 0, "final objects");
    requireAbsent(*fixture.cache, *fixture.backend, "A", "deleted victim");
    requireAbsent(*fixture.cache, *fixture.backend, "B", "deleted victim");
    requireAbsent(*fixture.cache, *fixture.backend, "C", "deleted victim");
    requireAbsent(*fixture.cache, *fixture.backend, "D", "rejected admission");
}

CACHE_TEST(evict_snapshot_when_a_later_victim_delete_fails) {
    EvictFixture fixture(100);
    fixture.admit("A", 20);
    fixture.admit("B", 30);
    fixture.admit("C", 40);  // eviction order A, B, C

    fixture.sink.clear();
    fixture.backend->failDeleteAtRelative(1);  // A is deleted, B's delete fails
    const auto result = fixture.cache->admitReplica("D", makeValueOfSize(80),
                                                    context("4", "preferred"));
    CACHE_REQUIRE(result.backend_error(), "the failed delete must surface");
    CACHE_REQUIRE(result.victims.size() == 1, "only A was deleted");

    const auto evictions = fixture.sink.named("replica_evict");
    CACHE_REQUIRE(evictions.size() == 1, "only A may have an eviction event");
    requireEvictSnapshot(evictions.at(0), "A", 20, 70, 2);
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0, "no admit event");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1, "one final error");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      fixture.sink.named("cache_backend_error").at(0), "reason") ==
                      "replica_evict_failed",
                  "error reason must identify the failed delete");

    requireAbsent(*fixture.cache, *fixture.backend, "A", "deleted victim");
    requireResident(*fixture.cache, *fixture.backend, "B", "undeleted victim");
    requireResident(*fixture.cache, *fixture.backend, "C", "untouched resident");
    requireAbsent(*fixture.cache, *fixture.backend, "D", "rejected admission");
    CACHE_REQUIRE(!fixture.backend->sawCall("put:replica:D"),
                  "no put may be issued after a failed eviction");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.replica_bytes == 70, "final bytes must be the surviving B+C");
    CACHE_REQUIRE(gauge.replica_objects == 2, "final objects must be the surviving B+C");
}

CACHE_TEST(evict_snapshot_absent_when_no_eviction_is_needed) {
    EvictFixture fixture(100);
    fixture.sink.clear();

    const auto result = fixture.cache->admitReplica("A", makeValueOfSize(40),
                                                    context("5", "preferred"));
    CACHE_REQUIRE(result.inserted(), "A must be admitted");
    CACHE_REQUIRE(result.victims.empty(), "no victims expected");
    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 0,
                  "no eviction event without an eviction");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 1, "one admit event");
    const auto admit = fixture.sink.named("replica_admit").at(0);
    CACHE_REQUIRE(numberField(admit, "resident_bytes") == 40, "admit resident_bytes");
    CACHE_REQUIRE(numberField(admit, "resident_objects") == 1, "admit resident_objects");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.replica_bytes == 40, "final bytes");
    CACHE_REQUIRE(gauge.replica_objects == 1, "final objects");
}

CACHE_TEST(evict_snapshot_not_invented_on_a_zero_budget_bypass) {
    EvictFixture fixture(0);
    fixture.sink.clear();

    const auto result = fixture.cache->admitReplica("A", makeValueOfSize(40),
                                                    context("6", "preferred"));
    CACHE_REQUIRE(result.status == AdmitStatus::BypassedBudgetZero, "A must bypass");
    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 0,
                  "a bypass must not invent an eviction");
    CACHE_REQUIRE(fixture.sink.count("replica_admit") == 0, "a bypass must not admit");
    CACHE_REQUIRE(fixture.sink.count("replica_bypass") == 1, "exactly one bypass event");
    // bypass/skip events report the unchanged resident state, never a snapshot.
    const auto bypass = fixture.sink.named("replica_bypass").at(0);
    CACHE_REQUIRE(numberField(bypass, "resident_bytes") == 0, "bypass reports the current state");
    CACHE_REQUIRE(numberField(bypass, "resident_objects") == 0, "bypass reports the current state");

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.replica_bytes == 0, "no bytes");
    CACHE_REQUIRE(gauge.replica_objects == 0, "no objects");
}

CACHE_TEST(admit_and_evict_event_state_replays_to_the_final_gauge) {
    // Scope: an empty replica cache, no external deletes and no KV/index
    // divergence repair, and injected backend faults that throw BEFORE their
    // side effect. This says nothing about real Redis response uncertainty.
    const std::size_t budget = 100;
    EvictFixture fixture(budget);

    std::size_t expected_bytes = 0;
    std::size_t expected_objects = 0;
    const auto check_events = [&](std::size_t from_index) {
        const auto& events = fixture.sink.events();
        for (std::size_t i = from_index; i < events.size(); ++i) {
            const auto& event = events.at(i);
            if (event.name == "replica_admit") {
                expected_bytes += numberField(event, "value_bytes");
                expected_objects += 1;
            } else if (event.name == "replica_evict") {
                const std::size_t victim_bytes = numberField(event, "value_bytes");
                CACHE_REQUIRE(expected_bytes >= victim_bytes,
                              "replayed resident bytes would go negative");
                CACHE_REQUIRE(expected_objects >= 1,
                              "replayed object count would go negative");
                expected_bytes -= victim_bytes;
                expected_objects -= 1;
            } else {
                continue;  // bypass / skip / conflict / backend error
            }
            CACHE_REQUIRE(numberField(event, "resident_bytes") == expected_bytes,
                          event.name + ": resident_bytes disagrees with the replayed state");
            CACHE_REQUIRE(numberField(event, "resident_objects") == expected_objects,
                          event.name + ": resident_objects disagrees with the replayed state");
            CACHE_REQUIRE(expected_bytes <= budget,
                          "replayed resident bytes exceeded the budget");
        }
        return events.size();
    };

    std::size_t cursor = 0;
    fixture.cache->admitReplica("A", makeValueOfSize(20), context("1", "preferred"));
    cursor = check_events(cursor);
    fixture.cache->admitReplica("B", makeValueOfSize(30), context("2", "preferred"));
    cursor = check_events(cursor);
    fixture.cache->admitReplica("C", makeValueOfSize(40), context("3", "preferred"));
    cursor = check_events(cursor);
    CACHE_REQUIRE(expected_bytes == 90 && expected_objects == 3,
                  "state before the multi-victim phase");

    // Multi-victim eviction followed by a deterministic put failure.
    fixture.backend->failPutAtRelative(0);
    fixture.cache->admitReplica("D", makeValueOfSize(80), context("4", "preferred"));
    cursor = check_events(cursor);
    CACHE_REQUIRE(expected_bytes == 0 && expected_objects == 0,
                  "state after the failed insert");

    // Recovery: a normal admission, then one that evicts again.
    fixture.backend->clearFaults();
    fixture.cache->admitReplica("E", makeValueOfSize(80), context("5", "preferred"));
    cursor = check_events(cursor);
    fixture.cache->admitReplica("F", makeValueOfSize(30), context("6", "preferred"));
    check_events(cursor);

    const auto gauge = fixture.cache->gauge();
    CACHE_REQUIRE(gauge.replica_bytes == expected_bytes,
                  "final gauge bytes must equal the replayed state");
    CACHE_REQUIRE(gauge.replica_objects == expected_objects,
                  "final gauge objects must equal the replayed state");
    CACHE_REQUIRE(gauge.replica_bytes <= budget, "final bytes must respect the budget");
    CACHE_REQUIRE(fixture.sink.count("replica_evict") == 4,
                  "the whole sequence evicts A, B, C and E");
}

// ---------------------------------------------------------------------------
// Immutable-object integrity (finite-cache modes). A local owner object or
// resident replica is a trustworthy reference; anything else is a declared
// boundary, not a guarantee.
// ---------------------------------------------------------------------------
namespace {

struct IntegrityFixture {
    RecordingCacheEventSink sink;
    std::unique_ptr<cache_test::FaultyCache> backend_owner;
    cache_test::FaultyCache* backend = nullptr;
    std::unique_ptr<NodeCache> cache;

    IntegrityFixture(CachePolicy policy, std::size_t budget) {
        backend_owner = std::make_unique<cache_test::FaultyCache>(0);
        backend = backend_owner.get();
        cache = std::make_unique<NodeCache>(std::move(backend_owner),
                                            policyConfig(policy, budget), &sink);
    }
};

struct ConflictCapture {
    bool threw = false;
    std::string reason;
    std::string tier;
    std::size_t incoming = 0;
    std::size_t existing = 0;
};

ConflictCapture expectConflict(NodeCache& cache, const std::string& key, const Value& value) {
    ConflictCapture capture;
    try {
        (void)cache.checkImmutability(key, value, context("1", "preferred"));
    } catch (const rsync_cache::ImmutabilityViolation& e) {
        capture.threw = true;
        capture.reason = e.reason();
        capture.tier = e.tier();
        capture.incoming = e.incomingBytes();
        capture.existing = e.existingBytes();
    }
    return capture;
}

void requireConflictEvent(const RecordingCacheEventSink& sink, const std::string& key,
                          const std::string& tier, const std::string& reason,
                          std::size_t incoming, std::size_t existing) {
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 1, "exactly one cache_key_conflict");
    const auto event = sink.named("cache_key_conflict").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "key") == key, "conflict key");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "tier") == tier, "conflict tier");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "reason") == reason, "conflict reason");
    CACHE_REQUIRE(numberField(event, "incoming_bytes") == incoming, "incoming_bytes");
    CACHE_REQUIRE(numberField(event, "existing_bytes") == existing, "existing_bytes");
    CACHE_REQUIRE(RecordingCacheEventSink::field(event, "cache_contract_revision") == "4",
                  "conflict event must declare the contract revision");
    CACHE_REQUIRE(sink.count("replica_admit") == 0, "a conflict must not admit");
    CACHE_REQUIRE(sink.count("data_admit_skipped") == 0, "a conflict must not look like a skip");
}

}  // namespace

CACHE_TEST(immutability_rejects_a_replica_size_conflict) {
    IntegrityFixture fixture(CachePolicy::Lru, 1000);
    fixture.cache->admitReplica("k", makeValueOfSize(100, 'a'), context("0", "preferred"));
    const auto before = fixture.cache->gauge();
    fixture.sink.clear();

    const auto conflict = expectConflict(*fixture.cache, "k", makeValueOfSize(90, 'a'));
    CACHE_REQUIRE(conflict.threw, "a size conflict must throw ImmutabilityViolation");
    CACHE_REQUIRE(conflict.reason == "size_mismatch", "thrown reason");
    CACHE_REQUIRE(conflict.tier == "replica", "thrown tier");
    CACHE_REQUIRE(conflict.incoming == 90 && conflict.existing == 100, "thrown sizes");
    requireConflictEvent(fixture.sink, "k", "replica", "size_mismatch", 90, 100);

    const auto after = fixture.cache->gauge();
    CACHE_REQUIRE(after.replica_bytes == before.replica_bytes &&
                      after.replica_objects == before.replica_objects,
                  "a conflict must not change residency");
    CACHE_REQUIRE(after.admits == before.admits && after.evictions == before.evictions,
                  "a conflict must not admit or evict");
    CACHE_REQUIRE(after.touches == before.touches, "a conflict must not touch the LRU");
    requireResident(*fixture.cache, *fixture.backend, "k", "after the rejected size conflict");
}

CACHE_TEST(immutability_rejects_a_replica_content_conflict) {
    IntegrityFixture fixture(CachePolicy::Lru, 1000);
    fixture.cache->admitReplica("k", makeValueOfSize(100, 'a'), context("0", "preferred"));
    const auto before = fixture.cache->gauge();
    fixture.sink.clear();

    const auto conflict = expectConflict(*fixture.cache, "k", makeValueOfSize(100, 'b'));
    CACHE_REQUIRE(conflict.threw, "a same-size content conflict must throw");
    CACHE_REQUIRE(conflict.reason == "content_mismatch", "thrown reason");
    requireConflictEvent(fixture.sink, "k", "replica", "content_mismatch", 100, 100);

    const auto after = fixture.cache->gauge();
    CACHE_REQUIRE(after.replica_bytes == before.replica_bytes, "resident bytes unchanged");
    CACHE_REQUIRE(after.touches == before.touches, "conflict must not touch the LRU");
    const auto stored = fixture.cache->lookupForService("k");
    CACHE_REQUIRE(stored.value.has_value(), "the replica must still be servable");
    CACHE_REQUIRE(std::string(stored.value->data().begin(), stored.value->data().end()) ==
                      std::string(100, 'a'),
                  "the resident value must not be overwritten by the conflicting bytes");
}

CACHE_TEST(immutability_accepts_an_identical_duplicate_without_touching) {
    IntegrityFixture fixture(CachePolicy::Lru, 1000);
    fixture.cache->admitReplica("k", makeValueOfSize(100, 'a'), context("0", "preferred"));
    fixture.sink.clear();
    const auto before = fixture.cache->gauge();

    const auto check = fixture.cache->checkImmutability("k", makeValueOfSize(100, 'a'),
                                                       context("1", "preferred"));
    CACHE_REQUIRE(check.verdict == rsync_cache::IntegrityVerdict::Verified,
                  "identical bytes must verify");
    CACHE_REQUIRE(check.reason.empty(), "a verified duplicate has no conflict reason");
    CACHE_REQUIRE(fixture.sink.events().empty(), "an identical duplicate emits no conflict event");
    const auto after = fixture.cache->gauge();
    CACHE_REQUIRE(after.touches == before.touches, "verification must not touch the LRU");
    CACHE_REQUIRE(after.serves == before.serves, "verification is not a service read");
    CACHE_REQUIRE(after.replica_bytes == before.replica_bytes, "resident bytes unchanged");
}

CACHE_TEST(immutability_protects_owner_content_and_creates_no_replica) {
    IntegrityFixture fixture(CachePolicy::Lru, 1000);
    fixture.cache->seedOwner("owned", makeValueOfSize(100, 'a'));

    CACHE_REQUIRE(expectConflict(*fixture.cache, "owned", makeValueOfSize(90, 'a')).threw,
                  "an owner size conflict must throw");
    requireConflictEvent(fixture.sink, "owned", "owner", "size_mismatch", 90, 100);
    fixture.sink.clear();
    CACHE_REQUIRE(expectConflict(*fixture.cache, "owned", makeValueOfSize(100, 'z')).threw,
                  "an owner content conflict must throw");
    requireConflictEvent(fixture.sink, "owned", "owner", "content_mismatch", 100, 100);

    // The owner value is untouched and no redundant replica was created.
    const auto owner = fixture.cache->lookupForService("owned");
    CACHE_REQUIRE(owner.value.has_value() && owner.tier == CacheTier::Owner, "owner still readable");
    CACHE_REQUIRE(std::string(owner.value->data().begin(), owner.value->data().end()) ==
                      std::string(100, 'a'),
                  "the owner value must be unchanged");
    CACHE_REQUIRE(fixture.cache->gauge().replica_objects == 0, "no replica may be created");
    CACHE_REQUIRE(!fixture.backend->hasPhysical("replica:owned"),
                  "an owner key must not be written to the replica namespace");

    // Same content is not a conflict and still creates no replica.
    fixture.sink.clear();
    const auto check = fixture.cache->checkImmutability("owned", makeValueOfSize(100, 'a'),
                                                       context("2", "preferred"));
    CACHE_REQUIRE(check.verdict == rsync_cache::IntegrityVerdict::Verified,
                  "identical owner content must verify");
    CACHE_REQUIRE(fixture.sink.events().empty(), "identical owner content emits nothing");
    CACHE_REQUIRE(fixture.cache->gauge().replica_objects == 0, "still no replica");
}

CACHE_TEST(immutability_applies_to_unlimited_and_bypass) {
    IntegrityFixture unlimited(CachePolicy::Unlimited, 0);
    unlimited.cache->admitReplica("k", makeValueOfSize(50, 'a'), context("0", "preferred"));
    unlimited.sink.clear();
    CACHE_REQUIRE(expectConflict(*unlimited.cache, "k", makeValueOfSize(50, 'b')).threw,
                  "unlimited must reject a content conflict");
    requireConflictEvent(unlimited.sink, "k", "replica", "content_mismatch", 50, 50);

    IntegrityFixture bypass(CachePolicy::Bypass, 0);
    bypass.cache->seedOwner("owned", makeValueOfSize(50, 'a'));
    CACHE_REQUIRE(expectConflict(*bypass.cache, "owned", makeValueOfSize(50, 'b')).threw,
                  "bypass must still protect owner content");
    requireConflictEvent(bypass.sink, "owned", "owner", "content_mismatch", 50, 50);

    // Declared boundary: with no owner and no replica there is nothing to
    // compare against, so a first copy is accepted (and not called "verified").
    bypass.sink.clear();
    const auto first_copy = bypass.cache->checkImmutability("fresh", makeValueOfSize(50, 'b'),
                                                           context("1", "preferred"));
    CACHE_REQUIRE(first_copy.verdict == rsync_cache::IntegrityVerdict::NotApplicable,
                  "a first copy cannot be locally verified");
    CACHE_REQUIRE(bypass.sink.events().empty(), "a first copy is not a conflict");
    const auto bypassed = bypass.cache->admitReplica("fresh", makeValueOfSize(50, 'b'),
                                                     context("1", "preferred"));
    CACHE_REQUIRE(bypassed.status == AdmitStatus::BypassedBudgetZero, "bypass still bypasses");
    CACHE_REQUIRE(bypass.cache->gauge().replica_objects == 0, "bypass stores no replica");
}

CACHE_TEST(immutability_read_failure_is_not_a_match) {
    IntegrityFixture fixture(CachePolicy::Lru, 1000);
    fixture.cache->admitReplica("k", makeValueOfSize(100, 'a'), context("0", "preferred"));
    fixture.sink.clear();
    fixture.backend->failGets();

    const auto check = fixture.cache->checkImmutability("k", makeValueOfSize(100, 'b'),
                                                       context("1", "preferred"));
    CACHE_REQUIRE(check.verdict == rsync_cache::IntegrityVerdict::Unavailable,
                  "an unreadable reference must not be reported as verified");
    CACHE_REQUIRE(check.reason == "read_failed", "read failure reason");
    CACHE_REQUIRE(fixture.sink.count("cache_key_conflict") == 0,
                  "a read failure is not a content conflict");
    CACHE_REQUIRE(fixture.sink.count("cache_backend_error") == 1,
                  "a read failure must be recorded as a backend error");
    CACHE_REQUIRE(RecordingCacheEventSink::field(
                      fixture.sink.named("cache_backend_error").at(0), "reason") ==
                      "replica_get_failed",
                  "backend error reason");
    CACHE_REQUIRE(fixture.cache->gauge().touches == 0, "a failed read must not touch the LRU");
}

CACHE_TEST(immutability_conflict_leaves_budget_directory_and_lru_order_unchanged) {
    // budget 100, A=40 then B=40 -> LRU order [B, A]; A is the true LRU victim.
    IntegrityFixture fixture(CachePolicy::Lru, 100);
    fixture.cache->admitReplica("A", makeValueOfSize(40, 'a'), context("0", "preferred"));
    fixture.cache->admitReplica("B", makeValueOfSize(40, 'b'), context("0", "preferred"));
    const auto before = fixture.cache->gauge();
    fixture.sink.clear();

    CACHE_REQUIRE(expectConflict(*fixture.cache, "A", makeValueOfSize(40, 'z')).threw,
                  "a content conflict on A must throw");
    CACHE_REQUIRE(expectConflict(*fixture.cache, "A", makeValueOfSize(10, 'a')).threw,
                  "a size conflict on A must throw");
    const auto after = fixture.cache->gauge();
    CACHE_REQUIRE(after.replica_bytes == before.replica_bytes, "budget accounting unchanged");
    CACHE_REQUIRE(after.replica_objects == before.replica_objects, "object accounting unchanged");
    CACHE_REQUIRE(after.evictions == before.evictions, "no eviction may be triggered");
    CACHE_REQUIRE(fixture.cache->serviceableKeys().count("A") == 1 &&
                      fixture.cache->serviceableKeys().count("B") == 1,
                  "directory unchanged");

    // The LRU order is unchanged too: the next admission still evicts A.
    fixture.sink.clear();
    const auto third = fixture.cache->admitReplica("C", makeValueOfSize(30, 'c'),
                                                   context("2", "preferred"));
    CACHE_REQUIRE(third.inserted(), "C must be admitted");
    CACHE_REQUIRE(third.victims.size() == 1 && third.victims.at(0).key == "A",
                  "the rejected verifications must not have changed the LRU order");
}

CACHE_TEST(an_evicted_replica_cannot_be_verified_against_history) {
    // Declared boundary D: no fingerprint is retained, so a conflict with a
    // since-evicted copy cannot be detected. The check must report
    // NotApplicable rather than pretend it verified something.
    IntegrityFixture fixture(CachePolicy::Lru, 40);
    fixture.cache->admitReplica("A", makeValueOfSize(40, 'a'), context("0", "preferred"));
    fixture.cache->admitReplica("B", makeValueOfSize(40, 'b'), context("0", "preferred"));
    CACHE_REQUIRE(fixture.cache->gauge().evictions == 1, "A must have been evicted");
    CACHE_REQUIRE(fixture.cache->tierOf("A") == CacheTier::Miss, "A is not resident");
    fixture.sink.clear();

    const auto check = fixture.cache->checkImmutability("A", makeValueOfSize(40, 'z'),
                                                       context("1", "preferred"));
    CACHE_REQUIRE(check.verdict == rsync_cache::IntegrityVerdict::NotApplicable,
                  "an evicted replica has no retained fingerprint");
    CACHE_REQUIRE(fixture.sink.count("cache_key_conflict") == 0,
                  "no conflict can be claimed without a reference");
}

CACHE_TEST(integrity_verification_cannot_be_disabled_in_new_modes) {
    clearCacheEnv();
    setenv("RSYNC_CACHE_POLICY", "lru", 1);
    setenv("RSYNC_REPLICA_BUDGET_BYTES", "1024", 1);
    setenv("RSYNC_CACHE_VERIFY_REPLICA_CONTENT", "0", 1);
    bool threw = false;
    try {
        (void)CachePolicyConfig::fromEnv();
    } catch (const std::runtime_error&) {
        threw = true;
    }
    CACHE_REQUIRE(threw,
                  "explicitly disabling verification in a finite-cache mode must fail startup");

    setenv("RSYNC_CACHE_VERIFY_REPLICA_CONTENT", "1", 1);
    CACHE_REQUIRE(CachePolicyConfig::fromEnv().verify_replica_content,
                  "verification is on and stays on in finite-cache modes");

    unsetenv("RSYNC_CACHE_VERIFY_REPLICA_CONTENT");
    CACHE_REQUIRE(CachePolicyConfig::fromEnv().verify_replica_content,
                  "verification defaults on in finite-cache modes");

    // legacy keeps its historical flag meaning (its compatibility fix is next).
    setenv("RSYNC_CACHE_POLICY", "legacy", 1);
    unsetenv("RSYNC_REPLICA_BUDGET_BYTES");
    setenv("RSYNC_CACHE_VERIFY_REPLICA_CONTENT", "0", 1);
    CACHE_REQUIRE(!CachePolicyConfig::fromEnv().verify_replica_content,
                  "the legacy flag meaning must not change");
    clearCacheEnv();
}

int main() {
    return cache_test::runAll("node_cache_events");
}