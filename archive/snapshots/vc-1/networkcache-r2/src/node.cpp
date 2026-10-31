#include "node.hpp"
#include "cache/in_memory_cache.hpp"
#include "event_trace.hpp"
#include "network/mock_network.hpp"
#include <algorithm>
#include <cstdlib>
#include <sstream>
#include <unordered_map>
#include <utility>

namespace {

bool observabilityEnabled() {
    static const bool enabled = [] {
        const char* raw = std::getenv("RSYNC_OBSERVABILITY");
        if (raw == nullptr) return false;
        const std::string value(raw);
        return !value.empty() && value != "0" && value != "false" && value != "FALSE";
    }();
    return enabled;
}

bool oracleUnicastEnabled() {
    static const bool enabled = [] {
        const char* raw = std::getenv("RSYNC_ORACLE_UNICAST");
        if (raw == nullptr) return false;
        const std::string value(raw);
        return !value.empty() && value != "0" && value != "false" && value != "FALSE";
    }();
    return enabled;
}

std::string joinNodeIds(const std::vector<uint32_t>& nodes) {
    std::ostringstream out;
    for (std::size_t i = 0; i < nodes.size(); ++i) {
        if (i > 0) out << ';';
        out << nodes[i];
    }
    return out.str();
}

std::string joinKeys(const std::vector<std::string>& keys) {
    std::ostringstream out;
    for (std::size_t i = 0; i < keys.size(); ++i) {
        if (i > 0) out << ';';
        out << keys[i];
    }
    return out.str();
}

std::string joinSignatures(const std::vector<RequestPacket::KeySignature>& signatures) {
    std::ostringstream out;
    for (std::size_t i = 0; i < signatures.size(); ++i) {
        if (i > 0) out << ';';
        out << signatures[i];
    }
    return out.str();
}

std::size_t signedRequestTcpFrameBytes(std::size_t signature_count) {
    // wire-v3 fixed REQUEST fields and packed 1024-bit BF occupy 158 bytes;
    // each signature adds four bytes and TCP adds a four-byte length prefix.
    return 162 + 4 * signature_count;
}

const char* packetTypeName(PacketType type) {
    switch (type) {
        case PacketType::REQUEST:
            return "REQUEST";
        case PacketType::DATA:
            return "DATA";
        default:
            return "UNKNOWN";
    }
}

std::size_t countBloomBits(const BloomFilter& bf) {
    const auto& bits = bf.getBits();
    return static_cast<std::size_t>(std::count(bits.begin(), bits.end(), true));
}

SyncTable::Config makeSyncConfig(const Node::Config& node_config) {
    SyncTable::Config sync_config;
    sync_config.bloom_bits = node_config.bloom_bits;
    sync_config.bloom_hash_count = node_config.bloom_hash_count;
    return sync_config;
}

QueryTable::Config makeQueryConfig(const Node::Config& node_config) {
    QueryTable::Config query_config;
    query_config.legacy_timeout = node_config.query_timeout;
    query_config.fallback_delay = node_config.fallback_delay;
    query_config.state_lifetime = node_config.staged_fallback_enabled
        ? node_config.query_timeout
        : std::max(node_config.query_timeout,
                   node_config.fallback_delay + std::chrono::milliseconds(1));
    return query_config;
}

std::string sanitizeObsValue(std::string value) {
    for (char& ch : value) {
        if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r') {
            ch = '_';
        }
    }
    return value;
}

void obsLog(uint32_t node_id, const std::string& event,
            const std::vector<std::pair<std::string, std::string>>& fields = {}) {
    if (!observabilityEnabled()) return;
    std::cout << "[Obs] event=" << event << " node=" << node_id;
    for (const auto& [key, value] : fields) {
        std::cout << ' ' << key << '=' << sanitizeObsValue(value);
    }
    std::cout << "\n";
}

}  // namespace

Node::Node(uint32_t id, std::unique_ptr<NetworkInterface> network, std::unique_ptr<LocalCache> cache, Config config)
    : id_(id), config_(config), qt_(makeQueryConfig(config)),
      st_(makeSyncConfig(config)),
      union_window_(config.bloom_bits, config.bloom_hash_count),
      network_(std::move(network)), cache_(std::move(cache)) {
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
    qt_.set(node_id, bf, config_.query_timeout);
    rsync_trace::record(id_, "query_table_set",
                        {{"src", std::to_string(node_id)},
                         {"value_bytes", std::to_string(countBloomBits(bf))}});
    obsLog(id_, "query_table_set",
           {{"from_node", std::to_string(node_id)},
            {"bf_bits_set", std::to_string(countBloomBits(bf))}});
}

std::vector<uint32_t> Node::getQueryTable(const std::string& key) {
    // Query which nodes are waiting for the specified key
    auto matches = config_.staged_fallback_enabled
        ? qt_.takePending(key)
        : qt_.get(key);
    const auto expired_count = qt_.lastExpiredCount();
    if (expired_count > 0) {
        rsync_trace::record(id_, "query_table_expired",
                            {{"value_bytes", std::to_string(expired_count)}});
        obsLog(id_, "query_table_expired",
               {{"expired_entry_count", std::to_string(expired_count)}});
    }
    if (matches.empty()) {
        rsync_trace::record(id_, "query_table_miss", {{"key", key}});
        obsLog(id_, "query_table_miss", {{"key", key}});
    } else {
        rsync_trace::record(id_, "query_table_match",
                            {{"key", key}, {"dest", joinNodeIds(matches)}});
        obsLog(id_, "query_table_match",
               {{"key", key}, {"matched_downstream_nodes", joinNodeIds(matches)}});
    }
    return matches;
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

        if (nb_bits.size() != req_bits.size()) continue;
        bool matches = false;
        if (request_bf.getHashCount() == 1) {
            // Preserve legacy one-hash aggregate semantics.
            for (size_t i = 0; i < req_bits.size(); ++i) {
                if (req_bits[i] && nb_bits[i]) {
                    matches = true;
                    break;
                }
            }
        } else {
            // The bf2048-h3 pilot carries exactly one application key per REQUEST.
            // All positions for that key must be present in the neighbor summary.
            matches = neighbor_bf_opt->containsAllSetBits(request_bf);
        }

        if (matches) {
            result.push_back(node_id);
        }
    }
    
    return result;
}

