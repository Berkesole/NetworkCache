#pragma once

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "bloomfilter.hpp"

// QueryTable keeps both the legacy pending-demand entries and the state used by
// staged fallback search. Node owns wire parsing/sends; this type owns exact
// origin demand, deadlines, best-hop suppression, and fallback reverse paths.
class QueryTable {
public:
    using Clock = std::chrono::steady_clock;
    using TimePoint = Clock::time_point;
    using ContentSignature = uint32_t;
    using QueryUid = uint64_t;

    enum class FallbackPolicy {
        Enabled,
        Disabled,
    };

    static constexpr QueryUid kStableUidBit = QueryUid{1} << 63;
    static constexpr QueryUid kOriginMask = QueryUid{0x7fff};
    static constexpr QueryUid kSequenceMask = (QueryUid{1} << 48) - 1;

    struct Config {
        std::chrono::milliseconds legacy_timeout{500};
        std::chrono::milliseconds fallback_delay{1500};
        std::chrono::milliseconds state_lifetime{7000};
    };

    struct QueryEntry {
        BloomFilter bf;
        TimePoint created_at;
        std::chrono::milliseconds timeout;
        // Staged fallback mode consumes each exact DATA obligation once.  The
        // Bloom filter remains immutable, so remember exact keys already
        // returned instead of attempting Bloom deletion.
        std::unordered_set<std::string> satisfied_keys;
    };

    struct SignatureRoute {
        ContentSignature signature = 0;
        std::vector<uint32_t> matched_neighbors;
        std::vector<uint32_t> sent_neighbors;
        std::vector<uint32_t> remaining_neighbors;
    };

    struct PreferredEntry {
        QueryUid request_id = 0;
        uint32_t upstream_node = 0;
        BloomFilter request_bf;
        std::vector<SignatureRoute> routes;
        uint32_t hop_count = 0;
        TimePoint created_at{};
        TimePoint expires_at{};
    };

    struct OriginKey {
        std::string key;
        ContentSignature signature = 0;
    };

    struct OriginQuery {
        QueryUid uid = 0;
        FallbackPolicy fallback_policy = FallbackPolicy::Enabled;
        std::unordered_map<std::string, ContentSignature> key_signatures;
        std::unordered_set<std::string> unresolved_keys;
        TimePoint first_sent_at{};
        TimePoint fallback_deadline{};
        TimePoint expires_at{};
        std::optional<TimePoint> fallback_triggered_at;
        std::optional<TimePoint> completed_at;
    };

    struct DueOriginFallback {
        QueryUid uid = 0;
        std::vector<OriginKey> unresolved;
        TimePoint first_sent_at{};
        TimePoint triggered_at{};
    };

    struct KeySatisfaction {
        bool query_known = false;
        bool key_expected = false;
        bool newly_satisfied = false;
        bool duplicate = false;
        bool query_complete = false;
        std::optional<ContentSignature> expected_signature;
    };

    struct FallbackEntry {
        QueryUid uid = 0;
        uint32_t parent_node = 0;
        uint32_t best_hop = std::numeric_limits<uint32_t>::max();
        std::vector<ContentSignature> signatures;
        std::unordered_map<uint32_t, uint32_t> egress_min_hop;
        std::unordered_set<std::string> returned_keys;
        TimePoint created_at{};
        TimePoint expires_at{};
    };

    struct AcceptFallbackResult {
        bool accepted = false;
        bool first_seen = false;
        bool improved_path = false;
        bool duplicate_suppressed = false;
        bool reached_origin = false;
        uint32_t parent_node = 0;
        uint32_t best_hop = std::numeric_limits<uint32_t>::max();
        uint32_t outgoing_hop = std::numeric_limits<uint32_t>::max();
        std::vector<uint32_t> egress_nodes;
    };

    struct DataRouteResult {
        bool query_known = false;
        bool key_expected = false;
        bool newly_satisfied = false;
        bool duplicate_suppressed = false;
        bool wrong_direction_suppressed = false;
        bool deliver_to_origin = false;
        bool query_complete = false;
        std::optional<uint32_t> parent_node;
        std::optional<ContentSignature> expected_signature;
    };

