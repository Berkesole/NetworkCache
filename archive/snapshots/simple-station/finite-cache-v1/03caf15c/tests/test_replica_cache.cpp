// Deterministic byte-budget LRU tests for ReplicaCache.
//
// Every eviction decision here is hand-computable from the access stream, so a
// failure points at the LRU semantics rather than at the experiment harness.

#include "cache_test_support.hpp"

#include "cache/replica_cache.hpp"

#include <memory>
#include <string>
#include <vector>

using cache_test::makeValueOfSize;
using rsync_cache::AdmitStatus;
using rsync_cache::ReplicaCache;
using rsync_cache::ReplicaPolicy;

namespace {

struct Fixture {
    cache_test::FaultyCache* backend = nullptr;
    std::unique_ptr<ReplicaCache> cache;

    explicit Fixture(ReplicaPolicy policy, std::size_t budget,
                     std::size_t fail_after_successes = 0) {
        auto owned = std::make_unique<cache_test::FaultyCache>(fail_after_successes);
        backend = owned.get();
        backend_owner = std::move(owned);
        cache = std::make_unique<ReplicaCache>(backend, policy, budget, true, false);
    }

private:
    std::unique_ptr<cache_test::FaultyCache> backend_owner;
};

std::vector<std::string> keys(const std::vector<rsync_cache::ReplicaVictim>& victims) {
    std::vector<std::string> out;
    for (const auto& victim : victims) out.push_back(victim.key);
    return out;
}

}  // namespace

CACHE_TEST(budget_zero_bypasses_every_admission) {
    Fixture fixture(ReplicaPolicy::Lru, 0);
    for (int i = 0; i < 5; ++i) {
        const auto result = fixture.cache->admit("k" + std::to_string(i),
                                                 makeValueOfSize(65536));
        CACHE_REQUIRE(result.status == AdmitStatus::BypassedBudgetZero,
                      "budget 0 must bypass every admission");
        CACHE_REQUIRE(result.victims.empty(), "budget 0 must not evict anything");
    }
    CACHE_REQUIRE(fixture.cache->residentObjects() == 0, "budget 0 kept an object");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 0, "budget 0 accounted bytes");
    CACHE_REQUIRE(fixture.cache->evictions() == 0, "budget 0 recorded an eviction");
    CACHE_REQUIRE(fixture.cache->bypassesBudgetZero() == 5, "bypass counter wrong");
}

CACHE_TEST(policy_bypass_never_admits) {
    Fixture fixture(ReplicaPolicy::Bypass, 1024 * 1024);
    const auto result = fixture.cache->admit("k", makeValueOfSize(65536));
    CACHE_REQUIRE(result.status == AdmitStatus::BypassedBudgetZero,
                  "policy bypass must not admit");
    CACHE_REQUIRE(fixture.cache->residentObjects() == 0, "policy bypass kept an object");
}

CACHE_TEST(budget_smaller_than_one_object_bypasses) {
    Fixture fixture(ReplicaPolicy::Lru, 10);
    const auto result = fixture.cache->admit("big", makeValueOfSize(100));
    CACHE_REQUIRE(result.status == AdmitStatus::BypassedTooLarge,
                  "an object larger than the budget must bypass");
    CACHE_REQUIRE(result.victims.empty(), "oversized bypass must not evict");
    CACHE_REQUIRE(fixture.cache->residentObjects() == 0, "oversized object was stored");
    CACHE_REQUIRE(fixture.cache->bypassesTooLarge() == 1, "too-large counter wrong");
}

CACHE_TEST(budget_exactly_one_object_evicts_on_the_second) {
    Fixture fixture(ReplicaPolicy::Lru, 65536);
    CACHE_REQUIRE(fixture.cache->admit("a", makeValueOfSize(65536)).inserted(),
                  "first object must fit exactly");
    const auto second = fixture.cache->admit("b", makeValueOfSize(65536));
    CACHE_REQUIRE(second.inserted(), "second object must be admitted after eviction");
    CACHE_REQUIRE(keys(second.victims) == std::vector<std::string>{"a"},
                  "exactly-one-object budget must evict the only resident object");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 65536, "resident bytes wrong");
    CACHE_REQUIRE(fixture.cache->residentObjects() == 1, "resident objects wrong");
    CACHE_REQUIRE(fixture.cache->evictions() == 1, "eviction counter wrong");
    CACHE_REQUIRE(fixture.cache->evictedBytes() == 65536, "evicted bytes wrong");
    CACHE_REQUIRE(!fixture.cache->resident("a"), "evicted key still resident");
    CACHE_REQUIRE(fixture.cache->resident("b"), "admitted key not resident");
    CACHE_REQUIRE(!fixture.backend->hasPhysical(fixture.cache->physicalKeyFor("a")),
                  "evicted key still present in the backend");
}