//////////////////////////
// cache/redis interface//
//////////////////////////
void Node::addLocalData(const std::string& key, const std::string& value) {
    addLocalData(key, Value(std::vector<uint8_t>(value.begin(), value.end())));
}

void Node::addLocalData(const std::string& key, const Value& value) {
    cache_->put(key, value);
    local_files_.insert(key);
}

void Node::setApplicationCallback(ApplicationCallback callback) {
    application_callback_ = std::move(callback);
}

std::optional<uint32_t> Node::debugCachedRoute(const std::string& key) {
    return searchSyncTable(key);
}

std::vector<SyncTable::BloomOccupancy> Node::debugSyncBloomOccupancy() const {
    return st_.debugBloomOccupancy();
}

void Node::deliverApplication(const std::string& key, const Value& value,
                              uint64_t request_id, const std::string& via) {
    if (application_callback_) {
        application_callback_(ApplicationDelivery{key, request_id, via, value});
        return;
    }
    notifyApplication(key, value);
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
        rsync_trace::record(id_, "request_start", {{"key", k}, {"src", std::to_string(id_)}});
        obsLog(id_, "request_start", {{"key", k}});
    }

    // Record data that needs to be fetched remotely (for QueryTable)
    BloomFilter self_request_bf(config_.bloom_bits, config_.bloom_hash_count);
    bool has_remote_request = false;
    std::vector<std::string> remote_keys;

    // Group keys to forward by next-hop node
    std::unordered_map<uint32_t, std::vector<std::string>> direct_keys_per_node;
    std::vector<std::string> broadcast_keys;

    for (const auto& key : keys) {
        // 1. Check local cache first
        if (hasLocalData(key)) {
            auto val_opt = getLocalData(key);
            if (val_opt.has_value()) {
                rsync_trace::record(id_, "local_cache_hit",
                                    {{"request_id", "0"},
                                     {"key", key},
                                     {"src", std::to_string(id_)},
                                     {"dest", std::to_string(id_)},
                                     {"via", "local_cache"},
                                     {"value_bytes", std::to_string(val_opt->size())}});
                rsync_trace::record(id_, "provider_hit",
                                    {{"request_id", "0"},
                                     {"key", key},
                                     {"src", std::to_string(id_)},
                                     {"dest", std::to_string(id_)},
                                     {"via", "local_cache"},
                                     {"value_bytes", std::to_string(val_opt->size())}});
                rsync_trace::record(id_, "app_receive",
                                    {{"request_id", "0"},
                                     {"key", key},
                                     {"src", std::to_string(id_)},
                                     {"dest", std::to_string(id_)},
                                     {"via", "local_cache"},
                                     {"value_bytes", std::to_string(val_opt->size())}});
                obsLog(id_, "local_cache_hit", {{"key", key}});
                obsLog(id_, "provider_hit",
                       {{"key", key}, {"request_id", "0"}, {"dest", std::to_string(id_)}});
                obsLog(id_, "app_receive",
                       {{"key", key}, {"request_id", "0"}, {"source", "local_cache"}});
                deliverApplication(key, val_opt.value(), 0, "local_cache");
            }
            continue;
        }

        // Not in local cache, need to fetch remotely
        self_request_bf.add(key);
        has_remote_request = true;
        remote_keys.push_back(key);

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

    if (config_.staged_fallback_enabled && !remote_keys.empty()) {
        for (std::size_t offset = 0; offset < remote_keys.size();
             offset += RequestPacket::kMaxKeySignatures) {
            const auto end = std::min(offset + RequestPacket::kMaxKeySignatures,
                                      remote_keys.size());
            std::vector<QueryTable::OriginKey> origin_keys;
            origin_keys.reserve(end - offset);
            for (auto it = remote_keys.begin() + static_cast<std::ptrdiff_t>(offset);
                 it != remote_keys.begin() + static_cast<std::ptrdiff_t>(end); ++it) {
                origin_keys.push_back(
                    QueryTable::OriginKey{*it, RequestPacket::signatureForKey(*it)});
            }
            const auto uid = QueryTable::makeStableUid(
                id_, next_origin_query_sequence_++);
            if (!qt_.startOriginQuery(uid, origin_keys, config_.fallback_delay,
                                      config_.query_timeout)) {
                throw std::runtime_error("Node: duplicate origin query UID");
            }
            rsync_trace::record(id_, "origin_fallback_armed",
                                {{"request_id", std::to_string(uid)},
                                 {"src", std::to_string(id_)},
                                 {"phase", "preferred"},
                                 {"signature_count", std::to_string(origin_keys.size())},
                                 {"deadline_ms", std::to_string(config_.fallback_delay.count())}});
        }
    }

    // 4. For keys that hit in SyncTable, aggregate by next-hop and send unicast
    for (auto& entry : direct_keys_per_node) {
        uint32_t dest_node_id = entry.first;
        auto& dest_keys = entry.second;
        if (dest_keys.empty()) continue;

        for (std::size_t offset = 0; offset < dest_keys.size();
             offset += RequestPacket::kMaxKeySignatures) {
            const auto end = std::min(offset + RequestPacket::kMaxKeySignatures,
                                      dest_keys.size());
            std::vector<std::string> chunk(
                dest_keys.begin() + static_cast<std::ptrdiff_t>(offset),
                dest_keys.begin() + static_cast<std::ptrdiff_t>(end));
            auto pkt = RequestPacket::create(id_, chunk, config_.bloom_bits,
                                             config_.bloom_hash_count);
            pkt->dest_node_id = dest_node_id;
            const auto request_id = pkt->request_id;
            rsync_trace::record(id_, "request_send_start",
                                {{"request_id", std::to_string(request_id)},
                                 {"key", joinKeys(chunk)},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(dest_node_id)},
                                 {"packet_type", "REQUEST"},
                                 {"hop_count", std::to_string(pkt->hop_count)},
                                 {"via", "sync_table"},
                                 {"phase", "preferred"},
                                 {"signature_count", std::to_string(pkt->key_signatures.size())}});
            sendDirect(std::move(pkt), dest_node_id);
            rsync_trace::record(id_, "request_send_done",
                                {{"request_id", std::to_string(request_id)},
                                 {"key", joinKeys(chunk)},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(dest_node_id)},
                                 {"packet_type", "REQUEST"},
                                 {"via", "sync_table"},
                                 {"phase", "preferred"}});
        }

        std::cout << "[Node " << id_ << "] Sent REQUEST for "
                  << dest_keys.size() << " key(s) to Node "
                  << dest_node_id << " via SyncTable\n";
    }

    // 5. For keys that missed in SyncTable, broadcast once
    if (!broadcast_keys.empty()) {
        for (std::size_t offset = 0; offset < broadcast_keys.size();
             offset += RequestPacket::kMaxKeySignatures) {
            const auto end = std::min(offset + RequestPacket::kMaxKeySignatures,
                                      broadcast_keys.size());
            std::vector<std::string> chunk(
                broadcast_keys.begin() + static_cast<std::ptrdiff_t>(offset),
                broadcast_keys.begin() + static_cast<std::ptrdiff_t>(end));
            auto pkt = RequestPacket::create(id_, chunk, config_.bloom_bits,
                                             config_.bloom_hash_count);
            const auto request_id = pkt->request_id;
            rsync_trace::record(id_, "request_send_start",
                                {{"request_id", std::to_string(request_id)},
                                 {"key", joinKeys(chunk)},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(kBroadcastNodeId)},
                                 {"packet_type", "REQUEST"},
                                 {"hop_count", std::to_string(pkt->hop_count)},
                                 {"via", "broadcast"},
                                 {"phase", "preferred"},
                                 {"signature_count", std::to_string(pkt->key_signatures.size())}});
            sendBroadcast(std::move(pkt));
            rsync_trace::record(id_, "request_send_done",
                                {{"request_id", std::to_string(request_id)},
                                 {"key", joinKeys(chunk)},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(kBroadcastNodeId)},
                                 {"packet_type", "REQUEST"},
                                 {"via", "broadcast"},
                                 {"phase", "preferred"}});
        }
        std::cout << "[Node " << id_ << "] Broadcast REQUEST for "
                  << broadcast_keys.size() << " key(s) (no SyncTable hit)\n";
    }
}

