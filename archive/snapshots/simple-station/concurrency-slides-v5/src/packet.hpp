#pragma once
#include <algorithm>
#include <array>
#include <vector>
#include <cstdint>
#include <memory>
#include <string>
#include <atomic>
#include <stdexcept>
#include "bloomfilter.hpp"
#include "value.hpp"

enum class PacketType : uint8_t {
    REQUEST = 1,
    DATA = 2
    // SYNC is deprecated
};

inline constexpr uint32_t kBroadcastNodeId = 0;

// Generate unique request_id (atomic increment)
inline uint64_t generateRequestId() {
    static std::atomic<uint64_t> counter{1};
    return counter.fetch_add(1, std::memory_order_relaxed);
}

// Packet base class
class Packet {
public:
    PacketType type;
    uint64_t request_id;
    uint32_t src_node_id;
    uint32_t dest_node_id;
    uint32_t hop_count = 0;

    virtual ~Packet() = default;
    // Polymorphic clone interface, used to copy packets without knowing the specific subclass
    virtual std::unique_ptr<Packet> clone() const = 0;

protected:
    Packet(PacketType t) : type(t), request_id(0),
                           src_node_id(0), dest_node_id(0) {}
};

// RequestPacket subclass
class RequestPacket : public Packet {
public:
    using KeySignature = uint32_t;

    static constexpr size_t kSignatureBloomBits = 1024;
    static constexpr size_t kSignatureHashCount = 3;
    static constexpr size_t kMaxKeySignatures = 256;

    BloomFilter bf;
    bool is_aggregated = false;  // single-layer union enforcement
    bool is_fallback = false;    // staged fallback ignores SyncTable routing
    std::vector<KeySignature> key_signatures;

    RequestPacket() : Packet(PacketType::REQUEST) {}

    std::unique_ptr<Packet> clone() const override {
        return std::make_unique<RequestPacket>(*this);
    }

    static std::unique_ptr<RequestPacket> create(
        uint32_t src_node_id,
        const std::vector<std::string>& keys,
        size_t bloom_bits = 1024,
        size_t bloom_hash_count = 1
    );

    // A wire-v3 key signature stores three 10-bit Bloom positions in one
    // uint32_t.  The two most-significant bits are reserved and must be zero.
    static KeySignature signatureForKey(const std::string& key) {
        const BloomFilter bloom(kSignatureBloomBits, kSignatureHashCount);
        const auto positions = bloom.positions(key);
        if (positions.size() != kSignatureHashCount) {
            throw std::logic_error("RequestPacket: unexpected Bloom signature width");
        }
        return packSignature({positions[0], positions[1], positions[2]});
    }

    static KeySignature packSignature(
        const std::array<size_t, kSignatureHashCount>& positions
    ) {
        for (const auto position : positions) {
            if (position >= kSignatureBloomBits) {
                throw std::invalid_argument("RequestPacket: signature position exceeds 10 bits");
            }
        }
        return (static_cast<KeySignature>(positions[0]) << 20U) |
               (static_cast<KeySignature>(positions[1]) << 10U) |
               static_cast<KeySignature>(positions[2]);
    }

    static std::array<size_t, kSignatureHashCount> signaturePositions(KeySignature signature) {
        if (!isValidSignature(signature)) {
            throw std::invalid_argument("RequestPacket: signature has reserved bits set");
        }
        return {
            static_cast<size_t>((signature >> 20U) & 0x3FFU),
            static_cast<size_t>((signature >> 10U) & 0x3FFU),
            static_cast<size_t>(signature & 0x3FFU),
        };
    }

    static bool isValidSignature(KeySignature signature) {
        return (signature & 0xC0000000U) == 0;
    }

    static bool signatureMatchesBloom(KeySignature signature, const BloomFilter& bloom) {
        if (bloom.getBits().size() != kSignatureBloomBits ||
            bloom.getHashCount() != kSignatureHashCount ||
            !isValidSignature(signature)) {
            return false;
        }
        const auto positions = signaturePositions(signature);
        const auto& bits = bloom.getBits();
        return bits[positions[0]] && bits[positions[1]] && bits[positions[2]];
    }

    static BloomFilter bloomForSignatures(const std::vector<KeySignature>& signatures) {
        BloomFilter bloom(kSignatureBloomBits, kSignatureHashCount);
        std::vector<bool> bits(kSignatureBloomBits, false);
        for (const auto signature : signatures) {
            const auto positions = signaturePositions(signature);
            for (const auto position : positions) bits[position] = true;
        }
        bloom.setBits(bits);
        return bloom;
    }

    static std::vector<KeySignature> canonicalizeSignatures(
        const std::vector<KeySignature>& signatures
    ) {
        auto canonical = signatures;
        for (const auto signature : canonical) {
            if (!isValidSignature(signature)) {
                throw std::invalid_argument("RequestPacket: signature has reserved bits set");
            }
        }
        std::sort(canonical.begin(), canonical.end());
        canonical.erase(std::unique(canonical.begin(), canonical.end()), canonical.end());
        return canonical;
    }
};

// DataPacket subclass
class DataPacket : public Packet {
public:
    std::string key;
    Value value;

    DataPacket() : Packet(PacketType::DATA) {}

     std::unique_ptr<Packet> clone() const override {
         return std::make_unique<DataPacket>(*this);
     }

    static std::unique_ptr<DataPacket> create(
        uint64_t request_id,
        uint32_t src_node_id,
        uint32_t dest_node_id,
        const std::string& key,
        Value value
    );
};
