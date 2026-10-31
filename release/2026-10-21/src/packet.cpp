#include "packet.hpp"

std::unique_ptr<RequestPacket> RequestPacket::create(
    uint32_t src_node_id,
    const std::vector<std::string>& keys
) {
    auto pkt = std::make_unique<RequestPacket>();
    pkt->request_id = generateRequestId();
    pkt->src_node_id = src_node_id;
    pkt->dest_node_id = 0;  // Broadcast

    pkt->bf = BloomFilter(1024);
    pkt->bf.batch_add(keys);

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
