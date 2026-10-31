#include "packet_codec.hpp"

#include <stdexcept>
#include <string>
#include <vector>

namespace {

void append_u8(std::vector<uint8_t>& buffer, uint8_t value) {
    buffer.push_back(value);
}

void append_u32(std::vector<uint8_t>& buffer, uint32_t value) {
    for (int shift = 24; shift >= 0; shift -= 8) {
        buffer.push_back(static_cast<uint8_t>((value >> shift) & 0xFF));
    }
}

void append_u64(std::vector<uint8_t>& buffer, uint64_t value) {
    for (int shift = 56; shift >= 0; shift -= 8) {
        buffer.push_back(static_cast<uint8_t>((value >> shift) & 0xFF));
    }
}

void append_bytes(std::vector<uint8_t>& buffer, const std::vector<uint8_t>& bytes) {
    buffer.insert(buffer.end(), bytes.begin(), bytes.end());
}

void append_string(std::vector<uint8_t>& buffer, const std::string& value) {
    append_u32(buffer, static_cast<uint32_t>(value.size()));
    buffer.insert(buffer.end(), value.begin(), value.end());
}

uint8_t read_u8(const std::vector<uint8_t>& buffer, size_t& offset) {
    if (offset >= buffer.size()) {
        throw std::runtime_error("PacketCodec: truncated frame while reading u8");
    }

    return buffer[offset++];
}

uint32_t read_u32(const std::vector<uint8_t>& buffer, size_t& offset) {
    if (offset + 4 > buffer.size()) {
        throw std::runtime_error("PacketCodec: truncated frame while reading u32");
    }

    uint32_t value = 0;
    for (int i = 0; i < 4; ++i) {
        value = (value << 8) | buffer[offset++];
    }
    return value;
}

uint64_t read_u64(const std::vector<uint8_t>& buffer, size_t& offset) {
    if (offset + 8 > buffer.size()) {
        throw std::runtime_error("PacketCodec: truncated frame while reading u64");
    }

    uint64_t value = 0;
    for (int i = 0; i < 8; ++i) {
        value = (value << 8) | buffer[offset++];
    }
    return value;
}

std::string read_string(const std::vector<uint8_t>& buffer, size_t& offset) {
    const auto size = read_u32(buffer, offset);
    if (offset + size > buffer.size()) {
        throw std::runtime_error("PacketCodec: truncated frame while reading string");
    }

    std::string value(buffer.begin() + static_cast<std::ptrdiff_t>(offset),
                      buffer.begin() + static_cast<std::ptrdiff_t>(offset + size));
    offset += size;
    return value;
}

std::vector<uint8_t> read_bytes(const std::vector<uint8_t>& buffer, size_t& offset, size_t size) {
    if (offset + size > buffer.size()) {
        throw std::runtime_error("PacketCodec: truncated frame while reading bytes");
    }

    std::vector<uint8_t> value(buffer.begin() + static_cast<std::ptrdiff_t>(offset),
                               buffer.begin() + static_cast<std::ptrdiff_t>(offset + size));
    offset += size;
    return value;
}

void encode_packet_header(std::vector<uint8_t>& frame, const Packet& packet) {
    append_u8(frame, PacketCodec::kWireVersion);
    append_u8(frame, static_cast<uint8_t>(packet.type));
    append_u64(frame, packet.request_id);
    append_u32(frame, packet.src_node_id);
    append_u32(frame, packet.dest_node_id);
    append_u32(frame, packet.hop_count);
}

void decode_packet_header(const std::vector<uint8_t>& frame,
                          size_t& offset,
                          PacketType expected_type,
                          Packet& packet) {
    const auto version = read_u8(frame, offset);
    if (version != PacketCodec::kWireVersion) {
        throw std::runtime_error("PacketCodec: unsupported wire version");
    }

    const auto actual_type = static_cast<PacketType>(read_u8(frame, offset));
    if (actual_type != expected_type) {
        throw std::runtime_error("PacketCodec: packet type mismatch while decoding header");
    }

    packet.request_id = read_u64(frame, offset);
    packet.src_node_id = read_u32(frame, offset);
    packet.dest_node_id = read_u32(frame, offset);
    packet.hop_count = read_u32(frame, offset);
}

void encode_request_payload(std::vector<uint8_t>& frame, const RequestPacket& packet) {
    const auto& bits = packet.bf.getBits();
    append_u32(frame, static_cast<uint32_t>(bits.size()));
    for (bool bit : bits) {
        append_u8(frame, bit ? 1U : 0U);
    }
}

std::unique_ptr<RequestPacket> decode_request_packet(const std::vector<uint8_t>& frame, size_t& offset) {
    auto packet = std::make_unique<RequestPacket>();
    decode_packet_header(frame, offset, PacketType::REQUEST, *packet);

    const auto bit_count = read_u32(frame, offset);
    std::vector<bool> bits;
    bits.reserve(bit_count);
    for (uint32_t i = 0; i < bit_count; ++i) {
        bits.push_back(read_u8(frame, offset) != 0);
    }

    BloomFilter bloom_filter(bit_count == 0 ? 1U : bit_count);
    bloom_filter.setBits(bits);
    packet->bf = bloom_filter;
    return packet;
}

void encode_data_payload(std::vector<uint8_t>& frame, const DataPacket& packet) {
    append_string(frame, packet.key);
    append_u32(frame, static_cast<uint32_t>(packet.value.size()));
    append_bytes(frame, packet.value.data());
}

std::unique_ptr<DataPacket> decode_data_packet(const std::vector<uint8_t>& frame, size_t& offset) {
    auto packet = std::make_unique<DataPacket>();
    decode_packet_header(frame, offset, PacketType::DATA, *packet);
    packet->key = read_string(frame, offset);
    const auto value_size = read_u32(frame, offset);
    packet->value = Value(read_bytes(frame, offset, value_size));
    return packet;
}

}  // namespace

std::vector<uint8_t> PacketCodec::encode(const Packet& packet) {
    std::vector<uint8_t> frame;
    encode_packet_header(frame, packet);

    switch (packet.type) {
        case PacketType::REQUEST: {
            const auto* request_packet = dynamic_cast<const RequestPacket*>(&packet);
            if (!request_packet) {
                throw std::runtime_error("PacketCodec: REQUEST packet type mismatch");
            }
            encode_request_payload(frame, *request_packet);
            return frame;
        }
        case PacketType::DATA: {
            const auto* data_packet = dynamic_cast<const DataPacket*>(&packet);
            if (!data_packet) {
                throw std::runtime_error("PacketCodec: DATA packet type mismatch");
            }
            encode_data_payload(frame, *data_packet);
            return frame;
        }
        default:
            throw std::runtime_error("PacketCodec: unsupported packet type");
    }
}

std::unique_ptr<Packet> PacketCodec::decode(const std::vector<uint8_t>& frame) {
    if (frame.size() < 2) {
        throw std::runtime_error("PacketCodec: frame too small");
    }

    size_t offset = 0;
    const auto version = read_u8(frame, offset);
    if (version != kWireVersion) {
        throw std::runtime_error("PacketCodec: unsupported wire version");
    }

    const auto type = static_cast<PacketType>(read_u8(frame, offset));
    offset = 0;

    switch (type) {
        case PacketType::REQUEST:
            return decode_request_packet(frame, offset);
        case PacketType::DATA:
            return decode_data_packet(frame, offset);
        default:
            throw std::runtime_error("PacketCodec: unsupported packet type");
    }
}
