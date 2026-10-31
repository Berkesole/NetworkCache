#pragma once
#include <array>
#include <vector>
#include <string>
#include <functional>
#include <cstdint>
#include <stdexcept>

class BloomFilter {
public:
    BloomFilter(size_t size = 1024, size_t hash_count = 1)
        : bits_(size, false), hash_count_(hash_count) {
        if (size == 0 || hash_count == 0 || hash_count > kHashSalts.size()) {
            throw std::invalid_argument("BloomFilter: invalid size or hash count");
        }
    }

    // TODO:
    // 1. Multiple hash functions
    // 2. Random seed configuration
    // 3. Need to support various BF operations: merge, query, return status, etc.
    void add(const std::string& key) {
        for (const auto position : positions(key)) bits_[position] = true;
    }

    // TODO:
    // Is there a more efficient parallel insertion method, replace this serial insertion method
    // 1. SIMD or AVX2 instructions
    // 2. DPU or NIC
    void batch_add(const std::vector<std::string>& keys) {
        for (const auto& key : keys) {
            add(key);  // Fixed: bf.add(key) → add(key)
        }
    }

    bool contains(const std::string& key) const {
        for (const auto position : positions(key)) {
            if (!bits_[position]) return false;
        }
        return true;
    }

    // Return true when every bit required by another Bloom filter is present.
    // This is the matching rule used by the single-key 3-hash pilot.
    bool containsAllSetBits(const BloomFilter& required) const {
        if (bits_.size() != required.bits_.size() ||
            hash_count_ != required.hash_count_) {
            return false;
        }
        for (size_t i = 0; i < bits_.size(); ++i) {
            if (required.bits_[i] && !bits_[i]) return false;
        }
        return true;
    }

    // Merge (bitwise OR)
    // TODO: Can merge cause overflow? How to handle?
    void merge(const BloomFilter& other) {
        // Demo simplified: in real scenarios, can we merge even if sizes differ?
        if (other.bits_.size() != bits_.size() || other.hash_count_ != hash_count_) return;
        for (size_t i = 0; i < bits_.size(); ++i) {
            bits_[i] = bits_[i] || other.bits_[i];
        }
    }

    const std::vector<bool>& getBits() const { 
        return bits_; 
    }

    size_t getHashCount() const { return hash_count_; }

    std::vector<size_t> positions(const std::string& key) const {
        if (hash_count_ == 1) {
            return {std::hash<std::string>{}(key) % bits_.size()};
        }
        std::vector<size_t> output;
        output.reserve(hash_count_);
        for (size_t index = 0; index < hash_count_; ++index) {
            output.push_back(static_cast<size_t>(seededFnv1a64(key, kHashSalts[index]) % bits_.size()));
        }
        return output;
    }
    
    void setBits(const std::vector<bool>& bits) { 
        bits_ = bits; 
    }

private:
    static constexpr std::array<uint64_t, 3> kHashSalts = {
        0x9e3779b97f4a7c15ULL,
        0xbf58476d1ce4e5b9ULL,
        0x94d049bb133111ebULL,
    };

    static uint64_t seededFnv1a64(const std::string& key, uint64_t salt) {
        uint64_t value = 14695981039346656037ULL ^ salt;
        for (const unsigned char byte : key) {
            value ^= byte;
            value *= 1099511628211ULL;
        }
        return value;
    }

    std::vector<bool> bits_;
    size_t hash_count_;
};
