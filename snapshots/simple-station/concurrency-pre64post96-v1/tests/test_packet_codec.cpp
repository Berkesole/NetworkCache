#include "network/packet_codec.hpp"
#include "experiment_data.hpp"
#include "union_window.hpp"

#include <algorithm>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << "\n";
        std::exit(1);
    }
}

void require_throws(const std::function<void()>& operation, const char* message) {
    try {
        operation();
    } catch (const std::exception&) {
        return;
    }
    require(false, message);
}

std::vector<RequestPacket::KeySignature> distinct_signatures(std::size_t count) {
    std::set<RequestPacket::KeySignature> unique;
    for (std::size_t index = 0; unique.size() < count; ++index) {
        unique.insert(RequestPacket::signatureForKey("wire-v3-key-" + std::to_string(index)));
    }
    return {unique.begin(), unique.end()};
}

void assert_round_trip(uint8_t version, std::size_t expected_size,
                       std::size_t bloom_bits = 1024, std::size_t hash_count = 1) {
    if (hash_count == 3) setenv("RSYNC_BLOOM_HASH_COUNT", "3", 1);
    else unsetenv("RSYNC_BLOOM_HASH_COUNT");
    auto request = RequestPacket::create(7, {"alpha", "beta", "gamma"}, bloom_bits, hash_count);
    request->dest_node_id = 9;
    request->hop_count = 3;
    const auto expected_bits = request->bf.getBits();
    const auto encoded = PacketCodec::encode(*request, version);
    require(encoded.size() == expected_size, "unexpected REQUEST frame size");
    require(encoded.front() == version, "wire version was not encoded");

    auto decoded_base = PacketCodec::decode(encoded);
    auto* decoded = dynamic_cast<RequestPacket*>(decoded_base.get());
    require(decoded != nullptr, "decoded packet is not REQUEST");
    require(decoded->request_id == request->request_id, "request id changed");
    require(decoded->src_node_id == 7 && decoded->dest_node_id == 9, "endpoints changed");
    require(decoded->hop_count == 3, "hop count changed");
    require(decoded->bf.getBits() == expected_bits, "Bloom bits changed");
    require(decoded->bf.getHashCount() == hash_count, "Bloom hash count changed");
}

void assert_v3_round_trip(std::size_t signature_count, bool aggregated, bool fallback) {
    RequestPacket request;
    request.request_id = 0x800700000000002AULL;
    request.src_node_id = 7;
    request.dest_node_id = 9;
    request.hop_count = 3;
    request.is_aggregated = aggregated;
    request.is_fallback = fallback;
    request.key_signatures = distinct_signatures(signature_count);
    request.bf = RequestPacket::bloomForSignatures(request.key_signatures);

    const auto encoded = PacketCodec::encode(request, PacketCodec::kSignedRequestWireVersion);
    require(encoded.size() == 158 + 4 * signature_count,
            "unexpected wire-v3 REQUEST frame size");
    auto decoded_base = PacketCodec::decode(encoded);
    auto* decoded = dynamic_cast<RequestPacket*>(decoded_base.get());
    require(decoded != nullptr, "wire-v3 decoded packet is not REQUEST");
    require(decoded->request_id == request.request_id, "wire-v3 request id changed");
    require(decoded->src_node_id == 7 && decoded->dest_node_id == 9,
            "wire-v3 endpoints changed");
    require(decoded->hop_count == 3, "wire-v3 hop count changed");
    require(decoded->is_aggregated == aggregated, "wire-v3 aggregated flag changed");
    require(decoded->is_fallback == fallback, "wire-v3 fallback flag changed");
    require(decoded->bf.getBits() == request.bf.getBits(), "wire-v3 Bloom bits changed");
    require(decoded->bf.getHashCount() == 3, "wire-v3 hash count changed");
    require(decoded->key_signatures == request.key_signatures,
            "wire-v3 signatures changed");
}

