#pragma once
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "bloomfilter.hpp"

// QueryTable: Records "who is waiting for which data", supports time-window based aging.
// Design points:
// - Multiple requests from the same node generate multiple entries (no overwriting)
// - Each entry has its own timeout, automatically aged out when expired
class QueryTable {
public:
    struct QueryEntry {
        BloomFilter bf;
        std::chrono::steady_clock::time_point created_at;
        std::chrono::milliseconds timeout;
    };

    // Record a batch of requests from from_node (represented by BloomFilter)
    // Multiple calls will append multiple entries for the same node, not overwrite old entries.
    void set(uint32_t from_node, const BloomFilter& bf,
             std::chrono::milliseconds timeout = default_timeout_) {
        auto now = std::chrono::steady_clock::now();
        entries_[from_node].push_back(QueryEntry{bf, now, timeout});
    }

    // Check all entries' BF, return list of downstream nodes that may need this key.
    // If a node has any unexpired entry whose BF matches key, that node will appear in the result.
    std::vector<uint32_t> get(const std::string& key) {
        cleanupExpired();

        std::vector<uint32_t> matches;
        for (auto& [node_id, entry_list] : entries_) {
            for (const auto& entry : entry_list) {
                if (entry.bf.contains(key)) {
                    matches.push_back(node_id);
                    break;  // One match per node is enough
                }
            }
        }
        return matches;
    }

    // Explicitly clean up expired entries. Recommended to call periodically in scheduled tasks.
    void cleanup() {
        cleanupExpired();
    }

private:
    static constexpr std::chrono::milliseconds default_timeout_{500};

    std::unordered_map<uint32_t, std::vector<QueryEntry>> entries_;

    void cleanupExpired() {
        auto now = std::chrono::steady_clock::now();

        for (auto it = entries_.begin(); it != entries_.end();) {
            auto& entry_list = it->second;

            auto new_end = std::remove_if(
                entry_list.begin(), entry_list.end(),
                [&](const QueryEntry& entry) {
                    return (now - entry.created_at) > entry.timeout;
                });

            entry_list.erase(new_end, entry_list.end());

            if (entry_list.empty()) {
                it = entries_.erase(it);
            } else {
                ++it;
            }
        }
    }
};