    struct ExpireResult {
        std::size_t legacy_entries = 0;
        std::size_t preferred_entries = 0;
        std::size_t origin_queries = 0;
        std::size_t origin_unsatisfied = 0;
        std::size_t fallback_entries = 0;

        std::size_t total() const {
            return legacy_entries + preferred_entries + origin_queries +
                   fallback_entries;
        }
    };

    QueryTable() = default;
    explicit QueryTable(Config config) : config_(config) {
        validateDuration(config_.legacy_timeout, "legacy timeout");
        validateDuration(config_.fallback_delay, "fallback delay");
        validateDuration(config_.state_lifetime, "state lifetime");
        if (config_.fallback_delay >= config_.state_lifetime) {
            throw std::invalid_argument(
                "QueryTable: fallback delay must be shorter than state lifetime");
        }
    }

    static QueryUid makeStableUid(uint32_t origin_node,
                                  QueryUid local_sequence) {
        if (origin_node == 0 || origin_node > kOriginMask) {
            throw std::invalid_argument("QueryTable: origin node exceeds 15 bits");
        }
        if (local_sequence > kSequenceMask) {
            throw std::invalid_argument("QueryTable: local sequence exceeds 48 bits");
        }
        return kStableUidBit |
               (static_cast<QueryUid>(origin_node) << 48) |
               local_sequence;
    }

    static bool isStableUid(QueryUid uid) {
        return (uid & kStableUidBit) != 0;
    }

    static uint32_t stableUidOrigin(QueryUid uid) {
        requireStableUid(uid);
        return static_cast<uint32_t>((uid >> 48) & kOriginMask);
    }

    static QueryUid stableUidSequence(QueryUid uid) {
        requireStableUid(uid);
        return uid & kSequenceMask;
    }

    // Legacy pending-demand API, source-compatible with wire v1/v2 Node.
    void set(uint32_t from_node, const BloomFilter& bf) {
        set(from_node, bf, config_.legacy_timeout);
    }

    void set(uint32_t from_node, const BloomFilter& bf,
             std::chrono::milliseconds timeout) {
        validateDuration(timeout, "legacy entry timeout");
        const auto now = Clock::now();
        entries_[from_node].push_back(QueryEntry{bf, now, timeout});
    }

    std::vector<uint32_t> get(const std::string& key) {
        last_expired_count_ = cleanupExpiredLegacy(Clock::now());
        std::vector<uint32_t> matches;
        for (const auto& [node_id, entry_list] : entries_) {
            for (const auto& entry : entry_list) {
                if (entry.bf.contains(key)) {
                    matches.push_back(node_id);
                    break;
                }
            }
        }
        canonicalize(matches);
        return matches;
    }

    // Return each pending downstream obligation for key at most once.  This is
    // used by staged mode before cache duplicate suppression: a DATA packet
    // can still owe a reverse-path send even when this node already cached an
    // earlier copy, while later duplicate DATA must not replay stale entries.
    std::vector<uint32_t> takePending(const std::string& key) {
        last_expired_count_ = cleanupExpiredLegacy(Clock::now());
        std::vector<uint32_t> matches;
        for (auto& [node_id, entry_list] : entries_) {
            bool newly_pending = false;
            for (auto& entry : entry_list) {
                if (entry.bf.contains(key) &&
                    entry.satisfied_keys.insert(key).second) {
                    newly_pending = true;
                }
            }
            if (newly_pending) matches.push_back(node_id);
        }
        canonicalize(matches);
        return matches;
    }

    // A node may satisfy an upstream REQUEST directly from its local cache
    // before another copy of the same DATA returns along a forwarded branch.
    // Mark that exact upstream obligation complete so takePending() does not
    // turn the later copy into a duplicate response.
    void markPendingSatisfied(uint32_t node_id, const std::string& key) {
        const auto it = entries_.find(node_id);
        if (it == entries_.end()) return;
        for (auto& entry : it->second) {
            if (entry.bf.contains(key)) entry.satisfied_keys.insert(key);
        }
    }

