#pragma once
#include <vector>
#include <string>
#include <functional>

class BloomFilter {
public:
    // TODO: How to reclaim BF memory?
    BloomFilter(size_t size = 1024) : bits_(size, false) {}

    // TODO:
    // 1. Multiple hash functions
    // 2. Random seed configuration
    // 3. Need to support various BF operations: merge, query, return status, etc.
    void add(const std::string& key) {
        size_t hash = std::hash<std::string>{}(key);
        bits_[hash % bits_.size()] = true;
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
        size_t hash = std::hash<std::string>{}(key);
        return bits_[hash % bits_.size()];
    }

    // Merge (bitwise OR)
    // TODO: Can merge cause overflow? How to handle?
    void merge(const BloomFilter& other) {
        // Demo simplified: in real scenarios, can we merge even if sizes differ?
        if (other.bits_.size() != bits_.size()) return; 
        for (size_t i = 0; i < bits_.size(); ++i) {
            bits_[i] = bits_[i] || other.bits_[i];
        }
    }

    const std::vector<bool>& getBits() const { 
        return bits_; 
    }
    
    void setBits(const std::vector<bool>& bits) { 
        bits_ = bits; 
    }

private:
    std::vector<bool> bits_;
};