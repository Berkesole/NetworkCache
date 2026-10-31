#pragma once

#include "cache.hpp"
#include "cache_events.hpp"
#include "key_namespace.hpp"

#include <cstddef>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>

namespace rsync_cache {

// Owner objects: written exactly once during initialization, read-only
// afterwards. Owner objects are never subject to the replica byte budget and
// are never evicted.
//
// The owner index is kept in memory so
//   * tier classification is O(1) and never needs a backend round trip, and
//   * "this node already owns the key, do not create a redundant replica"
//     (design requirement A.7) is answered without touching storage.
class OwnerStore {
public:
    OwnerStore(LocalCache* backend, bool namespaced, CacheEventSink* sink = nullptr);

    // Initialization only. Throws std::logic_error after seal(), and
    // std::runtime_error when the same logical key is seeded twice with
    // different size or content: the protocol does not allow an object to
    // change, so a conflict must be reported rather than silently accepted.
    void seed(const std::string& logical_key, const Value& value);

    // Ends the initialization phase. Idempotent.
    void seal() { sealed_ = true; }
    bool sealed() const { return sealed_; }

    // Read-only access. Never touches replica recency.
    std::optional<Value> get(const std::string& logical_key) const;
    bool contains(const std::string& logical_key) const {
        return owners_.find(logical_key) != owners_.end();
    }

    std::size_t objectCount() const { return owners_.size(); }
    std::size_t valueBytes() const { return bytes_; }
    // Size recorded at seeding time, without a backend read. nullopt when the
    // key is not an owner object of this node.
    std::optional<std::size_t> sizeOf(const std::string& logical_key) const {
        const auto it = sizes_.find(logical_key);
        if (it == sizes_.end()) {
            return std::nullopt;
        }
        return it->second;
    }
    const std::unordered_set<std::string>& keys() const { return owners_; }
    bool namespaced() const { return namespaced_; }
    bool verifyContent() const { return verify_content_; }
    // Owner seeding is a one-time, low-volume path, so content comparison is
    // on by default and is NOT controlled by RSYNC_CACHE_VERIFY_REPLICA_CONTENT
    // (that variable only affects the finite-cache replica/legacy path). Only
    // exposed for tests.
    void setVerifyContent(bool value) { verify_content_ = value; }
    // False once an indeterminate backend result poisoned this store; seed()
    // and get() then throw CacheUnavailable.
    bool available() const { return !unavailable_; }

    std::string physicalKeyFor(const std::string& logical_key) const {
        return physicalKey(namespaced_ ? CacheNamespace::Owner : CacheNamespace::Legacy,
                           logical_key);
    }

private:
    // Every owner entry point calls this first: once an indeterminate result has
    // poisoned the store, seeding and reading must refuse rather than guess.
    void requireUsable() const;

    LocalCache* backend_;
    bool namespaced_;
    CacheEventSink* sink_;
    bool sealed_ = false;
    bool verify_content_ = true;
    // get() is const, so the poison flag must be mutable there. It only ever
    // moves false -> true.
    mutable bool unavailable_ = false;
    std::unordered_set<std::string> owners_;
    std::unordered_map<std::string, std::size_t> sizes_;
    std::size_t bytes_ = 0;
};

}  // namespace rsync_cache