    void cleanup() {
        last_expired_count_ = cleanupExpiredLegacy(Clock::now());
    }

    std::size_t lastExpiredCount() const { return last_expired_count_; }

    void trackPreferred(QueryUid request_id, uint32_t upstream_node,
                        const BloomFilter& request_bf,
                        std::vector<SignatureRoute> routes,
                        uint32_t hop_count,
                        std::chrono::milliseconds timeout,
                        TimePoint now = Clock::now()) {
        validateDuration(timeout, "preferred entry timeout");
        for (auto& route : routes) canonicalizeRoute(route);
        preferred_entries_[request_id].push_back(PreferredEntry{
            request_id, upstream_node, request_bf, std::move(routes), hop_count,
            now, now + timeout});
    }

    std::vector<PreferredEntry> preferredEntries(QueryUid request_id) const {
        const auto it = preferred_entries_.find(request_id);
        return it == preferred_entries_.end()
                   ? std::vector<PreferredEntry>{}
                   : it->second;
    }

    bool startOriginQuery(QueryUid uid, const std::vector<OriginKey>& keys,
                          TimePoint now = Clock::now()) {
        return startOriginQuery(uid, keys, config_.fallback_delay,
                                config_.state_lifetime,
                                FallbackPolicy::Enabled, now);
    }

    bool startOriginQuery(QueryUid uid, const std::vector<OriginKey>& keys,
                          std::chrono::milliseconds fallback_delay,
                          std::chrono::milliseconds lifetime,
                          FallbackPolicy fallback_policy = FallbackPolicy::Enabled,
                          TimePoint now = Clock::now()) {
        requireStableUid(uid);
        validateDuration(fallback_delay, "fallback delay");
        validateDuration(lifetime, "origin lifetime");
        if (fallback_delay >= lifetime) {
            throw std::invalid_argument(
                "QueryTable: fallback delay must be shorter than origin lifetime");
        }
        if (keys.empty()) {
            throw std::invalid_argument("QueryTable: origin query has no keys");
        }
        if (origin_queries_.contains(uid) || fallback_entries_.contains(uid)) {
            return false;
        }

        OriginQuery query;
        query.uid = uid;
        query.fallback_policy = fallback_policy;
        query.first_sent_at = now;
        query.fallback_deadline = now + fallback_delay;
        query.expires_at = now + lifetime;
        for (const auto& item : keys) {
            if (item.key.empty()) {
                throw std::invalid_argument("QueryTable: empty origin key");
            }
            const auto [it, inserted] =
                query.key_signatures.emplace(item.key, item.signature);
            if (!inserted && it->second != item.signature) {
                throw std::invalid_argument(
                    "QueryTable: one key has conflicting signatures");
            }
            query.unresolved_keys.insert(item.key);
        }
        origin_queries_.emplace(uid, std::move(query));
        return true;
    }

    KeySatisfaction markDataSatisfied(QueryUid uid, const std::string& key,
                                      TimePoint now = Clock::now()) {
        KeySatisfaction result;
        auto it = origin_queries_.find(uid);
        if (it == origin_queries_.end() || now >= it->second.expires_at) {
            return result;
        }

        auto& query = it->second;
        result.query_known = true;
        const auto key_it = query.key_signatures.find(key);
        if (key_it == query.key_signatures.end()) return result;
        result.key_expected = true;
        result.expected_signature = key_it->second;
        const auto erased = query.unresolved_keys.erase(key);
        result.newly_satisfied = erased == 1;
        result.duplicate = erased == 0;
        result.query_complete = query.unresolved_keys.empty();
        if (result.query_complete && !query.completed_at.has_value()) {
            query.completed_at = now;
        }
        return result;
    }

    // Preferred-phase aggregation can replace the packet-level request_id.
    // Resolve normal DATA by its exact key across all live origin queries so
    // Node does not need to maintain a second key-to-origin index.
    std::vector<std::pair<QueryUid, KeySatisfaction>> markDataSatisfiedByKey(
        const std::string& key, TimePoint now = Clock::now()) {
        std::vector<QueryUid> matching_uids;
        for (const auto& [uid, query] : origin_queries_) {
            if (now < query.expires_at && query.key_signatures.contains(key)) {
                matching_uids.push_back(uid);
            }
        }
        std::sort(matching_uids.begin(), matching_uids.end());

        std::vector<std::pair<QueryUid, KeySatisfaction>> results;
        results.reserve(matching_uids.size());
        for (const auto uid : matching_uids) {
            results.emplace_back(uid, markDataSatisfied(uid, key, now));
        }
        return results;
    }

