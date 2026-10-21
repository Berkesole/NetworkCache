#include "node.hpp"
#include "cache/in_memory_cache.hpp"
#include "network/mock_network.hpp"
#include <unordered_map>

Node::Node(uint32_t id, std::unique_ptr<NetworkInterface> network, std::unique_ptr<LocalCache> cache, Config config)
    : id_(id), config_(config), network_(std::move(network)), cache_(std::move(cache)),
      st_(SyncTable::Config()), union_window_(1024) {
    if (!cache_) { cache_ = std::make_unique<InMemoryCache>(); }
    if (!network_) { network_ = std::make_unique<MockNetwork>(id_); }
}

bool notifyApplication(const std::string& key, const Value& value) {
    // TODO: Need to implement a callback function to notify the application layer
    std::string content(value.data().begin(), value.data().end());
    std::cout << "[App] Received data for key=" << key
              << ", size=" << value.size()
              << ", content=\"" << content << "\"\n";
    return true;
}

/////////////////////////
// State table interface (private)
/////////////////////////
void Node::setQueryTable(uint32_t node_id, const BloomFilter& bf) {
    // Record pending requests for a node (represented by BF)
    qt_.set(node_id, bf);
}

std::vector<uint32_t> Node::getQueryTable(const std::string& key) {
    // Query which nodes are waiting for the specified key
    return qt_.get(key);
}

// SyncTable wrapper methods
void Node::setSyncTable(uint32_t node_id, const std::string& key) {
    // Record that a neighbor node has the specified key
    st_.set(node_id, key);
}

std::optional<uint32_t> Node::searchSyncTable(const std::string& filename) {
    // Find the best route to the specified file
    return st_.lookupBestRoute(filename);
}

std::vector<uint32_t> Node::searchSyncTableAllRoutes(const std::string& key) {
    // Find all possible routes to the specified key
    return st_.lookupAllRoutes(key);
}

// Use lightweight bitwise AND between request BF and neighbors' BF in SyncTable,
// to find the list of neighbor nodes that may have the requested data
std::vector<uint32_t> Node::selectNextHopsBySyncTable(const BloomFilter& request_bf) {
    std::vector<uint32_t> result;

    const auto& req_bits = request_bf.getBits();
    if (req_bits.empty()) return result;

    auto neighbors = st_.getAllNeighbors();
    for (uint32_t node_id : neighbors) {
        auto neighbor_bf_opt = st_.getNeighborBloomFilter(node_id);
        if (!neighbor_bf_opt.has_value()) continue;

        const auto& nb_bits = neighbor_bf_opt->getBits();
        if (nb_bits.empty()) continue;

        // Lightweight bitwise AND: if any bit is 1 in both, consider neighbor may have data
        size_t n = req_bits.size();
        if (nb_bits.size() < n) n = nb_bits.size();

        bool has_intersection = false;
        for (size_t i = 0; i < n; ++i) {
            if (req_bits[i] && nb_bits[i]) {
                has_intersection = true;
                break;
            }
        }

        if (has_intersection) {
            result.push_back(node_id);
        }
    }
    
    return result;
}

//////////////////////////
// cache/redis interface//
//////////////////////////
void Node::addLocalData(const std::string& key, const std::string& value) {
    Value v(std::vector<uint8_t>(value.begin(), value.end()));
    cache_->put(key, v);
    local_files_.insert(key);
}
// TODO: Need to modify the corresponding value type here
std::optional<Value> Node::getLocalData(const std::string& key) {
    return cache_->get(key);
}

bool Node::hasLocalData(const std::string& key) {
    return cache_->exists(key);
}

////////////////////////////
// network layer interface//
////////////////////////////
void Node::sendDirect(std::unique_ptr<Packet> pkt, uint32_t dest_node_id) {
    network_->send_direct(dest_node_id, std::move(pkt));
}

void Node::sendBroadcast(std::unique_ptr<Packet> pkt) {
    network_->broadcast(std::move(pkt));
}

