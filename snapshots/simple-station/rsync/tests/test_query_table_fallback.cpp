#include <algorithm>
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "query_table.hpp"

namespace {

using namespace std::chrono_literals;
using TimePoint = QueryTable::TimePoint;

TimePoint at(std::chrono::milliseconds offset) {
    return TimePoint{} + offset;
}

void testStableUid() {
    const auto uid = QueryTable::makeStableUid(54, 0x123456789abcULL);
    assert(QueryTable::isStableUid(uid));
    assert(QueryTable::stableUidOrigin(uid) == 54);
    assert(QueryTable::stableUidSequence(uid) == 0x123456789abcULL);

    bool rejected = false;
    try {
        (void)QueryTable::makeStableUid(0, 1);
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    assert(rejected);
    rejected = false;
    try {
        (void)QueryTable::makeStableUid(1, QueryTable::kSequenceMask + 1);
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    assert(rejected);
}

void testPreferredState() {
    QueryTable table;
    BloomFilter bf(1024, 3);
    bf.add("alpha");
    QueryTable::SignatureRoute route;
    route.signature = 17;
    route.matched_neighbors = {9, 1, 9};
    route.sent_neighbors = {9, 9};
    route.remaining_neighbors = {13, 5, 5};

    table.trackPreferred(91, 1, bf, {route}, 4, 500ms, at(10ms));
    const auto entries = table.preferredEntries(91);
    assert(entries.size() == 1);
    assert(entries[0].upstream_node == 1);
    assert(entries[0].hop_count == 4);
    assert((entries[0].routes[0].matched_neighbors ==
            std::vector<uint32_t>{1, 9}));
    assert((entries[0].routes[0].sent_neighbors ==
            std::vector<uint32_t>{9}));
    assert((entries[0].routes[0].remaining_neighbors ==
            std::vector<uint32_t>{5, 13}));

    auto expired = table.expire(at(510ms));
    assert(expired.preferred_entries == 1);
    assert(table.preferredEntries(91).empty());
}

void testPendingDataObligationsAreConsumedOnce() {
    QueryTable table;
    BloomFilter bf(1024, 3);
    bf.add("alpha");
    table.set(7, bf, 7000ms);
    table.set(7, bf, 7000ms);
    table.set(9, bf, 7000ms);

    // All matching entries for one downstream collapse into one send and are
    // consumed together. Legacy get() remains a non-consuming compatibility
    // query, but takePending() will not replay the DATA obligation.
    assert((table.takePending("alpha") == std::vector<uint32_t>{7, 9}));
    assert(table.takePending("alpha").empty());
    assert((table.get("alpha") == std::vector<uint32_t>{7, 9}));

    table.set(11, bf, 7000ms);
    table.markPendingSatisfied(11, "alpha");
    assert(table.takePending("alpha").empty());
}

void testOriginDeadlineAndExactKeys() {
    QueryTable table;
    const auto uid = QueryTable::makeStableUid(14, 7);
    assert(table.startOriginQuery(
        uid, {{"alpha", 101}, {"beta", 202}}, at(0ms)));
    assert(!table.startOriginQuery(uid, {{"alpha", 101}}, at(1ms)));

    // An unrelated DATA key cannot satisfy a Bloom/signature-compatible query.
    auto unrelated = table.markDataSatisfied(uid, "not-alpha", at(100ms));
    assert(unrelated.query_known);
    assert(!unrelated.key_expected);

    auto alpha = table.markDataSatisfied(uid, "alpha", at(1499ms));
    assert(alpha.newly_satisfied);
    assert(!alpha.query_complete);
    assert(table.takeDueOriginFallbacks(at(1499ms)).empty());

    const auto due = table.takeDueOriginFallbacks(at(1500ms));
    assert(due.size() == 1);
    assert(due[0].uid == uid);
    assert(due[0].unresolved.size() == 1);
    assert(due[0].unresolved[0].key == "beta");
    assert(due[0].unresolved[0].signature == 202);
    assert(table.takeDueOriginFallbacks(at(1600ms)).empty());

    auto wrong_signature =
        table.routeFallbackData(uid, "beta", 999, 9, at(1700ms));
    assert(wrong_signature.query_known);
    assert(!wrong_signature.key_expected);
    assert(!wrong_signature.deliver_to_origin);

    auto beta = table.routeFallbackData(uid, "beta", 202, 9, at(1701ms));
    assert(beta.key_expected);
    assert(beta.newly_satisfied);
    assert(beta.deliver_to_origin);
    assert(beta.query_complete);

    auto duplicate = table.routeFallbackData(uid, "beta", 202, 9, at(1702ms));
    assert(duplicate.key_expected);
    assert(duplicate.duplicate_suppressed);
    assert(!duplicate.deliver_to_origin);

    auto loop = table.acceptFallback(uid, 9, 4, {202}, {1, 9, 13},
                                     at(1800ms));
    assert(!loop.accepted);
    assert(loop.reached_origin);
    assert(loop.duplicate_suppressed);
}

void testDataWinsAtDeadline() {
    QueryTable table;
    const auto uid = QueryTable::makeStableUid(3, 8);
    const auto second_uid = QueryTable::makeStableUid(4, 9);
    assert(table.startOriginQuery(uid, {{"alpha", 42}}, at(0ms)));
    assert(table.startOriginQuery(second_uid,
                                  {{"alpha", 42}, {"beta", 43}}, at(0ms)));

    // Node's processOne() handles DATA before calling takeDue...; the table
    // therefore observes completion and does not arm fallback at the boundary.
    const auto matches = table.markDataSatisfiedByKey("alpha", at(1500ms));
    assert(matches.size() == 2);
    assert(matches[0].first == uid);
    assert(matches[0].second.query_complete);
    assert(matches[1].first == second_uid);
    assert(!matches[1].second.query_complete);
    const auto due = table.takeDueOriginFallbacks(at(1500ms));
    assert(due.size() == 1);
    assert(due[0].uid == second_uid);
    assert(due[0].unresolved.size() == 1);
    assert(due[0].unresolved[0].key == "beta");
    assert(table.markDataSatisfiedByKey("unrelated", at(1501ms)).empty());
}

void testFallbackDisabledOriginTracksExactCompletionWithoutFallback() {
    QueryTable table;
    const auto uid = QueryTable::makeStableUid(5, 10);
    assert(table.startOriginQuery(
        uid, {{"known-owner-key", 77}}, 1500ms, 7000ms,
        QueryTable::FallbackPolicy::Disabled, at(0ms)));

    const auto tracked = table.originQuery(uid);
    assert(tracked.has_value());
    assert(tracked->fallback_policy == QueryTable::FallbackPolicy::Disabled);
    assert(table.takeDueOriginFallbacks(at(1500ms)).empty());
    assert(table.takeDueOriginFallbacks(at(6999ms)).empty());

    const auto satisfied = table.markDataSatisfied(
        uid, "known-owner-key", at(2000ms));
    assert(satisfied.query_known);
    assert(satisfied.key_expected);
    assert(satisfied.newly_satisfied);
    assert(satisfied.query_complete);

    const auto expired = table.expire(at(7000ms));
    assert(expired.origin_queries == 1);
    assert(expired.origin_unsatisfied == 0);

    const auto missing_uid = QueryTable::makeStableUid(5, 11);
    assert(table.startOriginQuery(
        missing_uid, {{"missing-known-owner-key", 88}}, 1500ms, 7000ms,
        QueryTable::FallbackPolicy::Disabled, at(8000ms)));
    assert(table.takeDueOriginFallbacks(at(9500ms)).empty());
    const auto missing_expired = table.expire(at(15000ms));
    assert(missing_expired.origin_queries == 1);
    assert(missing_expired.origin_unsatisfied == 1);
}

void testFallbackBestHopAndEgressSuppression() {
    QueryTable table;
    const auto uid = QueryTable::makeStableUid(54, 99);

    auto first = table.acceptFallback(uid, 2, 5, {303, 101, 303},
                                      {4, 3, 2, 4}, at(0ms));
    assert(first.accepted);
    assert(first.first_seen);
    assert(first.parent_node == 2);
    assert(first.best_hop == 5);
    assert(first.outgoing_hop == 6);
    assert((first.egress_nodes == std::vector<uint32_t>{3, 4}));

    auto equal = table.acceptFallback(uid, 3, 5, {101, 303},
                                      {2, 3, 4}, at(1ms));
    assert(!equal.accepted);
    assert(equal.duplicate_suppressed);
    assert(equal.parent_node == 2);

    auto worse = table.acceptFallback(uid, 4, 7, {101, 303},
                                      {2, 3, 4}, at(2ms));
    assert(!worse.accepted);
    assert(worse.duplicate_suppressed);

    // A shorter path changes the reverse parent. Previously used egresses may
    // be retried only because the outgoing hop is strictly better.
    auto better = table.acceptFallback(uid, 4, 3, {101, 303},
                                       {2, 3, 4, 5}, at(3ms));
    assert(better.accepted);
    assert(better.improved_path);
    assert(better.parent_node == 4);
    assert(better.outgoing_hop == 4);
    assert((better.egress_nodes == std::vector<uint32_t>{2, 3, 5}));

    auto unrelated = table.routeFallbackData(uid, "wrong", 404, 3, at(4ms));
    assert(unrelated.query_known);
    assert(!unrelated.key_expected);
    assert(!unrelated.parent_node.has_value());

    // DATA from the newly selected parent is wrong-direction traffic. It must
    // not consume the key and prevent a subsequent child response.
    auto wrong_direction = table.routeFallbackData(uid, "alpha", 101, 4,
                                                    at(4ms));
    assert(wrong_direction.duplicate_suppressed);
    assert(wrong_direction.wrong_direction_suppressed);
    assert(!wrong_direction.parent_node.has_value());

    auto routed = table.routeFallbackData(uid, "alpha", 101, 3, at(5ms));
    assert(routed.key_expected);
    assert(routed.newly_satisfied);
    assert(routed.parent_node == 4);

    auto duplicate = table.routeFallbackData(uid, "alpha", 101, 3, at(6ms));
    assert(duplicate.duplicate_suppressed);
    assert(!duplicate.parent_node.has_value());
}

void testExpiryAndTombstone() {
    QueryTable table;
    const auto complete_uid = QueryTable::makeStableUid(1, 1);
    assert(table.startOriginQuery(complete_uid, {{"alpha", 11}}, at(0ms)));
    assert(table.markDataSatisfied(complete_uid, "alpha", at(100ms))
               .query_complete);

    // Completion retains an exact tombstone for the whole 7 second lifetime.
    auto late = table.routeFallbackData(complete_uid, "alpha", 11, 2,
                                        at(6999ms));
    assert(late.duplicate_suppressed);
    assert(!late.deliver_to_origin);

    const auto unresolved_uid = QueryTable::makeStableUid(2, 1);
    assert(table.startOriginQuery(unresolved_uid, {{"beta", 22}}, at(0ms)));
    const auto fallback_uid = QueryTable::makeStableUid(3, 1);
    assert(table.acceptFallback(fallback_uid, 7, 2, {33}, {7, 8},
                                at(0ms)).accepted);

    const auto expired = table.expire(at(7000ms));
    assert(expired.origin_queries == 2);
    assert(expired.origin_unsatisfied == 1);
    assert(expired.fallback_entries == 1);
    assert(!table.originQuery(complete_uid).has_value());
    assert(!table.fallbackEntry(fallback_uid).has_value());

    const auto forgotten = table.routeFallbackData(
        complete_uid, "alpha", 11, 2, at(7001ms));
    assert(!forgotten.query_known);
}

}  // namespace

int main() {
    testStableUid();
    testPreferredState();
    testPendingDataObligationsAreConsumedOnce();
    testOriginDeadlineAndExactKeys();
    testDataWinsAtDeadline();
    testFallbackDisabledOriginTracksExactCompletionWithoutFallback();
    testFallbackBestHopAndEgressSuppression();
    testExpiryAndTombstone();
    std::cout << "query_table_fallback: all tests passed\n";
    return 0;
}
