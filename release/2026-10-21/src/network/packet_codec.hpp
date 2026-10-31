#pragma once

#include "../packet.hpp"

#include <cstdint>
#include <memory>
#include <vector>

class PacketCodec {
public:
    static constexpr uint8_t kWireVersion = 1;

    static std::vector<uint8_t> encode(const Packet& packet);
    static std::unique_ptr<Packet> decode(const std::vector<uint8_t>& frame);
};