void assert_signature_helpers() {
    const auto signature = RequestPacket::signatureForKey("alpha");
    const auto positions = RequestPacket::signaturePositions(signature);
    const BloomFilter reference(1024, 3);
    const auto expected = reference.positions("alpha");
    require(positions[0] == expected[0] && positions[1] == expected[1] &&
                positions[2] == expected[2],
            "signature positions differ from BloomFilter positions");
    // Shared with generate_latency_v4_bf1024_h3_c512.py. This fixed vector
    // makes a silent Python/C++ hash drift fail in both experiment preflights.
    require(positions[0] == 900 && positions[1] == 384 && positions[2] == 790,
            "wire-v3 FNV-1a signature test vector changed");
    require(RequestPacket::packSignature(positions) == signature,
            "signature pack/unpack changed value");

    const auto bloom = RequestPacket::bloomForSignatures({signature});
    require(RequestPacket::signatureMatchesBloom(signature, bloom),
            "signature does not match reconstructed Bloom filter");
    auto missing_bits = bloom.getBits();
    missing_bits[positions[0]] = false;
    BloomFilter partial(1024, 3);
    partial.setBits(missing_bits);
    require(!RequestPacket::signatureMatchesBloom(signature, partial),
            "partial three-position match was accepted");
    require_throws(
        [] { RequestPacket::signaturePositions(0xC0000000U); },
        "signature reserved bits were accepted"
    );
}

void assert_v3_rejects_invalid_frames() {
    RequestPacket request;
    request.key_signatures = distinct_signatures(1);
    request.bf = RequestPacket::bloomForSignatures(request.key_signatures);
    const auto valid = PacketCodec::encode(request, PacketCodec::kSignedRequestWireVersion);

    auto reserved_flags = valid;
    reserved_flags[154] |= 0x80U;
    require_throws([&] { PacketCodec::decode(reserved_flags); },
                   "wire-v3 reserved flags were accepted");

    auto wrong_hash_count = valid;
    wrong_hash_count[155] = 1;
    require_throws([&] { PacketCodec::decode(wrong_hash_count); },
                   "wire-v3 wrong hash count was accepted");

    auto reserved_signature = valid;
    reserved_signature[158] |= 0x80U;
    require_throws([&] { PacketCodec::decode(reserved_signature); },
                   "wire-v3 reserved signature bits were accepted");

    auto inconsistent_bloom = valid;
    inconsistent_bloom[26] ^= 0x80U;
    require_throws([&] { PacketCodec::decode(inconsistent_bloom); },
                   "wire-v3 inconsistent Bloom/signatures were accepted");

    auto trailing = valid;
    trailing.push_back(0);
    require_throws([&] { PacketCodec::decode(trailing); },
                   "wire-v3 trailing payload was accepted");

    request.key_signatures.clear();
    request.bf = BloomFilter(1024, 3);
    require_throws(
        [&] { PacketCodec::encode(request, PacketCodec::kSignedRequestWireVersion); },
        "wire-v3 empty signature set was accepted"
    );

    request.key_signatures = distinct_signatures(257);
    request.bf = RequestPacket::bloomForSignatures(request.key_signatures);
    require_throws(
        [&] { PacketCodec::encode(request, PacketCodec::kSignedRequestWireVersion); },
        "wire-v3 oversized signature set was accepted"
    );
}