void Node::requestFileFromProvider(const std::string& key, uint32_t provider_node_id) {
    std::cout << "[Node " << id_ << "] User wants file: " << key << "\n";
    rsync_trace::record(id_, "request_start",
                        {{"key", key},
                         {"src", std::to_string(id_)},
                         {"dest", std::to_string(provider_node_id)},
                         {"via", "oracle_unicast"}});
    obsLog(id_, "request_start", {{"key", key}, {"oracle_provider", std::to_string(provider_node_id)}});

    if (hasLocalData(key)) {
        auto val_opt = getLocalData(key);
        if (val_opt.has_value()) {
            rsync_trace::record(id_, "local_cache_hit",
                                {{"request_id", "0"},
                                 {"key", key},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(id_)},
                                 {"via", "oracle_unicast_local_cache"},
                                 {"value_bytes", std::to_string(val_opt->size())}});
            rsync_trace::record(id_, "provider_hit",
                                {{"request_id", "0"},
                                 {"key", key},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(id_)},
                                 {"via", "oracle_unicast_local_cache"},
                                 {"value_bytes", std::to_string(val_opt->size())}});
            rsync_trace::record(id_, "app_receive",
                                {{"request_id", "0"},
                                 {"key", key},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(id_)},
                                 {"via", "oracle_unicast_local_cache"},
                                 {"value_bytes", std::to_string(val_opt->size())}});
            obsLog(id_, "local_cache_hit",
                   {{"key", key}, {"via", "oracle_unicast"}});
            obsLog(id_, "provider_hit",
                   {{"key", key},
                    {"request_id", "0"},
                    {"dest", std::to_string(id_)},
                    {"via", "oracle_unicast_local_cache"}});
            obsLog(id_, "app_receive",
                   {{"key", key},
                    {"request_id", "0"},
                    {"source", "oracle_unicast_local_cache"}});
            deliverApplication(key, val_opt.value(), 0, "oracle_unicast_local_cache");
        }
        return;
    }

    BloomFilter self_request_bf(config_.bloom_bits, config_.bloom_hash_count);
    self_request_bf.add(key);
    setQueryTable(id_, self_request_bf);

    if (config_.staged_fallback_enabled) {
        const auto uid = QueryTable::makeStableUid(
            id_, next_origin_query_sequence_++);
        const std::vector<QueryTable::OriginKey> origin_keys{
            QueryTable::OriginKey{key, RequestPacket::signatureForKey(key)}};
        if (!qt_.startOriginQuery(
                uid, origin_keys, config_.fallback_delay,
                config_.query_timeout, QueryTable::FallbackPolicy::Disabled)) {
            throw std::runtime_error("Node: duplicate known-owner origin query UID");
        }
        rsync_trace::record(id_, "origin_query_tracked",
                            {{"request_id", std::to_string(uid)},
                             {"key", key},
                             {"src", std::to_string(id_)},
                             {"dest", std::to_string(provider_node_id)},
                             {"via", "known-owner"},
                             {"phase", "preferred"},
                             {"fallback", "0"}});
    }

    auto pkt = RequestPacket::create(id_, std::vector<std::string>{key},
                                     config_.bloom_bits, config_.bloom_hash_count);
    pkt->dest_node_id = provider_node_id;
    const auto request_id = pkt->request_id;
    rsync_trace::record(id_, "request_send_start",
                        {{"request_id", std::to_string(request_id)},
                         {"key", key},
                         {"src", std::to_string(id_)},
                         {"dest", std::to_string(provider_node_id)},
                         {"packet_type", "REQUEST"},
                         {"hop_count", std::to_string(pkt->hop_count)},
                         {"via", "oracle_unicast"}});
    sendDirect(std::move(pkt), provider_node_id);
    rsync_trace::record(id_, "request_send_done",
                        {{"request_id", std::to_string(request_id)},
                         {"key", key},
                         {"src", std::to_string(id_)},
                         {"dest", std::to_string(provider_node_id)},
                         {"packet_type", "REQUEST"},
                         {"via", "oracle_unicast"}});
    obsLog(id_, "request_forward",
           {{"request_id", std::to_string(request_id)},
            {"dest", std::to_string(provider_node_id)},
            {"key_count", "1"},
            {"via", "oracle_unicast"}});

    std::cout << "[Node " << id_ << "] Sent ORACLE REQUEST for key="
              << key << " to Node " << provider_node_id << "\n";
}