    std::vector<DueOriginFallback> takeDueOriginFallbacks(
        TimePoint now = Clock::now()) {
        expire(now);
        std::vector<DueOriginFallback> due;
        for (auto& [uid, query] : origin_queries_) {
            if (query.fallback_policy == FallbackPolicy::Disabled ||
                query.completed_at.has_value() ||
                query.fallback_triggered_at.has_value() ||
                now < query.fallback_deadline) {
                continue;
            }
            query.fallback_triggered_at = now;
            DueOriginFallback item;
            item.uid = uid;
            item.first_sent_at = query.first_sent_at;
            item.triggered_at = now;
            item.unresolved.reserve(query.unresolved_keys.size());
            for (const auto& key : query.unresolved_keys) {
                item.unresolved.push_back(
                    OriginKey{key, query.key_signatures.at(key)});
            }
            std::sort(item.unresolved.begin(), item.unresolved.end(),
                      [](const OriginKey& lhs, const OriginKey& rhs) {
                          return lhs.key < rhs.key;
                      });
            due.push_back(std::move(item));
        }
        std::sort(due.begin(), due.end(),
                  [](const DueOriginFallback& lhs,
                     const DueOriginFallback& rhs) {
                      return lhs.uid < rhs.uid;
                  });
        return due;
    }

    std::optional<OriginQuery> originQuery(QueryUid uid) const {
        const auto it = origin_queries_.find(uid);
        if (it == origin_queries_.end()) return std::nullopt;
        return it->second;
    }

    AcceptFallbackResult acceptFallback(
        QueryUid uid, uint32_t parent_node, uint32_t incoming_hop,
        std::vector<ContentSignature> signatures,
        std::vector<uint32_t> neighbors,
        TimePoint now = Clock::now()) {
        return acceptFallback(uid, parent_node, incoming_hop,
                              std::move(signatures), std::move(neighbors),
                              config_.state_lifetime, now);
    }

    AcceptFallbackResult acceptFallback(
        QueryUid uid, uint32_t parent_node, uint32_t incoming_hop,
        std::vector<ContentSignature> signatures,
        std::vector<uint32_t> neighbors,
        std::chrono::milliseconds lifetime,
        TimePoint now = Clock::now()) {
        requireStableUid(uid);
        validateDuration(lifetime, "fallback lifetime");
        if (signatures.empty()) {
            throw std::invalid_argument("QueryTable: fallback has no signatures");
        }
        canonicalize(signatures);
        canonicalize(neighbors);

        AcceptFallbackResult result;
        if (origin_queries_.contains(uid)) {
            result.duplicate_suppressed = true;
            result.reached_origin = true;
            return result;
        }

        auto it = fallback_entries_.find(uid);
        if (it != fallback_entries_.end() && now >= it->second.expires_at) {
            fallback_entries_.erase(it);
            it = fallback_entries_.end();
        }

        if (it == fallback_entries_.end()) {
            FallbackEntry entry;
            entry.uid = uid;
            entry.parent_node = parent_node;
            entry.best_hop = incoming_hop;
            entry.signatures = std::move(signatures);
            entry.created_at = now;
            entry.expires_at = now + lifetime;
            it = fallback_entries_.emplace(uid, std::move(entry)).first;
            result.accepted = true;
            result.first_seen = true;
        } else if (incoming_hop < it->second.best_hop) {
            it->second.parent_node = parent_node;
            it->second.best_hop = incoming_hop;
            it->second.signatures = std::move(signatures);
            result.accepted = true;
            result.improved_path = true;
        } else {
            result.duplicate_suppressed = true;
            result.parent_node = it->second.parent_node;
            result.best_hop = it->second.best_hop;
            return result;
        }

        auto& entry = it->second;
        result.parent_node = entry.parent_node;
        result.best_hop = entry.best_hop;
        result.outgoing_hop = entry.best_hop + 1;
        for (const auto neighbor : neighbors) {
            if (neighbor == parent_node) continue;
            const auto sent = entry.egress_min_hop.find(neighbor);
            if (sent != entry.egress_min_hop.end() &&
                sent->second <= result.outgoing_hop) {
                continue;
            }
            entry.egress_min_hop[neighbor] = result.outgoing_hop;
            result.egress_nodes.push_back(neighbor);
        }
        return result;
    }

