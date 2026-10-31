#pragma once
#include <algorithm>
#include <unordered_map>
#include <vector>
#include <string>
#include <optional>
#include <stdexcept>
#include <chrono>
#include "bloomfilter.hpp"

// 1. Next Hops: Each data may exist on multiple neighbor nodes (multi-path)
// 2. Cost/Priority: Each next hop has cost metrics (RTT, load, etc.)
// 3. Strategy Selection: Supports different forwarding strategies (best, load balance, multicast, etc.)
// 4. Dynamic Update: Update neighbor data status through SYNC messages
class SyncTable {
public:
    struct BloomOccupancy {
        uint32_t neighbor_id;
        size_t bits_set;
        size_t bit_count;
    };
    // Forwarding strategy enumeration
    enum class ForwardingStrategy {
        BEST_ROUTE,      
        MULTICAST,      
        RANDOM,          
        ROUND_ROBIN,     
        LOAD_BALANCE     
    };

    struct Config {
        std::chrono::seconds sync_ttl{300};              // Bloom Filter sync period
        std::chrono::seconds neighbor_timeout{600};      // Neighbor timeout
        size_t max_neighbors{100};                       // Maximum number of neighbors
        ForwardingStrategy default_strategy{ForwardingStrategy::BEST_ROUTE};
        bool enable_cost_tracking{true};                 // Enable cost tracking
        bool enable_prefix_match{false};                 // Enable prefix matching (future extension)
        size_t bloom_bits{1024};
        size_t bloom_hash_count{1};
    };

    // Next Hop: Core concept in FIB, represents a possible forwarding target
    struct NextHop {
        uint32_t node_id;                                // Neighbor node ID
        uint32_t cost{1};                                // Forwarding cost (can be RTT, hop count, etc.)
        uint32_t priority{0};                            // Priority (0 is highest)
        std::chrono::steady_clock::time_point last_update; // Last update time
        uint32_t success_count{0};                       // Successful forwarding count
        uint32_t failure_count{0};                       // Failure count (used for cost calculation)
        
        NextHop(uint32_t id, uint32_t c = 1, uint32_t p = 0)
            : node_id(id), cost(c), priority(p), 
              last_update(std::chrono::steady_clock::now()) {}
        
        // Calculate effective cost (combined with failure rate)
        double getEffectiveCost() const {
            if (success_count + failure_count == 0) return static_cast<double>(cost);
            double failure_rate = static_cast<double>(failure_count) / 
                                 (success_count + failure_count);
            return cost * (1.0 + failure_rate * 10.0); // Failure rate significantly increases cost
        }
        
        // Comparison operator (for sorting)
        bool operator<(const NextHop& other) const {
            if (priority != other.priority) return priority < other.priority;
            return getEffectiveCost() < other.getEffectiveCost();
        }
    };

    // FIB Entry: Records information about a neighbor node
    // Note: node_id is stored in the map key, no need to duplicate in value
    struct FibEntry {
        BloomFilter bloom_filter;                        // Bloom Filter of data this node has
        std::vector<NextHop> next_hops;                  // Paths to reach this node (usually direct connection)
        std::chrono::steady_clock::time_point last_sync; // Last sync time
        uint32_t sync_version{0};                        // Sync version number
        uint32_t access_count{0};                        // Access statistics
        
        FibEntry() : last_sync(std::chrono::steady_clock::now()) {}
        
        // Check if timed out
        bool isStale(std::chrono::seconds timeout) const {
            auto now = std::chrono::steady_clock::now();
            return (now - last_sync) > timeout;
        }
        
        // Update Bloom Filter
        void updateBloomFilter(const BloomFilter& bf) {
            bloom_filter = bf;
            last_sync = std::chrono::steady_clock::now();
            sync_version++;
        }
        
        // Get best next hop
        std::optional<NextHop> getBestNextHop() const {
            if (next_hops.empty()) return std::nullopt;
            return *std::min_element(next_hops.begin(), next_hops.end());
        }
    };

    explicit SyncTable(const Config& cfg) : cfg_(cfg) {}
    explicit SyncTable() : cfg_() {}
    ~SyncTable() = default;

    SyncTable(const SyncTable&) = delete;
    SyncTable& operator=(const SyncTable&) = delete;
    SyncTable(SyncTable&&) = default;
    SyncTable& operator=(SyncTable&&) = default;
    