CACHE_TEST(byte_accounting_is_by_real_value_length) {
    Fixture fixture(ReplicaPolicy::Lru, 100);
    CACHE_REQUIRE(fixture.cache->admit("a", makeValueOfSize(60)).inserted(), "a");
    CACHE_REQUIRE(fixture.cache->admit("b", makeValueOfSize(30)).inserted(), "b");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 90, "resident bytes after a+b");

    const auto third = fixture.cache->admit("c", makeValueOfSize(20));
    CACHE_REQUIRE(third.inserted(), "c must be admitted after evicting a");
    CACHE_REQUIRE(keys(third.victims) == std::vector<std::string>{"a"},
                  "a is the LRU victim (60+30+20 > 100)");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 50, "resident bytes after b+c");
    CACHE_REQUIRE(fixture.cache->residentKeysLruOrder() ==
                      std::vector<std::string>({"c", "b"}),
                  "LRU order after the third admission");
}

CACHE_TEST(hand_computed_lru_sequence) {
    Fixture fixture(ReplicaPolicy::Lru, 100);
    fixture.cache->admit("a", makeValueOfSize(60));
    fixture.cache->admit("b", makeValueOfSize(30));
    fixture.cache->admit("c", makeValueOfSize(20));  // evicts a -> {c,b}
    CACHE_REQUIRE(fixture.cache->residentKeysLruOrder() ==
                      std::vector<std::string>({"c", "b"}),
                  "state after the first three admissions");

    const auto used = fixture.cache->use("b");  // refresh b -> {b,c}
    CACHE_REQUIRE(used.value.has_value(), "use(b) must serve its value");
    CACHE_REQUIRE(used.touched, "a real service must refresh recency");
    CACHE_REQUIRE(fixture.cache->residentKeysLruOrder() ==
                      std::vector<std::string>({"b", "c"}),
                  "use(b) must move b to MRU");

    fixture.cache->admit("d", makeValueOfSize(10));  // 50+10 <= 100, no eviction
    CACHE_REQUIRE(fixture.cache->residentObjects() == 3, "d must not evict");
    CACHE_REQUIRE(fixture.cache->residentKeysLruOrder() ==
                      std::vector<std::string>({"d", "b", "c"}),
                  "order after admitting d");

    const auto e = fixture.cache->admit("e", makeValueOfSize(90));
    CACHE_REQUIRE(e.inserted(), "e must fit after evicting c and b");
    CACHE_REQUIRE(keys(e.victims) == std::vector<std::string>({"c", "b"}),
                  "90 bytes free after dropping c(20)+b(30)");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 100, "resident bytes must equal budget");
    CACHE_REQUIRE(fixture.cache->residentKeysLruOrder() ==
                      std::vector<std::string>({"e", "d"}),
                  "final order");
}

CACHE_TEST(exists_and_diagnostics_do_not_touch) {
    Fixture fixture(ReplicaPolicy::Lru, 2 * 65536);
    fixture.cache->admit("a", makeValueOfSize(65536));
    fixture.cache->admit("b", makeValueOfSize(65536));
    // Hammer the non-touching probes: a is still the LRU victim afterwards.
    for (int i = 0; i < 10; ++i) {
        CACHE_REQUIRE(fixture.cache->resident("a"), "resident(a)");
        CACHE_REQUIRE(fixture.cache->physicalExists("a"), "physicalExists(a)");
        (void)fixture.cache->residentKeysLruOrder();
        (void)fixture.cache->residentBytes();
    }
    CACHE_REQUIRE(fixture.cache->touches() == 0, "diagnostics must not touch");
    const auto third = fixture.cache->admit("c", makeValueOfSize(65536));
    CACHE_REQUIRE(keys(third.victims) == std::vector<std::string>{"a"},
                  "a must remain the LRU victim after diagnostic access");
}

