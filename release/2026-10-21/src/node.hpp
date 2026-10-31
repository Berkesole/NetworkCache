#pragma once
#include <memory>
#include <queue>
#include <string>
#include <iostream>
#include <vector>
#include <unordered_set>
#include "packet.hpp"
#include "sync_table.hpp"
#include "query_table.hpp"
#include "union_window.hpp"
#include "bloomfilter.hpp"
#include "cache/cache.hpp"
#include "network.hpp"

class Node {
public:
    struct Config {
        uint32_t max_hop_count;
        Config() : max_hop_count(6) {}
    };

    Node(uint32_t id, std::unique_ptr<NetworkInterface> network = nullptr,
         std::unique_ptr<LocalCache> cache = nullptr,
         Config config = {});

    // External call: application initiates a file request
    void requestFile(const std::vector<std::string>& keys);
    void processOne();
    void addLocalData(const std::string& key, const std::string& value);

private:
    void handleRequest(RequestPacket* pkt);
    void handleData(DataPacket* pkt);

    // Build a REQUEST forwarded from the incoming pkt: same request_id/BF,
    // src rewritten to self, dest set, hop_count incremented.
    std::unique_ptr<RequestPacket> buildForwardedRequest(const RequestPacket* pkt, uint32_t dest_node_id);

    // Drain staged union slots and emit one aggregated REQUEST per egress.
    // Called at the end of each processOne() batch (spec §3.4.1 (e) + §3.5).
    void flushUnionWindows();

    // Network Layer Interface
    void sendDirect(std::unique_ptr<Packet> pkt, uint32_t dest_node_id);
    void sendBroadcast(std::unique_ptr<Packet> pkt);

    // Local Cache Interface
    std::optional<Value> getLocalData(const std::string& key);
    bool hasLocalData(const std::string& key);

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

    // Temporary implementation: stores all filenames that exist in local cache
    // In the future, this should be abstracted from cache and implement an interface
    // to get all filenames that exist in local cache, for checking against bloom filter
    std::unordered_set<std::string> local_files_; 
    
    QueryTable qt_;
    SyncTable st_;
    UnionWindow union_window_;
    std::unique_ptr<NetworkInterface> network_;
    std::unique_ptr<LocalCache> cache_;
};