    // Non-mutating counterpart of routeFallbackData(): performs exactly the
    // identity/relevance checks and reports what the protocol would conclude,
    // but consumes no reverse-path obligation, marks no key as returned and
    // changes no query state. Node uses it to decide whether an incoming DATA
    // packet belongs to an acceptable request lifecycle BEFORE any state is
    // consumed, so an unrelated packet can never trigger an immutability
    // failure and a conflicting packet cannot consume state.
    //
    // Only query_known / key_expected / duplicate_suppressed /
    // wrong_direction_suppressed / parent_node / expected_signature are
    // meaningful here; newly_satisfied, deliver_to_origin and query_complete are
    // deliberately false because deciding them consumes state, which
    // routeFallbackData() does afterwards. Keep the checks below in sync with
    // routeFallbackData().
    DataRouteResult probeFallbackData(
        QueryUid uid, const std::string& key, ContentSignature signature,
        uint32_t incoming_node,
        TimePoint now = Clock::now()) const {
        DataRouteResult result;
        const auto origin_it = origin_queries_.find(uid);
        if (origin_it != origin_queries_.end() &&
            now < origin_it->second.expires_at) {
            result.query_known = true;
            const auto expected = origin_it->second.key_signatures.find(key);
            if (expected == origin_it->second.key_signatures.end()) return result;
            result.expected_signature = expected->second;
            if (expected->second != signature) return result;
            result.key_expected = true;
            return result;
        }

        const auto fallback_it = fallback_entries_.find(uid);
        if (fallback_it == fallback_entries_.end() ||
            now >= fallback_it->second.expires_at) {
            return result;
        }
        const auto& entry = fallback_it->second;
        result.query_known = true;
        result.key_expected = std::binary_search(
            entry.signatures.begin(), entry.signatures.end(), signature);
        if (!result.key_expected) return result;
        if (incoming_node == entry.parent_node) {
            result.duplicate_suppressed = true;
            result.wrong_direction_suppressed = true;
            return result;
        }
        if (entry.returned_keys.count(key) != 0) {
            result.duplicate_suppressed = true;
            return result;
        }
        result.parent_node = entry.parent_node;
        return result;
    }

    DataRouteResult routeFallbackData(
        QueryUid uid, const std::string& key, ContentSignature signature,
        uint32_t incoming_node,
        TimePoint now = Clock::now()) {
        DataRouteResult result;
        auto origin_it = origin_queries_.find(uid);
        if (origin_it != origin_queries_.end() &&
            now < origin_it->second.expires_at) {
            result.query_known = true;
            const auto expected = origin_it->second.key_signatures.find(key);
            if (expected == origin_it->second.key_signatures.end()) return result;
            result.expected_signature = expected->second;
            if (expected->second != signature) return result;

            const auto satisfaction = markDataSatisfied(uid, key, now);
            result.key_expected = satisfaction.key_expected;
            result.newly_satisfied = satisfaction.newly_satisfied;
            result.duplicate_suppressed = satisfaction.duplicate;
            result.deliver_to_origin = satisfaction.newly_satisfied;
            result.query_complete = satisfaction.query_complete;
            return result;
        }

        auto fallback_it = fallback_entries_.find(uid);
        if (fallback_it == fallback_entries_.end() ||
            now >= fallback_it->second.expires_at) {
            return result;
        }
        auto& entry = fallback_it->second;
        result.query_known = true;
        result.key_expected = std::binary_search(
            entry.signatures.begin(), entry.signatures.end(), signature);
        if (!result.key_expected) return result;
        // A better-hop REQUEST may replace the parent while DATA from the old
        // tree is in flight. DATA arriving from the current parent points away
        // from the origin. Do not poison returned_keys; a valid child response
        // must remain eligible to take the reverse path.
        if (incoming_node == entry.parent_node) {
            result.duplicate_suppressed = true;
            result.wrong_direction_suppressed = true;
            return result;
        }
        if (!entry.returned_keys.insert(key).second) {
            result.duplicate_suppressed = true;
            return result;
        }
        result.newly_satisfied = true;
        result.parent_node = entry.parent_node;
        return result;
    }

