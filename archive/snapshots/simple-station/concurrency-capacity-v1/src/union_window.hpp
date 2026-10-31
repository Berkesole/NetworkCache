#pragma once
#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <unordered_map>
#include <vector>

#include "packet.hpp"

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
        std::vector<RequestPacket::KeySignature> key_signatures;
        uint32_t max_hop_count;
        size_t count;  // debug: how many constituents were merged
    };

    explicit UnionWindow(size_t bf_size = 1024, size_t bf_hash_count = 1)
        : bf_size_(bf_size), bf_hash_count_(bf_hash_count) {}

    // Stage a forwarded (hop_count already +1) REQUEST for a given egress.
    void stage(uint32_t egress_node_id, const BloomFilter& bf, uint32_t hop_count,
               const std::vector<RequestPacket::KeySignature>& key_signatures = {}) {
        if (bf.getBits().size() != bf_size_ || bf.getHashCount() != bf_hash_count_) {
            throw std::invalid_argument("UnionWindow: incompatible Bloom parameters");
        }
        const bool signed_request = !key_signatures.empty();
        if (signed_request) {
            if (bf_size_ != RequestPacket::kSignatureBloomBits ||
                bf_hash_count_ != RequestPacket::kSignatureHashCount) {
                throw std::invalid_argument("UnionWindow: signatures require 1024-bit/3-hash Bloom");
            }
            const auto canonical = RequestPacket::canonicalizeSignatures(key_signatures);
            if (RequestPacket::bloomForSignatures(canonical).getBits() != bf.getBits()) {
                throw std::invalid_argument("UnionWindow: Bloom bits do not match signatures");
            }
        }
        auto it = slots_.find(egress_node_id);
        if (it == slots_.end()) {
            Slot s;
            s.merged_bf = BloomFilter(bf_size_, bf_hash_count_);
            s.merged_bf.merge(bf);
            s.max_hop_count = hop_count;
            s.count = 1;
            s.signed_request = signed_request;
            s.key_signatures = key_signatures;
            slots_.emplace(egress_node_id, std::move(s));
        } else {
            if (it->second.signed_request != signed_request) {
                throw std::invalid_argument("UnionWindow: cannot mix signed and legacy requests");
            }
            it->second.merged_bf.merge(bf);
            if (hop_count > it->second.max_hop_count) {
                it->second.max_hop_count = hop_count;
            }
            it->second.count += 1;
            it->second.key_signatures.insert(it->second.key_signatures.end(),
                                             key_signatures.begin(), key_signatures.end());
        }
    }

    // Remove and return all staged slots. Caller emits aggregated packets from them.
    std::vector<FlushEntry> drain() {
        std::vector<FlushEntry> out;
        out.reserve(slots_.size());
        for (auto& [egress, slot] : slots_) {
            if (!slot.signed_request) {
                out.push_back(FlushEntry{
                    egress,
                    std::move(slot.merged_bf),
                    {},
                    slot.max_hop_count,
                    slot.count
                });
                continue;
            }

            auto signatures = RequestPacket::canonicalizeSignatures(slot.key_signatures);
            for (size_t offset = 0; offset < signatures.size();
                 offset += RequestPacket::kMaxKeySignatures) {
                const auto end = std::min(offset + RequestPacket::kMaxKeySignatures,
                                          signatures.size());
                std::vector<RequestPacket::KeySignature> chunk(
                    signatures.begin() + static_cast<std::ptrdiff_t>(offset),
                    signatures.begin() + static_cast<std::ptrdiff_t>(end)
                );
                out.push_back(FlushEntry{
                    egress,
                    RequestPacket::bloomForSignatures(chunk),
                    std::move(chunk),
                    slot.max_hop_count,
                    slot.count
                });
            }
        }
        slots_.clear();
        return out;
    }

    size_t size() const { return slots_.size(); }

private:
    struct Slot {
        BloomFilter merged_bf{1, 1};
        uint32_t max_hop_count = 0;
        size_t count = 0;
        bool signed_request = false;
        std::vector<RequestPacket::KeySignature> key_signatures;
    };

    size_t bf_size_;
    size_t bf_hash_count_;
    std::unordered_map<uint32_t, Slot> slots_;
};