    // Incremental update: record that a node has a specified filename
    void set(uint32_t node_id, const std::string& key) {
        // Ensure neighbor exists
        if (!fib_entries_.contains(node_id)) {
            fib_entries_[node_id] = FibEntry();
            fib_entries_[node_id].bloom_filter = BloomFilter(cfg_.bloom_bits, cfg_.bloom_hash_count);
            fib_entries_[node_id].next_hops.emplace_back(node_id, /*cost=*/1, /*priority=*/0);
        }
        // Add filename to this neighbor's Bloom Filter
        fib_entries_[node_id].bloom_filter.add(key);
        fib_entries_[node_id].last_sync = std::chrono::steady_clock::now();
        fib_entries_[node_id].access_count = 0;
    }
    
    // Update neighbor's Bloom Filter
    void updateNeighbor(uint32_t node_id, const BloomFilter& bf, uint32_t cost = 1) {
        if (bf.getBits().size() != cfg_.bloom_bits ||
            bf.getHashCount() != cfg_.bloom_hash_count) {
            throw std::invalid_argument("SyncTable: incompatible Bloom parameters");
        }
        if (!fib_entries_.contains(node_id)) {
            fib_entries_[node_id] = FibEntry();
            fib_entries_[node_id].bloom_filter = BloomFilter(cfg_.bloom_bits, cfg_.bloom_hash_count);
            // Add direct connection next hop
            fib_entries_[node_id].next_hops.emplace_back(node_id, cost, 0);
        }
        fib_entries_[node_id].updateBloomFilter(bf);
        fib_entries_[node_id].access_count = 0;
    }
    
    // Add neighbor node
    void addNeighbor(uint32_t node_id, uint32_t cost = 1, uint32_t priority = 0) {
        if (!fib_entries_.contains(node_id)) {
            fib_entries_[node_id] = FibEntry();
            fib_entries_[node_id].bloom_filter = BloomFilter(cfg_.bloom_bits, cfg_.bloom_hash_count);
        }
        // Check if this next hop already exists
        auto& hops = fib_entries_[node_id].next_hops;
        auto it = std::find_if(hops.begin(), hops.end(),
                              [node_id](const NextHop& h) { return h.node_id == node_id; });
        if (it == hops.end()) {
            hops.emplace_back(node_id, cost, priority);
        }
    }
    
    // Remove neighbor node
    void removeNeighbor(uint32_t node_id) {
        fib_entries_.erase(node_id);
    }
    
    // Update next hop cost
    void updateCost(uint32_t node_id, uint32_t new_cost) {
        if (fib_entries_.contains(node_id)) {
            for (auto& hop : fib_entries_[node_id].next_hops) {
                if (hop.node_id == node_id) {
                    hop.cost = new_cost;
                    hop.last_update = std::chrono::steady_clock::now();
                    break;
                }
            }
        }
    }
    
    // Record successful forwarding (for adaptive cost adjustment)
    void recordSuccess(uint32_t node_id) {
        if (fib_entries_.contains(node_id)) {
            for (auto& hop : fib_entries_[node_id].next_hops) {
                if (hop.node_id == node_id) {
                    hop.success_count++;
                    break;
                }
            }
        }
    }
    
    // Record forwarding failure
    void recordFailure(uint32_t node_id) {
        if (fib_entries_.contains(node_id)) {
            for (auto& hop : fib_entries_[node_id].next_hops) {
                if (hop.node_id == node_id) {
                    hop.failure_count++;
                    break;
                }
            }
        }
    }

    // ============ Query Operations ============
    // Find forwarding targets based on strategy
    std::vector<uint32_t> lookup(const std::string& key, 
                                   ForwardingStrategy strategy = ForwardingStrategy::BEST_ROUTE) {
        std::vector<NextHop> candidates;
        
        // 1. Find all neighbors that may have this data through BF
        for (auto& [node_id, entry] : fib_entries_) {
            if (entry.bloom_filter.contains(key)) {
                auto best_hop = entry.getBestNextHop();
                if (best_hop.has_value()) {
                    candidates.push_back(*best_hop);
                    entry.access_count++;
                }
            }
        }
        
        if (candidates.empty()) {
            return {};
        }
        
        // 2. Select forwarding targets based on strategy
        return selectNextHops(candidates, strategy);
    }
    
    // Find single best forwarding target
    std::optional<uint32_t> lookupBestRoute(const std::string& key) {
        auto results = lookup(key, ForwardingStrategy::BEST_ROUTE);
        if (!results.empty()) {
            return results[0];
        }
        return std::nullopt;
    }
    
    // Find all possible forwarding targets (for multicast)
    std::vector<uint32_t> lookupAllRoutes(const std::string& key) {
        return lookup(key, ForwardingStrategy::MULTICAST);
    }
    
    // Get all neighbors
    std::vector<uint32_t> getAllNeighbors() const {
        std::vector<uint32_t> neighbors;
        for (const auto& [node_id, _] : fib_entries_) {
            neighbors.push_back(node_id);
        }
        return neighbors;
    }
    
