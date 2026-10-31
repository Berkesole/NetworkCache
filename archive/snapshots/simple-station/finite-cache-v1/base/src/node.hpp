#pragma once
#include <memory>
#include <queue>
#include <string>
#include <iostream>
#include <vector>
#include <unordered_set>
#include <functional>
#include <chrono>
#include "packet.hpp"
#include "sync_table.hpp"
#include "query_table.hpp"
#include "union_window.hpp"
#include "bloomfilter.hpp"
#include "cache/cache.hpp"
#include "cache/node_cache.hpp"
#include "network.hpp"

class Node {
public:
    struct ApplicationDelivery {
        std::string key;
        uint64_t request_id = 0;
        std::string via;
        Value value;
        std::string app_request_id;
        uint64_t origin_query_uid = 0;
    };
    using ApplicationCallback = std::function<void(const ApplicationDelivery&)>;
    struct Config {
        uint32_t max_hop_count;
        std::chrono::milliseconds query_timeout;
        std::chrono::milliseconds fallback_delay;
        size_t bloom_bits;
        size_t bloom_hash_count;
        bool staged_fallback_enabled;
        // Cache policy for this node. Default is Legacy: one flat key space,
        // no byte budget, no LRU, no cache lifecycle events, i.e. exactly the
        // behaviour the frozen capacity/slides campaigns ran.
        rsync_cache::CachePolicyConfig cache_policy;
        Config()
            : max_hop_count(6), query_timeout(500), fallback_delay(1500),
              bloom_bits(1024), bloom_hash_count(1), staged_fallback_enabled(false) {}
    };

    Node(uint32_t id, std::unique_ptr<NetworkInterface> network = nullptr,
         std::unique_ptr<LocalCache> cache = nullptr,
         Config config = {});

    // External call: application initiates a file request
    void requestFile(const std::vector<std::string>& keys);
    // Single-event-loop application API. Does not coalesce or alter routing.
    void requestFileAsync(const std::string& key, const std::string& app_request_id);
    void requestFileFromProvider(const std::string& key, uint32_t provider_node_id);
    // Returns true when this call received or emitted protocol traffic. The
    // latency-v3 harness uses this only for a post-completion quiescence gate.
    bool processOne();
    void addLocalData(const std::string& key, const std::string& value);
    void addLocalData(const std::string& key, const Value& value);
    void setApplicationCallback(ApplicationCallback callback);
    // Test/experiment observability only; it does not influence forwarding.
    std::optional<uint32_t> debugCachedRoute(const std::string& key);
    std::vector<SyncTable::BloomOccupancy> debugSyncBloomOccupancy() const;
    // Cache store accessor: emits cache_config / cache_gauge at startup, and
    // lets tests install a recording event sink.
    rsync_cache::NodeCache* cacheStore() { return store_.get(); }
    const rsync_cache::NodeCache* cacheStore() const { return store_.get(); }

private:
    void handleRequest(RequestPacket* pkt);
    void handleData(DataPacket* pkt);

    // Build a REQUEST forwarded from the incoming pkt: same request_id/BF,
    // src rewritten to self, dest set, hop_count incremented.
    std::unique_ptr<RequestPacket> buildForwardedRequest(const RequestPacket* pkt, uint32_t dest_node_id);

    // Drain staged union slots and emit one aggregated REQUEST per egress.
    // Called at the end of each processOne() batch (spec §3.4.1 (e) + §3.5).
    bool flushUnionWindows();
    // Emit origin-triggered phase-upgrade REQUESTs whose exact-key deadline
    // has elapsed. Called after inbound DATA has been processed.
    bool flushDueFallbacks();

    // Network Layer Interface
    void sendDirect(std::unique_ptr<Packet> pkt, uint32_t dest_node_id);
    void sendBroadcast(std::unique_ptr<Packet> pkt);

    // Local Cache Interface
    rsync_cache::LookupResult lookupLocal(const std::string& key);
    bool hasLocalData(const std::string& key);
    rsync_cache::CacheEventContext dataEventContext(const DataPacket* pkt,
                                                    const char* phase) const;
    // Enforces immutable-object integrity for one incoming DATA packet. Throws
    // rsync_cache::ImmutabilityViolation on a conflict (after the store emitted
    // exactly one cache_key_conflict). Returns false when the local reference
    // could not be read: the caller must drop the packet without delivering,
    // forwarding or caching it. Must be called before any protocol state is
    // consumed.
    bool verifyIncomingData(const DataPacket* pkt, const char* phase);
    void deliverApplication(const std::string& key, const Value& value,
                            uint64_t request_id, const std::string& via,
                            const std::string& app_request_id = {}, uint64_t origin_uid = 0);
    void completeOriginApplication(uint64_t uid, const std::string& key,
                                   const Value& value, uint64_t wire_id, const std::string& via);

    // QueryTable
    void setQueryTable(uint32_t node_id, const BloomFilter& bf);
    std::vector<uint32_t> getQueryTable(const std::string& key);
    
    // SyncTable
    void setSyncTable(uint32_t node_id, const std::string& key);
    std::optional<uint32_t> searchSyncTable(const std::string& filename);
    std::vector<uint32_t> searchSyncTableAllRoutes(const std::string& key);
    std::vector<uint32_t> selectNextHopsBySyncTable(const BloomFilter& request_bf);

    uint32_t id_;
    Config config_;

    QueryTable qt_;
    SyncTable st_;
    UnionWindow union_window_;
    std::unique_ptr<NetworkInterface> network_;
    // Owner index + replica LRU + serviceable-key directory. The directory
    // replaces the old insert-only local_files_ set, so replica eviction
    // removes its entry and owner entries are never removed.
    std::unique_ptr<rsync_cache::NodeCache> store_;
    std::unique_ptr<rsync_cache::CacheEventSink> trace_sink_;
    ApplicationCallback application_callback_;
    uint64_t next_origin_query_sequence_ = 1;
    std::optional<std::string> registering_app_id_;
    std::unordered_map<uint64_t, std::string> origin_app_ids_;
    std::unordered_set<std::string> seen_app_ids_;
};