void assert_union_window_signed_aggregation() {
    UnionWindow window(1024, 3);
    const auto signatures = distinct_signatures(3);
    window.stage(9, RequestPacket::bloomForSignatures({signatures[2], signatures[0]}), 2,
                 {signatures[2], signatures[0]});
    window.stage(9, RequestPacket::bloomForSignatures({signatures[1], signatures[0]}), 5,
                 {signatures[1], signatures[0]});
    const auto entries = window.drain();
    require(entries.size() == 1, "signed aggregation produced extra chunks");
    require(entries[0].egress_node_id == 9, "signed aggregation changed egress");
    require(entries[0].key_signatures == signatures,
            "signed aggregation did not sort/deduplicate signatures");
    require(entries[0].merged_bf.getBits() ==
                RequestPacket::bloomForSignatures(signatures).getBits(),
            "signed aggregation Bloom was not rebuilt from signatures");
    require(entries[0].max_hop_count == 5 && entries[0].count == 2,
            "signed aggregation metadata changed");

    UnionWindow split_window(1024, 3);
    const auto many = distinct_signatures(300);
    split_window.stage(11, RequestPacket::bloomForSignatures(many), 7, many);
    const auto split = split_window.drain();
    require(split.size() == 2, "300 signatures were not split into two frames");
    require(split[0].key_signatures.size() == 256 &&
                split[1].key_signatures.size() == 44,
            "signature split sizes are not 256/44");
    std::vector<RequestPacket::KeySignature> reconstructed;
    for (const auto& entry : split) {
        require(std::is_sorted(entry.key_signatures.begin(), entry.key_signatures.end()),
                "signature chunk is not sorted");
        require(entry.merged_bf.getBits() ==
                    RequestPacket::bloomForSignatures(entry.key_signatures).getBits(),
                "signature chunk Bloom is inconsistent");
        RequestPacket packet;
        packet.bf = entry.merged_bf;
        packet.key_signatures = entry.key_signatures;
        packet.is_aggregated = true;
        const auto encoded = PacketCodec::encode(packet, PacketCodec::kSignedRequestWireVersion);
        require(encoded.size() + sizeof(uint32_t) <= 1500,
                "length-prefixed wire-v3 aggregate exceeds MTU");
        reconstructed.insert(reconstructed.end(), entry.key_signatures.begin(),
                             entry.key_signatures.end());
    }
    require(reconstructed == many, "signature splitting changed canonical order/content");
}

}  // namespace

int main() {
    assert_round_trip(PacketCodec::kLegacyWireVersion, 1050);
    assert_round_trip(PacketCodec::kBitPackedBloomWireVersion, 154);
    assert_round_trip(PacketCodec::kBitPackedBloomWireVersion, 282, 2048, 3);
    require(282 + sizeof(uint32_t) == 286, "unexpected length-prefixed REQUEST size");
    assert_round_trip(PacketCodec::kBitPackedBloomWireVersion, 538, 4096, 3);
    require(538 + sizeof(uint32_t) == 542, "unexpected 4096-bit length-prefixed REQUEST size");
    assert_signature_helpers();
    assert_v3_round_trip(1, false, false);
    require(162 + sizeof(uint32_t) == 166,
            "unexpected length-prefixed single-signature wire-v3 size");
    assert_v3_round_trip(64, true, false);
    assert_v3_round_trip(256, false, true);
    require(1182 + sizeof(uint32_t) == 1186,
            "unexpected maximum length-prefixed wire-v3 size");
    assert_v3_rejects_invalid_frames();
    assert_union_window_signed_aggregation();

    setenv("RSYNC_WIRE_VERSION", "3", 1);
    require(PacketCodec::configured_wire_version() == PacketCodec::kSignedRequestWireVersion,
            "RSYNC_WIRE_VERSION=3 was not accepted");
    unsetenv("RSYNC_WIRE_VERSION");
    unsetenv("RSYNC_BLOOM_HASH_COUNT");

    std::vector<uint8_t> binary = {0, '\n', '\t', 255, 1, 2, 3};
    auto data = DataPacket::create(42, 4, 5, "binary", Value(binary));
    const auto encoded = PacketCodec::encode(*data, PacketCodec::kBitPackedBloomWireVersion);
    auto decoded_base = PacketCodec::decode(encoded);
    auto* decoded = dynamic_cast<DataPacket*>(decoded_base.get());
    require(decoded != nullptr, "decoded packet is not DATA");
    require(decoded->value.data() == binary, "binary DATA changed");
    const auto generated = rsync_experiment::deterministic_value(1024, 123);
    require(rsync_experiment::hex64(rsync_experiment::fnv1a64(generated)) ==
                "8309e7eec76daa9c",
            "deterministic DATA/hash differs from the canonical generator");
    const auto encoded_v3_data = PacketCodec::encode(*data, PacketCodec::kSignedRequestWireVersion);
    auto decoded_v3_base = PacketCodec::decode(encoded_v3_data);
    auto* decoded_v3 = dynamic_cast<DataPacket*>(decoded_v3_base.get());
    require(decoded_v3 != nullptr && decoded_v3->value.data() == binary,
            "wire-v3 binary DATA changed");

    std::cout << "packet codec v1/v2/v3 and signed aggregation tests passed\n";
    return 0;
}