CACHE_TEST(duplicate_admission_does_not_double_count_or_touch) {
    Fixture fixture(ReplicaPolicy::Lru, 2 * 65536);
    fixture.cache->admit("a", makeValueOfSize(65536));
    fixture.cache->admit("b", makeValueOfSize(65536));
    const auto duplicate = fixture.cache->admit("a", makeValueOfSize(65536));
    CACHE_REQUIRE(duplicate.status == AdmitStatus::AlreadyResident,
                  "duplicate DATA must be recognised as already resident");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 2 * 65536, "duplicate double counted");
    CACHE_REQUIRE(fixture.cache->admits() == 2, "duplicate incremented the admit counter");
    CACHE_REQUIRE(fixture.cache->touches() == 0,
                  "duplicate DATA must not keep a replica alive");
    CACHE_REQUIRE(fixture.cache->skippedAlreadyResident() == 1, "skip counter wrong");
    CACHE_REQUIRE(fixture.cache->residentKeysLruOrder() ==
                      std::vector<std::string>({"b", "a"}),
                  "duplicate admission changed recency");
}

CACHE_TEST(conflicting_same_key_is_rejected) {
    Fixture fixture(ReplicaPolicy::Unlimited, 0);
    CACHE_REQUIRE(fixture.cache->admit("k", makeValueOfSize(10, 'a')).inserted(), "first");

    const auto size_conflict = fixture.cache->admit("k", makeValueOfSize(11, 'a'));
    CACHE_REQUIRE(size_conflict.status == AdmitStatus::Conflict,
                  "different size must be a conflict");
    CACHE_REQUIRE(fixture.cache->conflicts() == 1, "conflict counter wrong");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 10, "conflict changed resident bytes");
    // The stored value must not have been overwritten with the conflicting one.
    const auto value = fixture.cache->use("k");
    CACHE_REQUIRE(value.value.has_value() && value.value->size() == 10,
                  "conflicting admission overwrote the resident value");

    fixture.cache->setVerifyContent(true);
    const auto content_conflict = fixture.cache->admit("k", makeValueOfSize(10, 'b'));
    CACHE_REQUIRE(content_conflict.status == AdmitStatus::Conflict,
                  "same-size different-content must be a conflict when verification is on");
}

CACHE_TEST(unlimited_matches_a_sufficient_budget) {
    const std::vector<std::pair<std::string, std::size_t>> stream = {
        {"a", 60}, {"b", 30}, {"c", 20}, {"b", 30}, {"d", 10},
        {"e", 90}, {"c", 20}, {"f", 40}, {"a", 60},
    };
    Fixture unlimited(ReplicaPolicy::Unlimited, 0);
    Fixture sufficient(ReplicaPolicy::Lru, 1024);
    for (const auto& [key, size] : stream) {
        unlimited.cache->admit(key, makeValueOfSize(size));
        sufficient.cache->admit(key, makeValueOfSize(size));
    }
    CACHE_REQUIRE(sufficient.cache->evictions() == 0,
                  "a sufficient budget must not evict");
    CACHE_REQUIRE(unlimited.cache->residentKeysLruOrder() ==
                      sufficient.cache->residentKeysLruOrder(),
                  "unlimited and sufficient-budget LRU order must agree");
    CACHE_REQUIRE(unlimited.cache->residentBytes() == sufficient.cache->residentBytes(),
                  "unlimited and sufficient-budget resident bytes must agree");
}

CACHE_TEST(backend_put_failure_is_not_recorded_as_admitted) {
    Fixture fixture(ReplicaPolicy::Lru, 1024);
    fixture.backend->failPuts();
    const auto result = fixture.cache->admit("k", makeValueOfSize(100));
    CACHE_REQUIRE(result.status == AdmitStatus::BackendError,
                  "a failing backend put must be a backend error");
    CACHE_REQUIRE(result.failing_op == "put", "failing op must be reported");
    CACHE_REQUIRE(fixture.cache->residentObjects() == 0,
                  "a failed put must not leave an index entry");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 0, "a failed put accounted bytes");
    CACHE_REQUIRE(fixture.cache->admits() == 0, "a failed put counted an admit");
    CACHE_REQUIRE(fixture.cache->backendErrors() == 1, "backend error not counted");
}