///////////////////////////////
///////// core logic //////////
///////////////////////////////
void Node::requestFile(const std::vector<std::string>& keys) {
    // Debug output: all keys requested by user
    for (const auto& k : keys) {
        std::cout << "[Node " << id_ << "] User wants file: " << k << "\n";
    }

    // Record data that needs to be fetched remotely (for QueryTable)
    BloomFilter self_request_bf(1024);
    bool has_remote_request = false;

    // Group keys to forward by next-hop node
    std::unordered_map<uint32_t, std::vector<std::string>> direct_keys_per_node;
    std::vector<std::string> broadcast_keys;

    for (const auto& key : keys) {
        // 1. Check local cache first
        if (hasLocalData(key)) {
            auto val_opt = getLocalData(key);
            if (val_opt.has_value()) {
                notifyApplication(key, val_opt.value());
            }
            continue;
        }

        // Not in local cache, need to fetch remotely
        self_request_bf.add(key);
        has_remote_request = true;

        // 2. Check SyncTable to see if there are any matching neighbor nodes
        auto next_hops = searchSyncTableAllRoutes(key);
        if (!next_hops.empty()) {
            for (uint32_t node_id : next_hops) {
                direct_keys_per_node[node_id].push_back(key);
            }
        } else {
            // 3. SyncTable miss, fall back to broadcast
            broadcast_keys.push_back(key);
        }
    }

    // Record which keys we're waiting for in QueryTable
    if (has_remote_request) {
        setQueryTable(id_, self_request_bf);
    }

    // 4. For keys that hit in SyncTable, aggregate by next-hop and send unicast
    for (auto& entry : direct_keys_per_node) {
        uint32_t dest_node_id = entry.first;
        auto& dest_keys = entry.second;
        if (dest_keys.empty()) continue;

        auto pkt = RequestPacket::create(id_, dest_keys);
        pkt->dest_node_id = dest_node_id;
        sendDirect(std::move(pkt), dest_node_id);

        std::cout << "[Node " << id_ << "] Sent REQUEST for "
                  << dest_keys.size() << " key(s) to Node "
                  << dest_node_id << " via SyncTable\n";
    }

    // 5. For keys that missed in SyncTable, broadcast once
    if (!broadcast_keys.empty()) {
        auto pkt = RequestPacket::create(id_, broadcast_keys);
        sendBroadcast(std::move(pkt));
        std::cout << "[Node " << id_ << "] Broadcast REQUEST for "
                  << broadcast_keys.size() << " key(s) (no SyncTable hit)\n";
    }
}

std::unique_ptr<RequestPacket> Node::buildForwardedRequest(const RequestPacket* pkt, uint32_t dest_node_id) {
    auto fwd = RequestPacket::create(id_, {});
    fwd->request_id = pkt->request_id;
    fwd->src_node_id = id_;
    fwd->bf = pkt->bf;
    fwd->dest_node_id = dest_node_id;
    fwd->hop_count = pkt->hop_count + 1;
    fwd->is_aggregated = pkt->is_aggregated;  // preserve flag across hops
    return fwd;
}

void Node::handleRequest(RequestPacket* pkt) {
    if (pkt->hop_count >= config_.max_hop_count) {
        std::cout << "[Node " << id_ << "] handleRequest() - DROP: hop_count="
                  << pkt->hop_count << " >= max=" << config_.max_hop_count << "\n";
        return;
    }

    std::cout << "[Node " << id_ << "] handleRequest() - checking local files against BF\n";

    // Check if local files match BF
    for (const auto& file : local_files_) {
        if (pkt->bf.contains(file)) {
            // We have this file locally, send DATA packet
            auto val_opt = cache_->get(file);
            if (val_opt) {
                auto data_pkt = DataPacket::create(
                    pkt->request_id,
                    id_,              // Current node is the source of DATA
                    pkt->src_node_id, // The request initiator
                    file,
                    std::move(*val_opt)
                );

                // Send back to the source node
                sendDirect(std::move(data_pkt), pkt->src_node_id);
                std::cout << "[Node " << id_ << "] Sending DATA for key=" << file << " to Node " << pkt->src_node_id << "\n";
            }
        }
    }

    // Record which keys the upstream node is waiting for (represented by BF)
    setQueryTable(pkt->src_node_id, pkt->bf);

    // Use SyncTable + BF for lightweight routing:
    // 1. Find neighbors that may have data based on BF ∧ neighbor BF
    // 2. If no matching neighbors, fall back to broadcast
    auto next_hops = selectNextHopsBySyncTable(pkt->bf);

    // Compute egress candidate list (with prev-hop exclusion applied to BOTH paths
    std::vector<uint32_t> candidates;
    const char* path_label;
    if (next_hops.empty()) {
        // Broadcast fallback (minus prev-hop)
        auto neighbors = network_->get_neighbors();
        for (uint32_t n : neighbors) {
            if (n != pkt->src_node_id) candidates.push_back(n);
        }
        path_label = "broadcast-minus-prev-hop";
    } else {
        for (uint32_t n : next_hops) {
            if (n != pkt->src_node_id) candidates.push_back(n);
        }
        path_label = "SyncTable BF match";
    }

    // single-layer enforcement
    // - is_aggregated=true: forward directly (no re-aggregation)
    // - is_aggregated=false: stage into per-egress union window
    if (pkt->is_aggregated) {
        for (uint32_t dest : candidates) {
            sendDirect(buildForwardedRequest(pkt, dest), dest);
            std::cout << "[Node " << id_ << "] handleRequest(): forward AGGREGATE to Node "
                      << dest << " via " << path_label
                      << " (hop=" << pkt->hop_count + 1 << ")\n";
        }
    } else {
        for (uint32_t dest : candidates) {
            // Stage with forwarded hop_count (= pkt->hop_count + 1), matching §3.5 step 6
            union_window_.stage(dest, pkt->bf, pkt->hop_count + 1);
            std::cout << "[Node " << id_ << "] handleRequest(): stage REQUEST for Node "
                      << dest << " via " << path_label
                      << " (hop=" << pkt->hop_count + 1 << ")\n";
        }
    }
}

