// Deterministic tests for the owner/replica key namespaces and the OwnerStore.
//
// Covers the design requirements:
//   A.1 physical key encoding cannot collide
//   A.2 protocol/schedule keep logical keys (asserted by round-trip)
//   A.3 owner objects are not part of the replica budget (see test_replica_cache)
//   A.4 owner objects cannot be overwritten after initialization
//   A.5 owner entries cannot be removed through the replica delete path

#include "cache_test_support.hpp"

#include "cache/key_namespace.hpp"
#include "cache/node_cache.hpp"
#include "cache/owner_store.hpp"

#include <memory>
#include <set>
#include <string>
#include <vector>

using cache_test::makeValue;
using cache_test::RecordingCacheEventSink;
using rsync_cache::CacheNamespace;
using rsync_cache::CachePolicy;
using rsync_cache::CachePolicyConfig;
using rsync_cache::logicalKeyFromPhysical;
using rsync_cache::NodeCache;
using rsync_cache::OwnerStore;
using rsync_cache::physicalKey;

namespace {

std::vector<std::string> probeKeys() {
    return {
        "",
        "a",
        "b",
        "aa",
        "owner:a",
        "replica:a",
        "owner:",
        "replica:",
        "owner:owner:a",
        "replica:replica:a",
        "owner:replica:a",
        "replica:owner:a",
        "latency_v4_object_0001",
        std::string(300, 'k'),
        "a:b:c",
        ":owner:x",
    };
}

CachePolicyConfig lruBudget(std::size_t bytes) {
    CachePolicyConfig config;
    config.policy = CachePolicy::Lru;
    config.budget_bytes = bytes;
    return config;
}

}  // namespace

CACHE_TEST(owner_namespace_is_injective) {
    for (const auto& a : probeKeys()) {
        for (const auto& b : probeKeys()) {
            const bool same = physicalKey(CacheNamespace::Owner, a) ==
                              physicalKey(CacheNamespace::Owner, b);
            CACHE_REQUIRE(same == (a == b),
                          "owner namespace encoding is not injective for '" + a +
                              "' vs '" + b + "'");
        }
    }
}

CACHE_TEST(replica_namespace_is_injective) {
    for (const auto& a : probeKeys()) {
        for (const auto& b : probeKeys()) {
            const bool same = physicalKey(CacheNamespace::Replica, a) ==
                              physicalKey(CacheNamespace::Replica, b);
            CACHE_REQUIRE(same == (a == b),
                          "replica namespace encoding is not injective for '" + a +
                              "' vs '" + b + "'");
        }
    }
}

CACHE_TEST(owner_and_replica_namespaces_never_collide) {
    std::set<std::string> owner_physical;
    std::set<std::string> replica_physical;
    for (const auto& key : probeKeys()) {
        owner_physical.insert(physicalKey(CacheNamespace::Owner, key));
        replica_physical.insert(physicalKey(CacheNamespace::Replica, key));
    }
    for (const auto& key : owner_physical) {
        CACHE_REQUIRE(replica_physical.count(key) == 0,
                      "physical key collision between namespaces: '" + key + "'");
    }
    // A logical key that already carries the other namespace's prefix must not
    // alias either: owner("replica:a") != replica("a") and vice versa.
    CACHE_REQUIRE(physicalKey(CacheNamespace::Owner, "replica:a") !=
                      physicalKey(CacheNamespace::Replica, "a"),
                  "owner('replica:a') aliases replica('a')");
    CACHE_REQUIRE(physicalKey(CacheNamespace::Replica, "owner:a") !=
                      physicalKey(CacheNamespace::Owner, "a"),
                  "replica('owner:a') aliases owner('a')");
}

CACHE_TEST(physical_key_round_trip_preserves_logical_keys) {
    for (const auto& key : probeKeys()) {
        for (const auto ns : {CacheNamespace::Owner, CacheNamespace::Replica}) {
            std::string decoded;
            CACHE_REQUIRE(logicalKeyFromPhysical(ns, physicalKey(ns, key), decoded),
                          "round trip rejected a key it encoded");
            CACHE_REQUIRE(decoded == key, "round trip changed logical key '" + key + "'");
        }
    }
    std::string decoded;
    CACHE_REQUIRE(!logicalKeyFromPhysical(CacheNamespace::Owner, "replica:x", decoded),
                  "owner decode accepted a replica physical key");
    CACHE_REQUIRE(!logicalKeyFromPhysical(CacheNamespace::Replica, "owner:x", decoded),
                  "replica decode accepted an owner physical key");
}

CACHE_TEST(legacy_namespace_keeps_bare_keys) {
    CACHE_REQUIRE(physicalKey(CacheNamespace::Legacy, "latency_v4_object_0001") ==
                      "latency_v4_object_0001",
                  "legacy namespace must not prefix keys");
    std::string decoded;
    CACHE_REQUIRE(logicalKeyFromPhysical(CacheNamespace::Legacy, "owner:x", decoded),
                  "legacy decode must accept any key");
    CACHE_REQUIRE(decoded == "owner:x", "legacy decode changed the key");
}