std::unique_ptr<RequestPacket> Node::buildForwardedRequest(const RequestPacket* pkt, uint32_t dest_node_id) {
    auto fwd = RequestPacket::create(id_, {});
    fwd->request_id = pkt->request_id;
    fwd->src_node_id = id_;
    fwd->bf = pkt->bf;
    fwd->dest_node_id = dest_node_id;
    fwd->hop_count = pkt->hop_count + 1;
    fwd->is_aggregated = pkt->is_aggregated;  // preserve flag across hops
    fwd->is_fallback = pkt->is_fallback;
    fwd->key_signatures = pkt->key_signatures;
    return fwd;
}

void Node::handleRequest(RequestPacket* pkt) {
    rsync_trace::record(id_, "request_handle_start",
                        {{"request_id", std::to_string(pkt->request_id)},
                         {"src", std::to_string(pkt->src_node_id)},
                         {"dest", std::to_string(pkt->dest_node_id)},
                         {"packet_type", packetTypeName(pkt->type)},
                         {"hop_count", std::to_string(pkt->hop_count)}});
    if (pkt->hop_count >= config_.max_hop_count) {
        std::cout << "[Node " << id_ << "] handleRequest() - DROP: hop_count="
                  << pkt->hop_count << " >= max=" << config_.max_hop_count << "\n";
        return;
    }

    const bool signed_request = !pkt->key_signatures.empty();
    std::optional<QueryTable::AcceptFallbackResult> fallback_route;
    if (pkt->is_fallback) {
        if (!config_.staged_fallback_enabled || !signed_request ||
            !QueryTable::isStableUid(pkt->request_id)) {
            rsync_trace::record(id_, "duplicate_request_suppressed",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"src", std::to_string(pkt->src_node_id)},
                                 {"phase", "fallback"},
                                 {"reason", "invalid-fallback-request"},
                                 {"fallback", "1"}});
            return;
        }
        fallback_route = qt_.acceptFallback(
            pkt->request_id, pkt->src_node_id, pkt->hop_count,
            pkt->key_signatures, network_->get_neighbors(),
            config_.query_timeout);
        if (!fallback_route->accepted) {
            rsync_trace::record(id_, "duplicate_request_suppressed",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"src", std::to_string(pkt->src_node_id)},
                                 {"phase", "fallback"},
                                 {"reason", fallback_route->reached_origin
                                                ? "returned-to-origin"
                                                : "same-or-longer-hop"},
                                 {"signature_count", std::to_string(pkt->key_signatures.size())},
                                 {"fallback", "1"}});
            return;
        }
    }

    std::cout << "[Node " << id_ << "] handleRequest() - checking local files against BF\n";

    // Check if local files match the request. Wire v3 keeps per-key Bloom
    // signatures so an aggregate's unrelated set bits cannot create a local
    // provider hit. Legacy wire versions retain the original BF lookup.
    bool sent_matching_data = false;
    std::vector<std::string> locally_satisfied_keys;
    for (const auto& file : local_files_) {
        const auto local_signature = signed_request
            ? RequestPacket::signatureForKey(file)
            : RequestPacket::KeySignature{0};
        const bool requested = signed_request
            ? std::binary_search(pkt->key_signatures.begin(), pkt->key_signatures.end(),
                                 local_signature)
            : pkt->bf.contains(file);
        if (requested) {
            // We have this file locally, send DATA packet
            auto val_opt = cache_->get(file);
            if (val_opt) {
                rsync_trace::record(id_, "provider_hit",
                                    {{"request_id", std::to_string(pkt->request_id)},
                                     {"key", file},
                                     {"src", std::to_string(id_)},
                                     {"dest", std::to_string(pkt->src_node_id)},
                                     {"packet_type", "DATA"},
                                     {"hop_count", std::to_string(pkt->hop_count)},
                                     {"value_bytes", std::to_string(val_opt->size())}});
                auto data_pkt = DataPacket::create(
                    pkt->request_id,
                    id_,              // Current node is the source of DATA
                    pkt->src_node_id, // The request initiator
                    file,
                    std::move(*val_opt)
                );

                // Send back to the source node
                rsync_trace::record(id_, "data_send_start",
                                    {{"request_id", std::to_string(pkt->request_id)},
                                     {"key", file},
                                     {"src", std::to_string(id_)},
                                     {"dest", std::to_string(pkt->src_node_id)},
                                     {"packet_type", "DATA"},
                                     {"value_bytes", std::to_string(data_pkt->value.size())}});
                sendDirect(std::move(data_pkt), pkt->src_node_id);
                rsync_trace::record(id_, "data_send_done",
                                    {{"request_id", std::to_string(pkt->request_id)},
                                     {"key", file},
                                     {"src", std::to_string(id_)},
                                     {"dest", std::to_string(pkt->src_node_id)},
                                     {"packet_type", "DATA"}});
                obsLog(id_, "provider_hit",
                       {{"key", file},
                        {"request_id", std::to_string(pkt->request_id)},
                        {"dest", std::to_string(pkt->src_node_id)}});
                obsLog(id_, "data_forward",
                       {{"key", file},
                        {"request_id", std::to_string(pkt->request_id)},
                        {"dest", std::to_string(pkt->src_node_id)}});
                std::cout << "[Node " << id_ << "] Sending DATA for key=" << file << " to Node " << pkt->src_node_id << "\n";
                sent_matching_data = true;
                locally_satisfied_keys.push_back(file);
            }
        }
    }

    if (oracleUnicastEnabled() && sent_matching_data) {
        obsLog(id_, "request_stop",
               {{"request_id", std::to_string(pkt->request_id)},
                {"reason", "oracle_unicast_provider_hit"}});
        return;
    }

    if (pkt->is_fallback) {
        for (const auto dest : fallback_route->egress_nodes) {
            auto forwarded = buildForwardedRequest(pkt, dest);
            forwarded->is_aggregated = false;
            const auto frame_bytes =
                signedRequestTcpFrameBytes(forwarded->key_signatures.size());
            const auto outgoing_hop = forwarded->hop_count;
            rsync_trace::record(id_, "fallback_forward",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(dest)},
                                 {"packet_type", "REQUEST"},
                                 {"hop_count", std::to_string(outgoing_hop)},
                                 {"phase", "fallback"},
                                 {"signature_count", std::to_string(pkt->key_signatures.size())},
                                 {"frame_bytes", std::to_string(frame_bytes)},
                                 {"fallback", "1"}});
            sendDirect(std::move(forwarded), dest);
        }
        return;
    }

    // Record which keys the upstream node is waiting for (represented by BF)
    setQueryTable(pkt->src_node_id, pkt->bf);
    if (config_.staged_fallback_enabled) {
        for (const auto& key : locally_satisfied_keys) {
            qt_.markPendingSatisfied(pkt->src_node_id, key);
        }
    }

    if (signed_request && !pkt->is_fallback) {
        // Route every constituent signature independently. A match for one
        // key must never suppress the unmatched direction for another key in
        // the same aggregate.
        std::unordered_map<uint32_t, std::vector<RequestPacket::KeySignature>> by_egress;
        const auto physical_neighbors = network_->get_neighbors();
        const auto sync_neighbors = st_.getAllNeighbors();
        std::vector<QueryTable::SignatureRoute> tracked_routes;
        tracked_routes.reserve(pkt->key_signatures.size());
        for (const auto signature : pkt->key_signatures) {
            QueryTable::SignatureRoute tracked;
            tracked.signature = signature;
            std::vector<uint32_t> valid_matches;
            for (const auto neighbor : sync_neighbors) {
                const auto neighbor_bf = st_.getNeighborBloomFilter(neighbor);
                if (neighbor_bf.has_value() &&
                    RequestPacket::signatureMatchesBloom(signature, *neighbor_bf)) {
                    tracked.matched_neighbors.push_back(neighbor);
                    if (neighbor != pkt->src_node_id) {
                        valid_matches.push_back(neighbor);
                    }
                }
            }

            const bool immediate_fallback = valid_matches.empty();
            const auto& destinations = immediate_fallback ? physical_neighbors : valid_matches;
            for (const auto neighbor : destinations) {
                if (neighbor == pkt->src_node_id) continue;
                by_egress[neighbor].push_back(signature);
                tracked.sent_neighbors.push_back(neighbor);
            }
            for (const auto neighbor : physical_neighbors) {
                if (neighbor == pkt->src_node_id ||
                    std::find(tracked.sent_neighbors.begin(), tracked.sent_neighbors.end(),
                              neighbor) != tracked.sent_neighbors.end()) {
                    continue;
                }
                tracked.remaining_neighbors.push_back(neighbor);
            }
            tracked_routes.push_back(std::move(tracked));
            if (immediate_fallback) {
                rsync_trace::record(id_, "immediate_dead_end_fallback",
                                    {{"request_id", std::to_string(pkt->request_id)},
                                     {"src", std::to_string(pkt->src_node_id)},
                                     {"phase", "preferred"},
                                     {"reason", "no-valid-matched-neighbor"},
                                     {"signature_count", "1"}});
            }
        }

        qt_.trackPreferred(pkt->request_id, pkt->src_node_id, pkt->bf,
                           std::move(tracked_routes), pkt->hop_count,
                           config_.query_timeout);

        for (auto& [dest, signatures] : by_egress) {
            signatures = RequestPacket::canonicalizeSignatures(signatures);
            const auto subset_bf = RequestPacket::bloomForSignatures(signatures);
            rsync_trace::record(id_, "query_route_selected",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"src", std::to_string(pkt->src_node_id)},
                                 {"dest", std::to_string(dest)},
                                 {"phase", "preferred"},
                                 {"neighbors", std::to_string(dest)},
                                 {"signature_count", std::to_string(signatures.size())}});
            if (pkt->is_aggregated) {
                auto forwarded = buildForwardedRequest(pkt, dest);
                forwarded->bf = subset_bf;
                forwarded->key_signatures = signatures;
                rsync_trace::record(id_, "request_forward_start",
                                    {{"request_id", std::to_string(forwarded->request_id)},
                                     {"src", std::to_string(id_)},
                                     {"dest", std::to_string(dest)},
                                     {"packet_type", "REQUEST"},
                                     {"hop_count", std::to_string(forwarded->hop_count)},
                                     {"via", "signature-route"},
                                     {"phase", "preferred"},
                                     {"signature_count", std::to_string(signatures.size())}});
                sendDirect(std::move(forwarded), dest);
            } else {
                union_window_.stage(dest, subset_bf, pkt->hop_count + 1, signatures);
            }
        }
        return;
    }

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
            auto forwarded = buildForwardedRequest(pkt, dest);
            rsync_trace::record(id_, "request_forward_start",
                                {{"request_id", std::to_string(forwarded->request_id)},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(dest)},
                                 {"packet_type", "REQUEST"},
                                 {"hop_count", std::to_string(forwarded->hop_count)},
                                 {"via", path_label}});
            sendDirect(std::move(forwarded), dest);
            rsync_trace::record(id_, "request_forward_done",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(dest)},
                                 {"packet_type", "REQUEST"},
                                 {"via", path_label}});
            obsLog(id_, "request_forward",
                   {{"request_id", std::to_string(pkt->request_id)},
                    {"dest", std::to_string(dest)},
                    {"key_count", "aggregate"},
                    {"via", path_label}});
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
    rsync_trace::record(id_, "data_handle_start",
                        {{"request_id", std::to_string(pkt->request_id)},
                         {"key", pkt->key},
                         {"src", std::to_string(pkt->src_node_id)},
                         {"dest", std::to_string(pkt->dest_node_id)},
                         {"packet_type", packetTypeName(pkt->type)},
                         {"hop_count", std::to_string(pkt->hop_count)},
                         {"value_bytes", std::to_string(pkt->value.size())}});
    obsLog(id_, "data_receive",
           {{"key", pkt->key},
            {"request_id", std::to_string(pkt->request_id)},
            {"src", std::to_string(pkt->src_node_id)}});

    // Query state must see DATA before cache-based duplicate suppression. In
    // particular, a later path may be the first one that still has a live
    // reverse-path obligation.
    if (QueryTable::isStableUid(pkt->request_id)) {
        const auto signature = RequestPacket::signatureForKey(pkt->key);
        const auto route = qt_.routeFallbackData(pkt->request_id, pkt->key,
                                                 signature, pkt->src_node_id);
        if (!route.query_known || !route.key_expected) {
            obsLog(id_, "data_drop",
                   {{"key", pkt->key},
                    {"request_id", std::to_string(pkt->request_id)},
                    {"reason", "unexpected_fallback_data"}});
            return;
        }

        const bool already_cached = hasLocalData(pkt->key);
        if (!already_cached) {
            cache_->put(pkt->key, pkt->value);
            local_files_.insert(pkt->key);
            if (pkt->src_node_id != 0) setSyncTable(pkt->src_node_id, pkt->key);
        }

        if (route.duplicate_suppressed) {
            rsync_trace::record(id_, "duplicate_request_suppressed",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"key", pkt->key},
                                 {"src", std::to_string(pkt->src_node_id)},
                                 {"phase", "fallback"},
                                 {"reason", route.wrong_direction_suppressed
                                                ? "data-from-current-parent"
                                                : "duplicate-data"},
                                 {"fallback", "1"}});
            return;
        }

        rsync_trace::record(id_, "query_key_satisfied",
                            {{"request_id", std::to_string(pkt->request_id)},
                             {"key", pkt->key},
                             {"src", std::to_string(pkt->src_node_id)},
                             {"phase", "fallback"},
                             {"fallback", "1"}});

        if (route.deliver_to_origin) {
            rsync_trace::record(id_, "app_receive",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"key", pkt->key},
                                 {"src", std::to_string(pkt->src_node_id)},
                                 {"dest", std::to_string(id_)},
                                 {"packet_type", "DATA"},
                                 {"via", "fallback"},
                                 {"value_bytes", std::to_string(pkt->value.size())},
                                 {"phase", "fallback"}});
            obsLog(id_, "app_receive",
                   {{"key", pkt->key},
                    {"request_id", std::to_string(pkt->request_id)},
                    {"source", "fallback"}});
            deliverApplication(pkt->key, pkt->value, pkt->request_id,
                               "fallback");
            if (route.query_complete) {
                rsync_trace::record(id_, "query_complete",
                                    {{"request_id", std::to_string(pkt->request_id)},
                                     {"src", std::to_string(id_)},
                                     {"phase", "fallback"},
                                     {"fallback", "1"}});
            }
            return;
        }

        if (route.parent_node.has_value() &&
            *route.parent_node != pkt->src_node_id) {
            const auto parent = *route.parent_node;
            auto data_pkt = DataPacket::create(
                pkt->request_id, id_, parent, pkt->key, Value(pkt->value.data()));
            rsync_trace::record(id_, "data_forward_start",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"key", pkt->key},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(parent)},
                                 {"packet_type", "DATA"},
                                 {"via", "fallback-reverse-path"},
                                 {"phase", "fallback"},
                                 {"value_bytes", std::to_string(data_pkt->value.size())}});
            sendDirect(std::move(data_pkt), parent);
            rsync_trace::record(id_, "data_forward_done",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"key", pkt->key},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(parent)},
                                 {"packet_type", "DATA"},
                                 {"via", "fallback-reverse-path"},
                                 {"phase", "fallback"}});
        }
        return;
    }

    const auto satisfactions = config_.staged_fallback_enabled
        ? qt_.markDataSatisfiedByKey(pkt->key)
        : std::vector<std::pair<QueryTable::QueryUid,
                                QueryTable::KeySatisfaction>>{};
    bool newly_satisfied_origin = false;
    for (const auto& [uid, satisfaction] : satisfactions) {
        if (!satisfaction.newly_satisfied) continue;
        newly_satisfied_origin = true;
        rsync_trace::record(id_, "query_key_satisfied",
                            {{"request_id", std::to_string(uid)},
                             {"key", pkt->key},
                             {"src", std::to_string(pkt->src_node_id)},
                             {"phase", "preferred"},
                             {"fallback", "0"}});
        if (satisfaction.query_complete) {
            rsync_trace::record(id_, "query_complete",
                                {{"request_id", std::to_string(uid)},
                                 {"src", std::to_string(id_)},
                                 {"phase", "preferred"},
                                 {"fallback", "0"}});
        }
    }

    // Consume reverse-path obligations before cache duplicate suppression.
    // takePending() makes this one-shot in staged mode, avoiding replay storms
    // from stale Bloom entries while preserving legacy get() semantics.
    const auto downstream = getQueryTable(pkt->key);
    const bool has_effective_downstream = std::any_of(
        downstream.begin(), downstream.end(),
        [pkt](uint32_t node_id) { return node_id != pkt->src_node_id; });

    const bool already_cached = hasLocalData(pkt->key);
    if (!already_cached) {
        cache_->put(pkt->key, pkt->value);
        local_files_.insert(pkt->key);
        if (pkt->src_node_id != 0) setSyncTable(pkt->src_node_id, pkt->key);
    } else if (!newly_satisfied_origin && !has_effective_downstream) {
        obsLog(id_, "data_drop",
               {{"key", pkt->key},
                {"request_id", std::to_string(pkt->request_id)},
                {"reason", "local_cache"}});
        return;
    }

    bool application_delivered = false;
    for (const auto node_id : downstream) {
        if (node_id == pkt->src_node_id) continue;

        if (node_id == id_) {
            if (application_delivered ||
                (config_.staged_fallback_enabled && !newly_satisfied_origin)) {
                continue;
            }
            application_delivered = true;
            std::cout << "[Node " << id_ << "] Received DATA for key="
                      << pkt->key << "\n";
            rsync_trace::record(id_, "app_receive",
                                {{"request_id", std::to_string(pkt->request_id)},
                                 {"key", pkt->key},
                                 {"src", std::to_string(pkt->src_node_id)},
                                 {"dest", std::to_string(id_)},
                                 {"packet_type", "DATA"},
                                 {"via", "data"},
                                 {"value_bytes", std::to_string(pkt->value.size())},
                                 {"phase", "preferred"}});
            obsLog(id_, "app_receive",
                   {{"key", pkt->key},
                    {"request_id", std::to_string(pkt->request_id)},
                    {"source", "data"}});
            deliverApplication(pkt->key, pkt->value, pkt->request_id, "data");
            continue;
        }

        auto data_pkt = DataPacket::create(
            pkt->request_id, id_, node_id, pkt->key, Value(pkt->value.data()));
        rsync_trace::record(id_, "data_forward_start",
                            {{"request_id", std::to_string(pkt->request_id)},
                             {"key", pkt->key},
                             {"src", std::to_string(id_)},
                             {"dest", std::to_string(node_id)},
                             {"packet_type", "DATA"},
                             {"value_bytes", std::to_string(data_pkt->value.size())}});
        sendDirect(std::move(data_pkt), node_id);
        rsync_trace::record(id_, "data_forward_done",
                            {{"request_id", std::to_string(pkt->request_id)},
                             {"key", pkt->key},
                             {"src", std::to_string(id_)},
                             {"dest", std::to_string(node_id)},
                             {"packet_type", "DATA"}});
        obsLog(id_, "data_forward",
               {{"key", pkt->key},
                {"request_id", std::to_string(pkt->request_id)},
                {"dest", std::to_string(node_id)}});
    }
}

