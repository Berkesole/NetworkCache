#include "packet_codec.hpp"

#include <cstdlib>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void append_u8(std::vector<uint8_t>& buffer, uint8_t value) {
    buffer.push_back(value);
}

void append_u16(std::vector<uint8_t>& buffer, uint16_t value) {
    buffer.push_back(static_cast<uint8_t>((value >> 8U) & 0xFFU));
    buffer.push_back(static_cast<uint8_t>(value & 0xFFU));
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

uint16_t read_u16(const std::vector<uint8_t>& buffer, size_t& offset) {
    if (offset + 2 > buffer.size()) {
        throw std::runtime_error("PacketCodec: truncated frame while reading u16");
    }
    uint16_t value = static_cast<uint16_t>(buffer[offset++]) << 8U;
    value |= static_cast<uint16_t>(buffer[offset++]);
    return value;
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

void encode_packet_header(std::vector<uint8_t>& frame, const Packet& packet,
                          uint8_t wire_version) {
    append_u8(frame, wire_version);
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
    if (version != PacketCodec::kLegacyWireVersion &&
        version != PacketCodec::kBitPackedBloomWireVersion &&
        version != PacketCodec::kSignedRequestWireVersion) {
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

void encode_request_payload(std::vector<uint8_t>& frame, const RequestPacket& packet,
                            uint8_t wire_version) {
    const auto& bits = packet.bf.getBits();
    append_u32(frame, static_cast<uint32_t>(bits.size()));
    if (wire_version == PacketCodec::kBitPackedBloomWireVersion ||
        wire_version == PacketCodec::kSignedRequestWireVersion) {
        for (size_t offset = 0; offset < bits.size(); offset += 8) {
            uint8_t packed = 0;
            for (size_t bit = 0; bit < 8 && offset + bit < bits.size(); ++bit) {
                if (bits[offset + bit]) {
                    packed |= static_cast<uint8_t>(1U << (7U - bit));
                }
            }
            append_u8(frame, packed);
        }
    } else {
        for (bool bit : bits) {
            append_u8(frame, bit ? 1U : 0U);
        }
    }

    if (wire_version != PacketCodec::kSignedRequestWireVersion) return;

    if (bits.size() != RequestPacket::kSignatureBloomBits ||
        packet.bf.getHashCount() != RequestPacket::kSignatureHashCount) {
        throw std::runtime_error("PacketCodec: wire v3 requires 1024-bit/3-hash Bloom filter");
    }
    const auto signatures = RequestPacket::canonicalizeSignatures(packet.key_signatures);
    if (signatures.empty() || signatures.size() > RequestPacket::kMaxKeySignatures) {
        throw std::runtime_error("PacketCodec: wire v3 requires 1..256 key signatures");
    }
    if (RequestPacket::bloomForSignatures(signatures).getBits() != bits) {
        throw std::runtime_error("PacketCodec: wire v3 Bloom bits do not match key signatures");
    }

    uint8_t flags = 0;
    if (packet.is_aggregated) flags |= 0x01U;
    if (packet.is_fallback) flags |= 0x02U;
    append_u8(frame, flags);
    append_u8(frame, static_cast<uint8_t>(packet.bf.getHashCount()));
    append_u16(frame, static_cast<uint16_t>(signatures.size()));
    for (const auto signature : signatures) append_u32(frame, signature);
}

std::unique_ptr<RequestPacket> decode_request_packet(const std::vector<uint8_t>& frame, size_t& offset) {
    auto packet = std::make_unique<RequestPacket>();
    if (offset >= frame.size()) {
        throw std::runtime_error("PacketCodec: missing wire version");
    }
    const uint8_t version = frame[offset];
    decode_packet_header(frame, offset, PacketType::REQUEST, *packet);

    const auto bit_count = read_u32(frame, offset);
    std::vector<bool> bits;
    bits.reserve(bit_count);
    if (version == PacketCodec::kBitPackedBloomWireVersion ||
        version == PacketCodec::kSignedRequestWireVersion) {
        const uint32_t packed_count = (bit_count + 7U) / 8U;
        for (uint32_t byte_index = 0; byte_index < packed_count; ++byte_index) {
            const uint8_t packed = read_u8(frame, offset);
            for (uint32_t bit = 0; bit < 8U && bits.size() < bit_count; ++bit) {
                bits.push_back((packed & static_cast<uint8_t>(1U << (7U - bit))) != 0);
            }
        }
    } else {
        for (uint32_t i = 0; i < bit_count; ++i) {
            bits.push_back(read_u8(frame, offset) != 0);
        }
    }

    size_t hash_count = 1;
    if (version == PacketCodec::kSignedRequestWireVersion) {
        const uint8_t flags = read_u8(frame, offset);
        if ((flags & 0xFCU) != 0) {
            throw std::runtime_error("PacketCodec: wire v3 REQUEST has reserved flags set");
        }
        packet->is_aggregated = (flags & 0x01U) != 0;
        packet->is_fallback = (flags & 0x02U) != 0;
        hash_count = read_u8(frame, offset);
        if (bit_count != RequestPacket::kSignatureBloomBits ||
            hash_count != RequestPacket::kSignatureHashCount) {
            throw std::runtime_error("PacketCodec: wire v3 requires 1024-bit/3-hash Bloom filter");
        }
        const auto signature_count = read_u16(frame, offset);
        if (signature_count == 0 || signature_count > RequestPacket::kMaxKeySignatures) {
            throw std::runtime_error("PacketCodec: wire v3 requires 1..256 key signatures");
        }
        packet->key_signatures.reserve(signature_count);
        for (uint16_t index = 0; index < signature_count; ++index) {
            const auto signature = read_u32(frame, offset);
            if (!RequestPacket::isValidSignature(signature)) {
                throw std::runtime_error("PacketCodec: wire v3 signature has reserved bits set");
            }
            packet->key_signatures.push_back(signature);
        }
        const auto canonical = RequestPacket::canonicalizeSignatures(packet->key_signatures);
        if (canonical != packet->key_signatures) {
            throw std::runtime_error("PacketCodec: wire v3 signatures are not canonical");
        }
    } else {
        hash_count = PacketCodec::configured_bloom_hash_count();
    }

    BloomFilter bloom_filter(bit_count == 0 ? 1U : bit_count, hash_count);
    bloom_filter.setBits(bits);
    packet->bf = bloom_filter;
    if (version == PacketCodec::kSignedRequestWireVersion &&
        RequestPacket::bloomForSignatures(packet->key_signatures).getBits() != bits) {
        throw std::runtime_error("PacketCodec: wire v3 Bloom bits do not match key signatures");
    }
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

uint8_t PacketCodec::configured_wire_version() {
    const char* raw = std::getenv("RSYNC_WIRE_VERSION");
    if (raw == nullptr || raw[0] == '\0' || std::string(raw) == "1") {
        return kLegacyWireVersion;
    }
    if (std::string(raw) == "2") {
        return kBitPackedBloomWireVersion;
    }
    if (std::string(raw) == "3") {
        return kSignedRequestWireVersion;
    }
    throw std::runtime_error("PacketCodec: RSYNC_WIRE_VERSION must be 1, 2, or 3");
}

std::size_t PacketCodec::configured_bloom_hash_count() {
    const char* raw = std::getenv("RSYNC_BLOOM_HASH_COUNT");
    if (raw == nullptr || raw[0] == '\0') return 1;
    const std::string value(raw);
    if (value == "1") return 1;
    if (value == "3") return 3;
    throw std::runtime_error("PacketCodec: RSYNC_BLOOM_HASH_COUNT must be 1 or 3");
}

std::vector<uint8_t> PacketCodec::encode(const Packet& packet) {
    return encode(packet, configured_wire_version());
}

std::vector<uint8_t> PacketCodec::encode(const Packet& packet, uint8_t wire_version) {
    if (wire_version != kLegacyWireVersion &&
        wire_version != kBitPackedBloomWireVersion &&
        wire_version != kSignedRequestWireVersion) {
        throw std::runtime_error("PacketCodec: unsupported wire version");
    }
    std::vector<uint8_t> frame;
    encode_packet_header(frame, packet, wire_version);

    switch (packet.type) {
        case PacketType::REQUEST: {
            const auto* request_packet = dynamic_cast<const RequestPacket*>(&packet);
            if (!request_packet) {
                throw std::runtime_error("PacketCodec: REQUEST packet type mismatch");
            }
            encode_request_payload(frame, *request_packet, wire_version);
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
    if (version != kLegacyWireVersion &&
        version != kBitPackedBloomWireVersion &&
        version != kSignedRequestWireVersion) {
        throw std::runtime_error("PacketCodec: unsupported wire version");
    }

    const auto type = static_cast<PacketType>(read_u8(frame, offset));
    offset = 0;

    std::unique_ptr<Packet> packet;
    switch (type) {
        case PacketType::REQUEST:
            packet = decode_request_packet(frame, offset);
            break;
        case PacketType::DATA:
            packet = decode_data_packet(frame, offset);
            break;
        default:
            throw std::runtime_error("PacketCodec: unsupported packet type");
    }
    if (version == kSignedRequestWireVersion && offset != frame.size()) {
        throw std::runtime_error("PacketCodec: trailing bytes in wire v3 frame");
    }
    return packet;
}
