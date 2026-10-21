#pragma once
#include <vector>
#include <cstdint>
#include <memory>
#include <string>
#include <atomic>
#include "bloomfilter.hpp"
#include "value.hpp"

enum class PacketType : uint8_t {
    REQUEST = 1,
    DATA = 2
    // SYNC is deprecated
};

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
    BloomFilter bf;
    bool is_aggregated = false;  // single-layer union enforcement

    RequestPacket() : Packet(PacketType::REQUEST) {}

    std::unique_ptr<Packet> clone() const override {
        return std::make_unique<RequestPacket>(*this);
    }

    static std::unique_ptr<RequestPacket> create(
        uint32_t src_node_id,
        const std::vector<std::string>& keys
    );
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