// ===== Original code (commented out, SYNC packet is deprecated) =====
// handle sync packet
// void Node::handleSync(std::unique_ptr<Packet> pkt) {
//     // TODO: update the sync table
//     // ...
// }
// ===== End of original code =====


bool Node::flushUnionWindows() {
    auto entries = union_window_.drain();
    for (auto& e : entries) {
        auto out = std::make_unique<RequestPacket>();
        out->request_id = generateRequestId();  // new id for the aggregate
        out->src_node_id = id_;                  // aggregator = prev-hop for next node
        out->dest_node_id = e.egress_node_id;
        out->bf = std::move(e.merged_bf);
        out->key_signatures = std::move(e.key_signatures);
        out->hop_count = e.max_hop_count;        // already +1 at stage time
        out->is_aggregated = true;

        std::cout << "[Node " << id_ << "] flush AGGREGATE to Node " << e.egress_node_id
                  << " (count=" << e.count << ", hop=" << e.max_hop_count << ")\n";
        rsync_trace::record(id_, "aggregate_flush",
                            {{"request_id", std::to_string(out->request_id)},
                             {"dest", std::to_string(e.egress_node_id)},
                             {"packet_type", "REQUEST"},
                             {"hop_count", std::to_string(e.max_hop_count)},
                             {"value_bytes", std::to_string(e.count)}});
        obsLog(id_, "aggregate_flush",
               {{"request_id", std::to_string(out->request_id)},
                {"dest", std::to_string(e.egress_node_id)},
                {"count", std::to_string(e.count)},
                {"hop", std::to_string(e.max_hop_count)}});
        rsync_trace::record(id_, "request_forward_start",
                            {{"request_id", std::to_string(out->request_id)},
                             {"src", std::to_string(id_)},
                             {"dest", std::to_string(e.egress_node_id)},
                             {"packet_type", "REQUEST"},
                             {"hop_count", std::to_string(e.max_hop_count)},
                             {"via", "aggregate_flush"}});
        const auto request_id = out->request_id;
        sendDirect(std::move(out), e.egress_node_id);
        rsync_trace::record(id_, "request_forward_done",
                            {{"request_id", std::to_string(request_id)},
                             {"src", std::to_string(id_)},
                             {"dest", std::to_string(e.egress_node_id)},
                             {"packet_type", "REQUEST"},
                             {"via", "aggregate_flush"}});
    }
    return !entries.empty();
}