    // Check if neighbor exists
    bool hasNeighbor(uint32_t node_id) const {
        return fib_entries_.contains(node_id);
    }
    
    // Get neighbor's Bloom Filter
    std::optional<BloomFilter> getNeighborBloomFilter(uint32_t node_id) {
        if (fib_entries_.contains(node_id)) {
            return fib_entries_[node_id].bloom_filter;
        }
        return std::nullopt;
    }

    // ============ Maintenance Operations ============
    
    // Clean up stale neighbor information
    size_t cleanStaleEntries() {
        size_t removed = 0;
        for (auto it = fib_entries_.begin(); it != fib_entries_.end();) {
            if (it->second.isStale(cfg_.neighbor_timeout)) {
                it = fib_entries_.erase(it);
                ++removed;
            } else {
                ++it;
            }
        }
        return removed;
    }
    
    // Set default forwarding strategy
    void setDefaultStrategy(ForwardingStrategy strategy) {
        cfg_.default_strategy = strategy;
    }

    // ============ Statistics ============
    
    size_t neighborCount() const {
        return fib_entries_.size();
    }

    std::vector<BloomOccupancy> debugBloomOccupancy() const {
        std::vector<BloomOccupancy> result;
        result.reserve(fib_entries_.size());
        for (const auto& [neighbor_id, entry] : fib_entries_) {
            const auto& bits = entry.bloom_filter.getBits();
            result.push_back({neighbor_id,
                              static_cast<size_t>(std::count(bits.begin(), bits.end(), true)),
                              bits.size()});
        }
        std::sort(result.begin(), result.end(),
                  [](const auto& left, const auto& right) {
                      return left.neighbor_id < right.neighbor_id;
                  });
        return result;
    }
    
    const Config& getConfig() const {
        return cfg_;
    }
    
    // Get statistics
    struct Stats {
        size_t total_neighbors{0};
        size_t total_next_hops{0};
        size_t stale_entries{0};
        double avg_cost{0.0};
    };
    
    Stats getStats() const {
        Stats stats;
        stats.total_neighbors = fib_entries_.size();
        double total_cost = 0.0;
        
        for (const auto& [_, entry] : fib_entries_) {
            stats.total_next_hops += entry.next_hops.size();
            if (entry.isStale(cfg_.neighbor_timeout)) {
                stats.stale_entries++;
            }
            auto best = entry.getBestNextHop();
            if (best.has_value()) {
                total_cost += best->getEffectiveCost();
            }
        }
        
        if (stats.total_neighbors > 0) {
            stats.avg_cost = total_cost / stats.total_neighbors;
        }
        
        return stats;
    }

private:
    Config cfg_;
    
    // FIB table: node_id -> FibEntry
    std::unordered_map<uint32_t, FibEntry> fib_entries_;
    
    // Round-robin counter (for ROUND_ROBIN strategy)
    mutable size_t round_robin_counter_{0};
    
    // Select next hops based on strategy
    std::vector<uint32_t> selectNextHops(std::vector<NextHop>& candidates,
                                          ForwardingStrategy strategy) {
        if (candidates.empty()) return {};
        
        switch (strategy) {
            case ForwardingStrategy::BEST_ROUTE:
                // Select the one with lowest cost
                std::sort(candidates.begin(), candidates.end());
                return {candidates[0].node_id};
                
            case ForwardingStrategy::MULTICAST:
                // Return all candidates
                {
                    std::vector<uint32_t> result;
                    for (const auto& hop : candidates) {
                        result.push_back(hop.node_id);
                    }
                    return result;
                }
                
            case ForwardingStrategy::RANDOM:
                // Randomly select one
                {
                    size_t idx = std::rand() % candidates.size();
                    return {candidates[idx].node_id};
                }
                
            case ForwardingStrategy::ROUND_ROBIN:
                // Round-robin selection
                {
                    size_t idx = round_robin_counter_++ % candidates.size();
                    return {candidates[idx].node_id};
                }
                
            case ForwardingStrategy::LOAD_BALANCE:
                // Simple load balancing: select highest success rate
                std::sort(candidates.begin(), candidates.end(),
                         [](const NextHop& a, const NextHop& b) {
                             return a.getEffectiveCost() < b.getEffectiveCost();
                         });
                // Randomly select from the top few low-cost candidates
                {
                    size_t pool_size = std::min(size_t(3), candidates.size());
                    size_t idx = std::rand() % pool_size;
                    return {candidates[idx].node_id};
                }
                
            default:
                return {candidates[0].node_id};
        }
    }
    

};
