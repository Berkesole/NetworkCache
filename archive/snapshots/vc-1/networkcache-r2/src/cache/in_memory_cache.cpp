#include "in_memory_cache.hpp"

void InMemoryCache::put(const std::string& key, const Value& value) {
    store_[key] = value.data();
}

std::optional<Value> InMemoryCache::get(const std::string& key) const {
    auto it = store_.find(key);
    if (it == store_.end()) return std::nullopt;
    return Value(it->second);
}

void InMemoryCache::del(const std::string& key) {
    store_.erase(key);
}

bool InMemoryCache::exists(const std::string& key) const {
    return store_.find(key) != store_.end();
}

void InMemoryCache::flush() {
    store_.clear();
}
