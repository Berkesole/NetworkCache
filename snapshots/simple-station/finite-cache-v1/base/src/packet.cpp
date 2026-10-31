#include "packet.hpp"

std::unique_ptr<RequestPacket> RequestPacket::create(
    uint32_t src_node_id,
    const std::vector<std::string>& keys,
    size_t bloom_bits,
    size_t bloom_hash_count
) {
    auto pkt = std::make_unique<RequestPacket>();
    pkt->request_id = generateRequestId();
    pkt->src_node_id = src_node_id;
    pkt->dest_node_id = kBroadcastNodeId;  // Broadcast / unspecified egress

    pkt->bf = BloomFilter(bloom_bits, bloom_hash_count);
    pkt->bf.batch_add(keys);
    if (bloom_bits == kSignatureBloomBits && bloom_hash_count == kSignatureHashCount) {
        pkt->key_signatures.reserve(keys.size());
        for (const auto& key : keys) {
            pkt->key_signatures.push_back(signatureForKey(key));
        }
        pkt->key_signatures = canonicalizeSignatures(pkt->key_signatures);
    }

    return pkt;
}

std::unique_ptr<DataPacket> DataPacket::create(
    uint64_t request_id,
    uint32_t src_node_id,
    uint32_t dest_node_id,
    const std::string& key,
    Value value
) {
    auto pkt = std::make_unique<DataPacket>();
    pkt->request_id = request_id;
    pkt->src_node_id = src_node_id;
    pkt->dest_node_id = dest_node_id;
    pkt->key = key;
    pkt->value = std::move(value);

    return pkt;
}
