#pragma once
#include <cstdint>
#include <unordered_map>
#include <vector>

#include "bloomfilter.hpp"

// UnionWindow: per-egress staging for single-layer REQUEST aggregation.
//
// Semantics:
// - Each egress neighbor has at most one open slot.
// - A staged pkt carries its *forwarded* hop_count (already incremented by the
//   caller). merged.hop_count = max(staged.hop_count) preserves the TTL hard
//   bound (spec I5, §3.5 step 6).
// - There is NO time-based flush. The "batch" is defined by the caller: whatever
//   was staged between two drain() calls gets merged into one aggregate per
//   egress. In the mock this batch = packets drained from inbox in one
//   processOne() call.
// - Caller is responsible for constructing and sending the aggregated packet
//   from a FlushEntry; UnionWindow does not touch the network layer.
class UnionWindow {
public:
    struct FlushEntry {
        uint32_t egress_node_id;
        BloomFilter merged_bf;
        uint32_t max_hop_count;
        size_t count;  // debug: how many constituents were merged
    };

    explicit UnionWindow(size_t bf_size = 1024) : bf_size_(bf_size) {}

    // Stage a forwarded (hop_count already +1) REQUEST for a given egress.
    void stage(uint32_t egress_node_id, const BloomFilter& bf, uint32_t hop_count) {
        auto it = slots_.find(egress_node_id);
        if (it == slots_.end()) {
            Slot s;
            s.merged_bf = BloomFilter(bf_size_);
            s.merged_bf.merge(bf);
            s.max_hop_count = hop_count;
            s.count = 1;
            slots_.emplace(egress_node_id, std::move(s));
        } else {
            it->second.merged_bf.merge(bf);
            if (hop_count > it->second.max_hop_count) {
                it->second.max_hop_count = hop_count;
            }
            it->second.count += 1;
        }
    }

    // Remove and return all staged slots. Caller emits aggregated packets from them.
    std::vector<FlushEntry> drain() {
        std::vector<FlushEntry> out;
        out.reserve(slots_.size());
        for (auto& [egress, slot] : slots_) {
            out.push_back(FlushEntry{
                egress,
                std::move(slot.merged_bf),
                slot.max_hop_count,
                slot.count
            });
        }
        slots_.clear();
        return out;
    }

    size_t size() const { return slots_.size(); }

private:
    struct Slot {
        BloomFilter merged_bf{0};
        uint32_t max_hop_count = 0;
        size_t count = 0;
    };

    size_t bf_size_;
    std::unordered_map<uint32_t, Slot> slots_;
};