// Handle returned data
void Node::handleData(DataPacket* pkt) {
    // 1. Put into buffer/cache, and update sync table to serve other requests ASAP, reducing latency

    // 1.1 Check if the data is already in the cache
    if (hasLocalData(pkt->key)) {
        // The data is already in the cache, just drop
        return;
    }
    // 1.2 Put the data into the cache
    // TODO: Unify with cache eviction logic
    cache_->put(pkt->key, pkt->value);
    local_files_.insert(pkt->key);
    // Update SyncTable: record which neighbor node has this key
    if (pkt->src_node_id != 0) {
        setSyncTable(pkt->src_node_id, pkt->key);
    }

    // 2. Check query table: which nodes are waiting for this data
    std::vector<uint32_t> downstream = getQueryTable(pkt->key);

    // 3. Forward to downstream: exclude prev-hop to avoid
    //    sending DATA back along the edge it arrived on)
    for (uint32_t node_id : downstream) {
        if (node_id == pkt->src_node_id) continue;  // prev-hop exclusion

        if (node_id == id_) {
            // Two cases:
            // 1. I'm the initiator, callback to application layer directly
            std::cout << "[Node " << id_ << "] Received DATA for key=" << pkt->key << "\n";
            notifyApplication(pkt->key, pkt->value);
        } else {
            // Forward to downstream node
            auto data_pkt = DataPacket::create(
                pkt->request_id,
                id_,      // Current node is the forwarding source
                node_id,  // Downstream node
                pkt->key,
                Value(pkt->value.data())  // Copy Value
            );
            sendDirect(std::move(data_pkt), node_id);
            std::cout << "[Node " << id_ << "] Forwarding DATA for key=" << pkt->key << " to Node " << node_id << "\n";
        }
    }

}

// ===== Original code (commented out, SYNC packet is deprecated) =====
// handle sync packet
// void Node::handleSync(std::unique_ptr<Packet> pkt) {
//     // TODO: update the sync table
//     // ...
// }
// ===== End of original code =====


void Node::flushUnionWindows() {
    auto entries = union_window_.drain();
    for (auto& e : entries) {
        auto out = std::make_unique<RequestPacket>();
        out->request_id = generateRequestId();  // new id for the aggregate
        out->src_node_id = id_;                  // aggregator = prev-hop for next node
        out->dest_node_id = e.egress_node_id;
        out->bf = std::move(e.merged_bf);
        out->hop_count = e.max_hop_count;        // already +1 at stage time
        out->is_aggregated = true;

        std::cout << "[Node " << id_ << "] flush AGGREGATE to Node " << e.egress_node_id
                  << " (count=" << e.count << ", hop=" << e.max_hop_count << ")\n";
        sendDirect(std::move(out), e.egress_node_id);
    }
}

// Drain all pending packets, then flush staged unions.
// "Batch" = inbox snapshot at entry time; aggregation scope is one processOne() call.
void Node::processOne() {
    while (auto pkt = network_->receive()) {
        switch (pkt->type) {
            case PacketType::REQUEST: {
                auto* req_pkt = static_cast<RequestPacket*>(pkt.get());
                handleRequest(req_pkt);
                break;
            }
            case PacketType::DATA: {
                auto* data_pkt = static_cast<DataPacket*>(pkt.get());
                handleData(data_pkt);
                break;
            }
            default:
                std::cout << "[Node " << id_ << "] Unknown packet type: "
                          << static_cast<int>(pkt->type) << "\n";
                break;
        }
    }
    flushUnionWindows();
}