CACHE_TEST(backend_delete_failure_keeps_the_victim_accounted) {
    Fixture fixture(ReplicaPolicy::Lru, 100);
    CACHE_REQUIRE(fixture.cache->admit("a", makeValueOfSize(60)).inserted(), "a");
    fixture.backend->failDeletes();
    const auto result = fixture.cache->admit("b", makeValueOfSize(60));
    CACHE_REQUIRE(result.status == AdmitStatus::BackendError,
                  "a failing eviction delete must be a backend error");
    CACHE_REQUIRE(result.failing_op == "evict_delete", "failing op must be reported");
    CACHE_REQUIRE(result.victims.empty(), "a failed delete must not be reported as an eviction");
    CACHE_REQUIRE(fixture.cache->evictions() == 0, "failed delete counted an eviction");
    CACHE_REQUIRE(fixture.cache->resident("a"),
                  "the victim must stay resident when its delete failed");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 60,
                  "resident bytes must still include the undeleted object");
    CACHE_REQUIRE(!fixture.cache->resident("b"), "b must not be admitted");
}

CACHE_TEST(eviction_records_timestamps_and_ordinals) {
    Fixture fixture(ReplicaPolicy::Lru, 65536);
    fixture.cache->admit("a", makeValueOfSize(65536));
    const auto second = fixture.cache->admit("b", makeValueOfSize(65536));
    CACHE_REQUIRE(second.victims.size() == 1, "expected one victim");
    const auto& victim = second.victims.at(0);
    CACHE_REQUIRE(victim.inserted_mono_ns > 0, "victim insertion time missing");
    CACHE_REQUIRE(victim.last_touch_mono_ns >= victim.inserted_mono_ns,
                  "last touch must not precede insertion");
    CACHE_REQUIRE(victim.insert_seq > 0, "victim insertion ordinal missing");
    CACHE_REQUIRE(victim.touch_seq >= victim.insert_seq,
                  "victim touch ordinal must not precede insertion");
    CACHE_REQUIRE(victim.value_bytes == 65536, "victim size wrong");
    // Snapshot captured at the delete: evicting the only resident object leaves
    // 0 bytes / 0 objects, independently of the object that is inserted after.
    CACHE_REQUIRE(victim.resident_bytes_after == 0,
                  "a single victim must snapshot the empty post-deletion state");
    CACHE_REQUIRE(victim.resident_objects_after == 0,
                  "a single victim must snapshot the empty post-deletion state");
    CACHE_REQUIRE(victim.evicted_mono_ns >= victim.last_touch_mono_ns,
                  "the eviction capture point must not precede the last touch");
    CACHE_REQUIRE(second.resident_bytes_after == 65536,
                  "the admitted object's own state is reported separately");
    CACHE_REQUIRE(second.resident_objects_after == 1,
                  "the admitted object's own state is reported separately");
}