    std::optional<FallbackEntry> fallbackEntry(QueryUid uid) const {
        const auto it = fallback_entries_.find(uid);
        if (it == fallback_entries_.end()) return std::nullopt;
        return it->second;
    }

    ExpireResult expire(TimePoint now = Clock::now()) {
        ExpireResult result;
        result.legacy_entries = cleanupExpiredLegacy(now);

        for (auto map_it = preferred_entries_.begin();
             map_it != preferred_entries_.end();) {
            auto& list = map_it->second;
            const auto before = list.size();
            list.erase(std::remove_if(list.begin(), list.end(),
                                      [now](const PreferredEntry& entry) {
                                          return now >= entry.expires_at;
                                      }),
                       list.end());
            result.preferred_entries += before - list.size();
            if (list.empty()) {
                map_it = preferred_entries_.erase(map_it);
            } else {
                ++map_it;
            }
        }

        for (auto it = origin_queries_.begin(); it != origin_queries_.end();) {
            if (now < it->second.expires_at) {
                ++it;
                continue;
            }
            ++result.origin_queries;
            if (!it->second.unresolved_keys.empty()) {
                ++result.origin_unsatisfied;
            }
            it = origin_queries_.erase(it);
        }
        for (auto it = fallback_entries_.begin();
             it != fallback_entries_.end();) {
            if (now < it->second.expires_at) {
                ++it;
                continue;
            }
            ++result.fallback_entries;
            it = fallback_entries_.erase(it);
        }
        last_expired_count_ = result.total();
        return result;
    }

private:
    Config config_{};
    std::unordered_map<uint32_t, std::vector<QueryEntry>> entries_;
    std::unordered_map<QueryUid, std::vector<PreferredEntry>> preferred_entries_;
    std::unordered_map<QueryUid, OriginQuery> origin_queries_;
    std::unordered_map<QueryUid, FallbackEntry> fallback_entries_;
    std::size_t last_expired_count_ = 0;

    static void validateDuration(std::chrono::milliseconds value,
                                 const char* label) {
        if (value.count() <= 0) {
            throw std::invalid_argument(std::string("QueryTable: invalid ") +
                                        label);
        }
    }

    static void requireStableUid(QueryUid uid) {
        if (!isStableUid(uid) || ((uid >> 48) & kOriginMask) == 0) {
            throw std::invalid_argument("QueryTable: invalid stable UID");
        }
    }

    template <typename T>
    static void canonicalize(std::vector<T>& values) {
        std::sort(values.begin(), values.end());
        values.erase(std::unique(values.begin(), values.end()), values.end());
    }

    static void canonicalizeRoute(SignatureRoute& route) {
        canonicalize(route.matched_neighbors);
        canonicalize(route.sent_neighbors);
        canonicalize(route.remaining_neighbors);
    }

    std::size_t cleanupExpiredLegacy(TimePoint now) {
        std::size_t expired_count = 0;
        for (auto it = entries_.begin(); it != entries_.end();) {
            auto& entry_list = it->second;
            const auto before_size = entry_list.size();
            entry_list.erase(
                std::remove_if(entry_list.begin(), entry_list.end(),
                               [now](const QueryEntry& entry) {
                                   return now - entry.created_at > entry.timeout;
                               }),
                entry_list.end());
            expired_count += before_size - entry_list.size();
            if (entry_list.empty()) {
                it = entries_.erase(it);
            } else {
                ++it;
            }
        }
        return expired_count;
    }
};