bool Node::flushDueFallbacks() {
    if (!config_.staged_fallback_enabled) return false;

    const auto due = qt_.takeDueOriginFallbacks();
    bool activity = !due.empty();
    for (const auto& item : due) {
        std::vector<RequestPacket::KeySignature> signatures;
        std::vector<std::string> keys;
        signatures.reserve(item.unresolved.size());
        keys.reserve(item.unresolved.size());
        for (const auto& unresolved : item.unresolved) {
            signatures.push_back(unresolved.signature);
            keys.push_back(unresolved.key);
        }
        signatures = RequestPacket::canonicalizeSignatures(std::move(signatures));
        if (signatures.empty()) continue;

        rsync_trace::record(id_, "origin_fallback_triggered",
                            {{"request_id", std::to_string(item.uid)},
                             {"key", joinKeys(keys)},
                             {"src", std::to_string(id_)},
                             {"phase", "fallback"},
                             {"reason", "origin-deadline"},
                             {"signature_count", std::to_string(signatures.size())},
                             {"deadline_ms", std::to_string(config_.fallback_delay.count())},
                             {"fallback", "1"}});

        auto neighbors = network_->get_neighbors();
        std::sort(neighbors.begin(), neighbors.end());
        neighbors.erase(std::unique(neighbors.begin(), neighbors.end()),
                        neighbors.end());
        for (const auto dest : neighbors) {
            auto out = std::make_unique<RequestPacket>();
            out->request_id = item.uid;
            out->src_node_id = id_;
            out->dest_node_id = dest;
            out->bf = RequestPacket::bloomForSignatures(signatures);
            out->hop_count = 0;
            out->is_aggregated = false;
            out->is_fallback = true;
            out->key_signatures = signatures;
            const auto frame_bytes =
                signedRequestTcpFrameBytes(out->key_signatures.size());
            rsync_trace::record(id_, "fallback_forward",
                                {{"request_id", std::to_string(item.uid)},
                                 {"key", joinKeys(keys)},
                                 {"src", std::to_string(id_)},
                                 {"dest", std::to_string(dest)},
                                 {"packet_type", "REQUEST"},
                                 {"hop_count", "0"},
                                 {"phase", "fallback"},
                                 {"signature_count", std::to_string(signatures.size())},
                                 {"frame_bytes", std::to_string(frame_bytes)},
                                 {"fallback", "1"}});
            sendDirect(std::move(out), dest);
        }
    }
    return activity;
}

// Drain all pending packets, then flush staged unions.
// "Batch" = inbox snapshot at entry time; aggregation scope is one processOne() call.
bool Node::processOne() {
    bool activity = false;
    while (auto pkt = network_->receive()) {
        activity = true;
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
    const bool union_activity = flushUnionWindows();
    bool expiry_activity = false;
    if (config_.staged_fallback_enabled) {
        const auto expired = qt_.expire();
        if (expired.origin_unsatisfied > 0) {
            expiry_activity = true;
            rsync_trace::record(id_, "query_expired_unsatisfied",
                                {{"src", std::to_string(id_)},
                                 {"phase", "fallback"},
                                 {"reason", "query-lifetime-expired"},
                                 {"value_bytes", std::to_string(expired.origin_unsatisfied)},
                                 {"fallback", "1"}});
        }
    }
    const bool fallback_activity = flushDueFallbacks();
    return fallback_activity || union_activity || expiry_activity || activity;
}