CACHE_TEST(victim_snapshots_are_per_delete_and_independent_of_the_new_object) {
    // budget 100; A=20, B=30, C=40 -> 90 bytes / 3 objects; D=80 evicts A, B, C.
    Fixture fixture(ReplicaPolicy::Lru, 100);
    fixture.cache->admit("a", makeValueOfSize(20));
    fixture.cache->admit("b", makeValueOfSize(30));
    fixture.cache->admit("c", makeValueOfSize(40));

    const auto result = fixture.cache->admit("d", makeValueOfSize(80));
    CACHE_REQUIRE(result.inserted(), "d must be admitted");
    CACHE_REQUIRE(result.victims.size() == 3, "three victims expected");
    CACHE_REQUIRE(result.victims.at(0).key == "a", "first victim must be a");
    CACHE_REQUIRE(result.victims.at(1).key == "b", "second victim must be b");
    CACHE_REQUIRE(result.victims.at(2).key == "c", "third victim must be c");
    CACHE_REQUIRE(result.victims.at(0).resident_bytes_after == 70, "70 after evicting a");
    CACHE_REQUIRE(result.victims.at(0).resident_objects_after == 2, "2 objects after evicting a");
    CACHE_REQUIRE(result.victims.at(1).resident_bytes_after == 40, "40 after evicting b");
    CACHE_REQUIRE(result.victims.at(1).resident_objects_after == 1, "1 object after evicting b");
    CACHE_REQUIRE(result.victims.at(2).resident_bytes_after == 0, "0 after evicting c");
    CACHE_REQUIRE(result.victims.at(2).resident_objects_after == 0, "0 objects after evicting c");
    CACHE_REQUIRE(result.victims.at(0).value_bytes == 20, "victim a size");
    CACHE_REQUIRE(result.victims.at(1).value_bytes == 30, "victim b size");
    CACHE_REQUIRE(result.victims.at(2).value_bytes == 40, "victim c size");
    // The admission result's own state includes the new object; no victim
    // snapshot may contain it.
    CACHE_REQUIRE(result.resident_bytes_after == 80, "final state includes d");
    CACHE_REQUIRE(result.resident_objects_after == 1, "final state includes d");

    // Identical eviction sequence, but the insert fails afterwards: the three
    // snapshots must be unchanged, and the final state is empty.
    Fixture failing(ReplicaPolicy::Lru, 100);
    failing.cache->admit("a", makeValueOfSize(20));
    failing.cache->admit("b", makeValueOfSize(30));
    failing.cache->admit("c", makeValueOfSize(40));
    failing.backend->failPutAtRelative(0);
    const auto failed = failing.cache->admit("d", makeValueOfSize(80));
    CACHE_REQUIRE(failed.status == AdmitStatus::BackendError, "the put must fail");
    CACHE_REQUIRE(failed.victims.size() == 3, "three victims were still deleted");
    CACHE_REQUIRE(failed.victims.at(0).resident_bytes_after == 70, "70 after evicting a");
    CACHE_REQUIRE(failed.victims.at(0).resident_objects_after == 2, "2 objects after evicting a");
    CACHE_REQUIRE(failed.victims.at(1).resident_bytes_after == 40, "40 after evicting b");
    CACHE_REQUIRE(failed.victims.at(1).resident_objects_after == 1, "1 object after evicting b");
    CACHE_REQUIRE(failed.victims.at(2).resident_bytes_after == 0, "0 after evicting c");
    CACHE_REQUIRE(failed.victims.at(2).resident_objects_after == 0, "0 objects after evicting c");
    CACHE_REQUIRE(failed.resident_bytes_after == 0, "nothing was inserted");
    CACHE_REQUIRE(failed.resident_objects_after == 0, "nothing was inserted");
    CACHE_REQUIRE(!failing.backend->hasPhysical(failing.cache->physicalKeyFor("d")),
                  "the rejected object must not be stored");
    // A failing later delete leaves earlier snapshots intact too.
    Fixture partial(ReplicaPolicy::Lru, 100);
    partial.cache->admit("a", makeValueOfSize(20));
    partial.cache->admit("b", makeValueOfSize(30));
    partial.cache->admit("c", makeValueOfSize(40));
    partial.backend->failDeleteAtRelative(1);  // a is deleted, b's delete fails
    const auto partial_result = partial.cache->admit("d", makeValueOfSize(80));
    CACHE_REQUIRE(partial_result.status == AdmitStatus::BackendError, "the delete must fail");
    CACHE_REQUIRE(partial_result.victims.size() == 1, "only a was deleted");
    CACHE_REQUIRE(partial_result.victims.at(0).resident_bytes_after == 70,
                  "a's snapshot is still the state after a was deleted");
    CACHE_REQUIRE(partial_result.victims.at(0).resident_objects_after == 2,
                  "a's snapshot is still the state after a was deleted");
    CACHE_REQUIRE(partial_result.resident_bytes_after == 70, "b and c remain resident");
    CACHE_REQUIRE(partial_result.resident_objects_after == 2, "b and c remain resident");
}

CACHE_TEST(divergence_is_repaired_and_reported) {
    Fixture fixture(ReplicaPolicy::Lru, 65536);
    fixture.cache->admit("k", makeValueOfSize(100));
    CACHE_REQUIRE(fixture.cache->resident("k"), "k must be resident");
    // Mutate the KV behind the cache's back.
    fixture.backend->eraseDirect(fixture.cache->physicalKeyFor("k"));

    const auto used = fixture.cache->use("k");
    CACHE_REQUIRE(used.divergence, "a missing backing value must be reported as divergence");
    CACHE_REQUIRE(used.repaired, "divergence must be repaired");
    CACHE_REQUIRE(!used.value.has_value(), "a missing object must not be served");
    CACHE_REQUIRE(!fixture.cache->resident("k"), "the index entry must be dropped");
    CACHE_REQUIRE(fixture.cache->residentBytes() == 0, "repaired bytes must return to zero");
    CACHE_REQUIRE(fixture.cache->divergencesRepaired() == 1, "repair counter wrong");
}

int main() {
    return cache_test::runAll("replica_cache");
}