CACHE_TEST(owner_store_reads_back_and_seals) {
    auto backend = std::make_unique<cache_test::FaultyCache>(0);
    cache_test::FaultyCache* raw = backend.get();
    OwnerStore store(raw, /*namespaced=*/true);
    store.seed("k1", makeValue("v1"));
    store.seed("k2", makeValue("v2"));

    CACHE_REQUIRE(store.contains("k1"), "seeded key not in the owner index");
    CACHE_REQUIRE(store.objectCount() == 2, "owner object count wrong");
    CACHE_REQUIRE(store.valueBytes() == 4, "owner byte accounting wrong");
    const auto value = store.get("k1");
    CACHE_REQUIRE(value.has_value(), "owner get returned nothing");
    CACHE_REQUIRE(cache_test::valueString(*value) == "v1", "owner value changed");
    CACHE_REQUIRE(!store.get("missing").has_value(), "owner get invented a value");
    CACHE_REQUIRE(raw->hasPhysical(physicalKey(CacheNamespace::Owner, "k1")),
                  "owner object was not written under the owner namespace");
    CACHE_REQUIRE(!raw->hasPhysical("k1"),
                  "owner object leaked into the bare (legacy) key space");

    store.seal();
    bool threw = false;
    try {
        store.seed("k3", makeValue("v3"));
    } catch (const std::logic_error&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "seed() after seal() must throw");
}

CACHE_TEST(owner_store_rejects_content_conflict) {
    auto backend = std::make_unique<cache_test::FaultyCache>(0);
    cache_test::FaultyCache* raw = backend.get();
    RecordingCacheEventSink sink;
    OwnerStore store(raw, /*namespaced=*/true, &sink);
    store.seed("k", makeValue("same-size-1"));

    bool threw = false;
    try {
        store.seed("k", makeValue("same-size-2"));  // same size, different content
    } catch (const std::runtime_error&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "identical-size content conflict must be rejected");
    CACHE_REQUIRE(sink.count("cache_key_conflict") == 1,
                  "content conflict must emit exactly one cache_key_conflict");
    const auto conflict = sink.named("cache_key_conflict").at(0);
    CACHE_REQUIRE(RecordingCacheEventSink::field(conflict, "reason") ==
                      "owner_content_mismatch",
                  "conflict reason wrong");
    CACHE_REQUIRE(raw->hasPhysical(physicalKey(CacheNamespace::Owner, "k")),
                  "the original owner object must survive a rejected re-seed");

    threw = false;
    try {
        store.seed("k", makeValue("longer-value-x"));  // different size
    } catch (const std::runtime_error&) {
        threw = true;
    }
    CACHE_REQUIRE(threw, "size conflict must be rejected");
    CACHE_REQUIRE(store.objectCount() == 1, "conflict must not add an owner object");
    CACHE_REQUIRE(store.valueBytes() == 11, "conflict must not change owner bytes");
}

CACHE_TEST(owner_store_identical_reseed_is_idempotent) {
    auto backend = std::make_unique<cache_test::FaultyCache>(0);
    OwnerStore store(backend.get(), /*namespaced=*/true);
    store.seed("k", makeValue("v"));
    store.seed("k", makeValue("v"));
    CACHE_REQUIRE(store.objectCount() == 1, "idempotent re-seed double counted");
    CACHE_REQUIRE(store.valueBytes() == 1, "idempotent re-seed double counted bytes");
}

CACHE_TEST(owner_objects_are_protected_from_the_replica_path) {
    auto backend = std::make_unique<cache_test::FaultyCache>(0);
    RecordingCacheEventSink sink;
    // Budget for exactly one object: the replica LRU wants to evict on the
    // second admission, but the resident object is an owner and must survive.
    NodeCache store(std::move(backend), lruBudget(65536), &sink);
    store.seedOwner("owned", cache_test::makeValueOfSize(65536));

    // Admit a replica, then force evictions with a second replica admission.
    const auto first = store.admitReplica("rep1", cache_test::makeValueOfSize(65536));
    CACHE_REQUIRE(first.inserted(), "first replica was not admitted");
    const auto second = store.admitReplica("rep2", cache_test::makeValueOfSize(65536));
    CACHE_REQUIRE(second.inserted(), "second replica was not admitted");
    CACHE_REQUIRE(second.victims.size() == 1, "expected exactly one eviction");
    CACHE_REQUIRE(second.victims.at(0).key == "rep1", "LRU victim should be rep1");

    CACHE_REQUIRE(store.isOwner("owned"), "owner must still be present");
    const auto owner_value = store.lookupForService("owned");
    CACHE_REQUIRE(owner_value.value.has_value(), "owner value disappeared");
    CACHE_REQUIRE(owner_value.tier == rsync_cache::CacheTier::Owner, "owner tier lost");
    CACHE_REQUIRE(store.serviceableKeys().count("owned") == 1,
                  "owner entry was removed from the serviceable directory");
    CACHE_REQUIRE(store.serviceableKeys().count("rep1") == 0,
                  "evicted replica is still advertised as serviceable");
    CACHE_REQUIRE(store.serviceableKeys().count("rep2") == 1,
                  "resident replica is missing from the serviceable directory");
}

int main() {
    return cache_test::runAll("cache_namespace");
}