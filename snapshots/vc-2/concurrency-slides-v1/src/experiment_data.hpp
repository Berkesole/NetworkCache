#pragma once

#include "value.hpp"

#include <cstdint>
#include <string>
#include <vector>

namespace rsync_experiment {

inline constexpr uint64_t kFnvOffset = 14695981039346656037ULL;
inline constexpr uint64_t kFnvPrime = 1099511628211ULL;

inline uint64_t fnv1a64(const std::vector<uint8_t>& bytes) {
    uint64_t hash = kFnvOffset;
    for (uint8_t byte : bytes) {
        hash ^= byte;
        hash *= kFnvPrime;
    }
    return hash;
}

inline uint64_t fnv1a64(const Value& value) {
    return fnv1a64(value.data());
}

inline uint64_t xorshift64(uint64_t& state) {
    if (state == 0) state = 0x9e3779b97f4a7c15ULL;
    state ^= state << 13U;
    state ^= state >> 7U;
    state ^= state << 17U;
    return state;
}

inline Value deterministic_value(std::size_t size, uint64_t seed) {
    std::vector<uint8_t> bytes(size);
    uint64_t state = seed;
    for (std::size_t index = 0; index < size; ++index) {
        bytes[index] = static_cast<uint8_t>(xorshift64(state) & 0xffU);
    }
    return Value(std::move(bytes));
}

inline std::string hex64(uint64_t value) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string output(16, '0');
    for (int index = 15; index >= 0; --index) {
        output[static_cast<std::size_t>(index)] = digits[value & 0xfU];
        value >>= 4U;
    }
    return output;
}

}  // namespace rsync_experiment
