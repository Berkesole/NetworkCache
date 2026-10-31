#pragma once

#include "../packet.hpp"

#include <cstdint>
#include <cstddef>
#include <memory>
#include <vector>

class PacketCodec {
public:
    static constexpr uint8_t kLegacyWireVersion = 1;
    static constexpr uint8_t kBitPackedBloomWireVersion = 2;
    static constexpr uint8_t kSignedRequestWireVersion = 3;
    static constexpr uint8_t kWireVersion = kLegacyWireVersion;

    static std::vector<uint8_t> encode(const Packet& packet);
    static std::vector<uint8_t> encode(const Packet& packet, uint8_t wire_version);
    static std::unique_ptr<Packet> decode(const std::vector<uint8_t>& frame);
    static uint8_t configured_wire_version();
    static std::size_t configured_bloom_hash_count();
};
